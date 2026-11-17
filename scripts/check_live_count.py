"""Evalúa por separado el ajuste por cuenta con Statcast guardado localmente.

El entrenamiento termina el 31 de diciembre anterior a la temporada evaluada.
Elige de antemano un lanzamiento entre el segundo y el sexto por aparición,
independientemente de su resultado. Evalúa las apariciones que llegaron a ese
lanzamiento con cuenta distinta de 0–0. Compara probabilidades del mismo
bateador con y sin el ajuste, manteniendo fijo el perfil al inicio de temporada.
La muestra representa esas cuentas, con bateadores de la cohorte suministrada.
Para evaluar el modelo completo se requieren también lanzadores y situaciones
de juego observadas en tiempo real.
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from hashlib import sha256
from itertools import product
import json
from pathlib import Path
import sys

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from baseball_analyzer_v2.analytics import HIT_EVENTS, build_analysis
from baseball_analyzer_v2.live_hitter import (
    LOOKBACK_DAYS, MIN_BATTER_PA, RECENT_GAMES, _adjust_for_count, _before_game,
)
from baseball_analyzer_v2.storage import CACHE_ROOT

KEYS = ["game_pk", "at_bat_number"]
COLUMNS = ("game_date", "game_type", *KEYS, "pitch_number", "balls", "strikes", "events",
           "estimated_ba_using_speedangle")
COUNTS = [(balls, strikes) for balls, strikes in product(range(4), range(3)) if balls or strikes]


def _cohort(path: Path) -> list[dict]:
    try:
        players = json.loads(path.read_text(encoding="utf-8"))["players"]
        ids = [player["id"] for player in players]
        if not ids or any(type(pid) is not int or pid <= 0 for pid in ids) or len(set(ids)) != len(ids):
            raise ValueError("Se requieren IDs MLB positivos y únicos.")
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"No se pudo leer el manifiesto {path}. {exc}") from None
    return players


def _regular_history(db: duckdb.DuckDBPyConnection, path: Path) -> pd.DataFrame:
    try:
        schema = db.execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]).fetchall()
        columns = {row[0] for row in schema}
        missing = set(COLUMNS) - columns
        if missing:
            raise ValueError(f"Faltan columnas de Statcast en {path}: {', '.join(sorted(missing))}.")
        return db.execute(f"SELECT {', '.join(COLUMNS)} FROM read_parquet(?) WHERE game_type='R'", [str(path)]).df()
    except duckdb.Error as exc:
        raise ValueError(f"No se pudo leer el historial de Statcast {path}. {exc}") from None


def _target_pitch(game: int, appearance: int) -> int:
    seed = sha256(f"{int(game)}:{int(appearance)}".encode()).digest()
    return 2 + int.from_bytes(seed[:4], "big") % 5


def _observations(data: pd.DataFrame, player: dict, cutoff: date, train_start: date) -> list[tuple]:
    train = _before_game(data, train_start, cutoff - timedelta(days=1))
    test = _before_game(data, cutoff, date(cutoff.year, 12, 31)).dropna(subset=KEYS)
    if train["events"].notna().sum() < MIN_BATTER_PA or test.empty:
        return []

    analysis = build_analysis(train, player, RECENT_GAMES, LOOKBACK_DAYS, today=cutoff)
    base = analysis["per_plate_appearance"]["hits"]["rate"]
    adjustments = {(b, s): _adjust_for_count(train, base, {"balls": b, "strikes": s}) for b, s in COUNTS}

    terminal = test.loc[test["events"].notna(), KEYS + ["events", "game_date"]].drop_duplicates()
    terminal = terminal.drop_duplicates(KEYS, keep=False)
    terminal["hit"] = terminal["events"].isin(HIT_EVENTS).astype(int)
    opened = test.loc[test["balls"].eq(0) & test["strikes"].eq(0), KEYS].drop_duplicates()
    sample = terminal.merge(opened, on=KEYS)
    sample["pitch_number"] = [_target_pitch(game, appearance) for game, appearance in
                              zip(sample["game_pk"], sample["at_bat_number"])]
    visits = test[KEYS + ["pitch_number", "balls", "strikes"]].drop_duplicates()
    valid_counts = visits["balls"].isin(range(4)) & visits["strikes"].isin(range(3))
    later_counts = visits["balls"].ne(0) | visits["strikes"].ne(0)
    visits = visits.loc[valid_counts & later_counts].drop_duplicates(KEYS + ["pitch_number"], keep=False)

    records = []
    for observed in sample.merge(visits, on=KEYS + ["pitch_number"]).itertuples(index=False):
        adjusted, _, _, applied = adjustments[(observed.balls, observed.strikes)]
        records.append((player["id"], observed.game_date, base, adjusted, applied, observed.hit))
    return records


def benchmark(players: list[dict], data_dir: Path, season: int) -> dict:
    paths = [data_dir / "statcast" / f"batter-{player['id']}.parquet" for player in players]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise ValueError(f"Caché de Statcast incompleta. Falta {missing[0]} ({len(missing)} en total).")

    cutoff = date(season, 1, 1)
    train_start = cutoff - timedelta(days=LOOKBACK_DAYS)
    records = []
    with duckdb.connect() as db:
        for player, path in zip(players, paths):
            records.extend(_observations(_regular_history(db, path), player, cutoff, train_start))
    if not records:
        raise ValueError(f"No hay apariciones evaluables en la temporada regular {season} para este manifiesto.")

    results = pd.DataFrame(records, columns=["player", "date", "base", "adjusted", "applied", "hit"])
    actual = results["hit"].to_numpy()
    summary = {
        "season": season, "training_from": train_start.isoformat(), "training_until_exclusive": cutoff.isoformat(),
        "cohort_players": len(players), "players": int(results["player"].nunique()), "n": len(results),
        "applied": int(results["applied"].sum()), "dates": int(results["date"].nunique()),
        "observed": float(actual.mean()),
    }
    for key in ("base", "adjusted"):
        probability = np.clip(results[key].to_numpy(), 1e-6, 1 - 1e-6)
        summary[key] = {
            "brier": float(np.mean((actual - probability) ** 2)),
            "logloss": float(np.mean(-actual * np.log(probability) - (1 - actual) * np.log1p(-probability))),
            "mean": float(probability.mean()),
        }
    results["delta"] = (results["hit"] - results["adjusted"]) ** 2 - (results["hit"] - results["base"]) ** 2
    by_day = results.groupby("date")["delta"].agg(["sum", "size"]).to_numpy()
    selected_days = np.random.default_rng(42).integers(len(by_day), size=(5000, len(by_day)))
    draws = by_day[selected_days].sum(axis=1)
    summary["bootstrap95_delta_brier_by_date"] = np.quantile(draws[:, 0] / draws[:, 1], [0.025, 0.975]).tolist()
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cohort", type=Path, default=ROOT / "docs" / "benchmarks" / "cohorte-2025.json",
                        help="Manifiesto JSON de bateadores.")
    parser.add_argument("--data-dir", type=Path, default=CACHE_ROOT,
                        help="Raíz del directorio de datos que contiene statcast/.")
    parser.add_argument("--season", type=int, default=2025, help="Año de temporada regular reservado para prueba.")
    args = parser.parse_args()
    if not 2016 <= args.season <= date.today().year:
        parser.error("Elige un año desde 2016 y hasta el actual.")
    try:
        summary = benchmark(_cohort(args.cohort), args.data_dir, args.season)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(summary, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
