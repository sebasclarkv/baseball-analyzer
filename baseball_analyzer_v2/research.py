"""Download, evaluate and compare local models: python -m baseball_analyzer_v2.research --help."""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from baseball_analyzer_v2.analytics import GAME_TYPE_CODES, filter_game_type
from baseball_analyzer_v2.cohorts import select_cohort
from baseball_analyzer_v2.data import fetch_batter_statcast
from baseball_analyzer_v2.evaluation import cohort_report, player_observations, score
from baseball_analyzer_v2.storage import CACHE_ROOT, atomic_json

FEATURES = ["hit_rate", "recent_rate", "expected_pa", "hr_rate", "k_rate", "effective_pa", "exit_velocity", "xba"]


def temporal_split(rows: list[dict], holdout_season: int | None = None) -> tuple[list, list, list]:
    if holdout_season is not None:
        boundary = f"{holdout_season}-01-01"
        development = [r for r in rows if r["date"] < boundary]
        test = [r for r in rows if int(r["date"][:4]) == holdout_season]
        days = sorted({r["date"] for r in development})
        if len(days) < 30 or not test:
            raise ValueError("Se necesitan al menos 30 fechas previas y observaciones en la temporada reservada.")
        calibration_start = days[int(len(days) * .75)]
        return ([r for r in development if r["date"] < calibration_start],
                [r for r in development if r["date"] >= calibration_start], test)
    days = sorted({r["date"] for r in rows})
    if len(days) < 30:
        raise ValueError("La comparación necesita al menos 30 fechas distintas y más historia para entrenamiento.")
    validation_start, test_start = days[int(len(days) * .6)], days[int(len(days) * .8)]
    return ([r for r in rows if r["date"] < validation_start],
            [r for r in rows if validation_start <= r["date"] < test_start],
            [r for r in rows if r["date"] >= test_start])


def compare_candidate(rows: list[dict], output: Path, holdout_season: int | None = None) -> dict:
    """Candidate remains experimental. Holdout never tunes the model or calibration."""
    try:
        from catboost import CatBoostClassifier, CatBoostRegressor
        from sklearn.linear_model import LogisticRegression
    except ImportError as exc:
        raise ValueError("Instala requirements-research.txt para comparar CatBoost.") from exc
    train, valid, test = temporal_split(rows, holdout_season)
    if min(len(train), len(valid), len(test)) < 100:
        raise ValueError("Se necesitan al menos 100 observaciones en cada corte temporal (entrenamiento, calibración y prueba).")
    def x(part):
        return pd.DataFrame([{key: r["features"].get(key) for key in FEATURES} for r in part], columns=FEATURES).fillna(np.nan)
    if len({r["actual_any_hit"] for r in train}) < 2 or len({r["actual_any_hit"] for r in valid}) < 2:
        raise ValueError("Entrenamiento y calibración necesitan juegos con y sin hits.")
    classifier = CatBoostClassifier(iterations=250, depth=4, learning_rate=.04, loss_function="Logloss", random_seed=42,
                                   thread_count=2, verbose=False, allow_writing_files=False)
    regressor = CatBoostRegressor(iterations=250, depth=4, learning_rate=.04, loss_function="Poisson", random_seed=42,
                                 thread_count=2, verbose=False, allow_writing_files=False)
    classifier.fit(x(train), [r["actual_any_hit"] for r in train])
    regressor.fit(x(train), [r["actual_hits"] for r in train])
    calibrator = LogisticRegression(C=1.0)
    calibrator.fit(classifier.predict(x(valid), prediction_type="RawFormulaVal").reshape(-1, 1), [r["actual_any_hit"] for r in valid])
    probability = calibrator.predict_proba(classifier.predict(x(test), prediction_type="RawFormulaVal").reshape(-1, 1))[:, 1]
    counts = regressor.predict(x(test), prediction_type="Exponent")
    predictions = [{**row, "candidate_probability": float(p), "candidate_hits": max(0.0, float(count))} for row, p, count in zip(test, probability, counts)]
    metrics = score(predictions, "candidate_probability", "candidate_hits")
    # The candidate has no count interval; never attach the baseline's coverage to it.
    metrics.pop("predictive_interval_coverage", None)
    metrics.pop("mean_interval_width", None)
    reference = score(test)
    day_deltas = {}
    for r in predictions:
        delta = (r["candidate_probability"] - r["actual_any_hit"]) ** 2 - (r["forecast_any_hit"] - r["actual_any_hit"]) ** 2
        day_deltas.setdefault(r["date"], []).append(delta)
    # Resample whole dates: players from the same slate are dependent observations.
    sums = np.array([sum(v) for v in day_deltas.values()])
    sizes = np.array([len(v) for v in day_deltas.values()])
    rng = np.random.default_rng(42)
    indices = rng.integers(0, len(sums), (1000, len(sums)))
    intervals = np.quantile(sums[indices].sum(axis=1) / sizes[indices].sum(axis=1), [.025, .975])
    enough = len(test) >= 1000 and len({r["player_id"] for r in test}) >= 10
    candidate_better = (metrics["brier_score_one_or_more_hits"] < reference["brier_score_one_or_more_hits"]
                        and metrics["mean_absolute_error_hits"] <= reference["mean_absolute_error_hits"]
                        and metrics["log_loss"] <= reference["log_loss"] and intervals[1] < 0)
    output.mkdir(parents=True, exist_ok=True)
    classifier.save_model(str(output / "hit-classifier.cbm"))
    regressor.save_model(str(output / "hit-count.cbm"))
    atomic_json(output / "calibration.json", {"features": FEATURES, "coefficient": float(calibrator.coef_[0][0]),
                                               "intercept": float(calibrator.intercept_[0])})
    return {"name": "CatBoost + calibración logística", "status": "research_only", "active_in_app": False,
            "eligible_for_review": bool(enough and candidate_better), "metrics": metrics, "reference_same_test_dates": reference,
            "brier_delta_95_percent_date_bootstrap": intervals.tolist(), "sizes": {"train": len(train), "calibration": len(valid), "test": len(test)},
            "dates": {"train_through": max(r["date"] for r in train), "calibration_from": min(r["date"] for r in valid),
                      "calibration_through": max(r["date"] for r in valid), "test_from": min(r["date"] for r in test)},
            "scope": "Solo características históricas del bateador. No aprende efectos de clima/parque sin un conjunto histórico de esas variables.",
            "note": "Un candidato no sustituye automáticamente el modelo público. Elegibilidad requiere 1000 juegos y 10 jugadores en prueba, mejora de Brier con intervalo por fechas bajo cero, sin empeorar MAE ni log loss."}


def _load_cohort(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    players = manifest["players"]
    ids = {int(p["id"]) for p in players}
    if (manifest.get("schema_version") != 1 or len(ids) != len(players)
            or not 30 <= len(players) <= 50 or min(ids) <= 0
            or int(manifest["holdout_season"]) != int(manifest["selection_season"]) + 1):
        raise ValueError("El manifiesto no es una cohorte válida generada por research cohort.")
    return manifest


def main():
    parser = argparse.ArgumentParser(description="Datos y evaluación local de Diamond IQ.")
    commands = parser.add_subparsers(dest="command", required=True)
    cohort = commands.add_parser("cohort", help="Selecciona una cohorte de MLB antes de la temporada de prueba.")
    cohort.add_argument("--selection-season", required=True, type=int)
    cohort.add_argument("--size", type=int, default=40)
    cohort.add_argument("--seed", type=int, default=42)
    cohort.add_argument("--output", type=Path, default=CACHE_ROOT / "evaluation" / "cohort.json")
    download = commands.add_parser("download", help="Descarga historial por jugador a la caché Parquet.")
    download_source = download.add_mutually_exclusive_group(required=True)
    download_source.add_argument("--players", help="IDs MLB separados por coma.")
    download_source.add_argument("--cohort", type=Path, help="Manifiesto generado con research cohort.")
    download.add_argument("--start", type=date.fromisoformat, required=True)
    download.add_argument("--end", type=date.fromisoformat, required=True)
    evaluate = commands.add_parser("evaluate", help="Evalúa archivos Statcast CSV/Parquet sin descargar datos.")
    evaluate_source = evaluate.add_mutually_exclusive_group(required=True)
    evaluate_source.add_argument("--input", action="append", type=Path)
    evaluate_source.add_argument("--cohort", type=Path, help="Lee los históricos de los jugadores del manifiesto en la caché Parquet.")
    evaluate.add_argument("--game-type", choices=tuple(GAME_TYPE_CODES), default="regular")
    evaluate.add_argument("--holdout-season", type=int, help="Reserva un año de prueba para --input. --cohort ya declara el suyo.")
    evaluate.add_argument("--max-games", type=int, default=500, help="Límite por jugador, y por temporada al reservar un año de prueba.")
    evaluate.add_argument("--games", type=int, default=40)
    evaluate.add_argument("--lookback-days", type=int, default=3650)
    evaluate.add_argument("--candidate", action="store_true")
    evaluate.add_argument("--output", type=Path, default=CACHE_ROOT / "evaluation")
    args = parser.parse_args()
    if args.command == "cohort":
        if not 2015 <= args.selection_season < date.today().year:
            parser.error("Selecciona una temporada MLB anterior al año actual, desde 2015.")
        try:
            manifest = select_cohort(args.selection_season, args.size, args.seed)
        except ValueError as exc:
            parser.error(str(exc))
        atomic_json(args.output, manifest)
        print(f"Cohorte de {len(manifest['players'])} bateadores. Archivo: {args.output}")
        return
    manifest = None
    if args.cohort:
        try:
            manifest = _load_cohort(args.cohort)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            parser.error(f"No se pudo leer la cohorte: {exc}")
    if args.command == "download":
        if args.start < date(2015, 1, 1) or args.end >= date.today() or args.start > args.end:
            parser.error("Usa fechas desde 2015 y una fecha final anterior a hoy.")
        players = [p["id"] for p in manifest["players"]] if manifest else [int(x.strip()) for x in args.players.split(",")]
        for player in players:
            frame = fetch_batter_statcast(player, args.start.isoformat(), args.end.isoformat())
            print(f"Jugador {player}: {len(frame)} lanzamientos. Archivo: {CACHE_ROOT / 'statcast' / f'batter-{player}.parquet'}", flush=True)
        return
    if args.max_games < 1 or not 5 <= args.games <= 150 or not 30 <= args.lookback_days <= 5000:
        parser.error("Revisa max-games (>0), games (5–150) y lookback-days (30–5000).")
    if manifest and args.holdout_season is not None:
        parser.error("La temporada reservada para --cohort ya está en el manifiesto.")
    holdout_season = int(manifest["holdout_season"]) if manifest else args.holdout_season
    players = {int(p["id"]): p for p in manifest["players"]} if manifest else {}
    inputs = [CACHE_ROOT / "statcast" / f"batter-{pid}.parquet" for pid in players] if manifest else args.input
    frames = []
    for path in inputs:
        if not path.is_file():
            parser.error(f"Falta {path}. Descarga el historial antes de evaluar.")
        if path.suffix.lower() == ".parquet":
            with duckdb.connect() as db:
                frames.append(db.execute("SELECT * FROM read_parquet(?)", [str(path)]).df())
        else:
            frames.append(pd.read_csv(path, low_memory=False))
    raw = pd.concat(frames, ignore_index=True)
    if not {"batter", "game_date", "events"}.issubset(raw.columns):
        parser.error("Los archivos requieren columnas batter, game_date y events de Statcast.")
    keys = [c for c in ("game_pk", "at_bat_number", "pitch_number") if c in raw]
    if len(keys) == 3:
        identified = raw[keys].notna().all(axis=1)
        raw = pd.concat([raw.loc[identified].drop_duplicates(keys), raw.loc[~identified]], ignore_index=True)
    seasons = None
    if holdout_season is not None:
        in_phase = filter_game_type(raw, args.game_type)
        season_years = pd.to_datetime(in_phase["game_date"], errors="coerce").dt.year.dropna().astype(int)
        seasons = tuple(sorted(year for year in season_years.unique().tolist() if year <= holdout_season))
        if holdout_season not in seasons or len(seasons) < 2:
            parser.error("La evaluación reservada requiere datos de la temporada de prueba y al menos una anterior en la misma fase.")
    observations = []
    for player_id, frame in raw.groupby("batter"):
        player_id = int(player_id)
        if manifest and player_id not in players:
            parser.error(f"El historial contiene un jugador ajeno al manifiesto: {player_id}.")
        print(f"Evaluando {player_id}...", flush=True)
        rows = player_observations(frame, players.get(player_id, {"id": player_id}), args.games, args.lookback_days, args.max_games,
                                   game_type=args.game_type, seasons=seasons)
        print(f"  {len(rows)} pronósticos", flush=True)
        observations.extend(rows)
    report = cohort_report(observations, holdout_season)
    report.update({"game_type": args.game_type, "input_files": [str(p) for p in inputs],
                   "settings": {"recent_games": args.games, "lookback_days": args.lookback_days,
                                "max_games": args.max_games, "sampling": "evenly_spaced_by_season" if seasons else "most_recent"}})
    if manifest:
        report["cohort"] = manifest
    args.output.mkdir(parents=True, exist_ok=True)
    if observations:
        with duckdb.connect() as db:
            db.register("observations", pd.json_normalize(observations))
            db.execute("COPY observations TO ? (FORMAT PARQUET)", [str(args.output / "observations.parquet")])
    if args.candidate:
        try:
            report["candidate"] = compare_candidate(observations, args.output / "candidate", holdout_season)
        except ValueError as exc:
            report["candidate"] = {"status": "unavailable", "reason": str(exc), "active_in_app": False}
    atomic_json(args.output / "latest.json", report)
    print(json.dumps({k: report[k] for k in ("status", "predictions", "players", "holdout_season")}, indent=2))
    print(f"Reporte: {args.output / 'latest.json'}")


if __name__ == "__main__":
    main()
