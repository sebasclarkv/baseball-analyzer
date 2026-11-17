"""Download, evaluate and compare local models: python -m baseball_analyzer_v2.research --help."""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from baseball_analyzer_v2.data import fetch_batter_statcast
from baseball_analyzer_v2.evaluation import cohort_report, player_observations, score
from baseball_analyzer_v2.storage import CACHE_ROOT, atomic_json

FEATURES = ["hit_rate", "recent_rate", "expected_pa", "hr_rate", "k_rate", "effective_pa", "exit_velocity", "xba"]


def temporal_split(rows: list[dict]) -> tuple[list, list, list]:
    days = sorted({r["date"] for r in rows})
    if len(days) < 30:
        raise ValueError("La comparación necesita al menos 30 fechas distintas y más historia para entrenamiento.")
    validation_start, test_start = days[int(len(days) * .6)], days[int(len(days) * .8)]
    return ([r for r in rows if r["date"] < validation_start],
            [r for r in rows if validation_start <= r["date"] < test_start],
            [r for r in rows if r["date"] >= test_start])


def compare_candidate(rows: list[dict], output: Path) -> dict:
    """Candidate remains experimental. Holdout never tunes the model or calibration."""
    try:
        from catboost import CatBoostClassifier, CatBoostRegressor
        from sklearn.linear_model import LogisticRegression
    except ImportError as exc:
        raise ValueError("Instala requirements-research.txt para comparar CatBoost.") from exc
    train, valid, test = temporal_split(rows)
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


def main():
    parser = argparse.ArgumentParser(description="Datos y evaluación local de Diamond IQ.")
    commands = parser.add_subparsers(dest="command", required=True)
    download = commands.add_parser("download", help="Descarga historial por jugador a la caché Parquet.")
    download.add_argument("--players", required=True, help="IDs MLB separados por coma.")
    download.add_argument("--start", type=date.fromisoformat, required=True)
    download.add_argument("--end", type=date.fromisoformat, required=True)
    evaluate = commands.add_parser("evaluate", help="Evalúa archivos Statcast CSV/Parquet sin descargar datos.")
    evaluate.add_argument("--input", action="append", required=True, type=Path)
    evaluate.add_argument("--max-games", type=int, default=500)
    evaluate.add_argument("--games", type=int, default=40)
    evaluate.add_argument("--lookback-days", type=int, default=3650)
    evaluate.add_argument("--candidate", action="store_true")
    evaluate.add_argument("--output", type=Path, default=CACHE_ROOT / "evaluation")
    args = parser.parse_args()
    if args.command == "download":
        if args.start < date(2015, 1, 1) or args.end >= date.today() or args.start > args.end:
            parser.error("Usa fechas desde2015 y una fecha final anterior a hoy.")
        for player in [int(x.strip()) for x in args.players.split(",")]:
            frame = fetch_batter_statcast(player, args.start.isoformat(), args.end.isoformat())
            print(f"Jugador {player}: {len(frame)} lanzamientos. Archivo: {CACHE_ROOT / 'statcast' / f'batter-{player}.parquet'}", flush=True)
        return
    if args.max_games < 1 or not 5 <= args.games <= 150 or not 30 <= args.lookback_days <= 5000:
        parser.error("Revisa max-games (>0), games (5–150) y lookback-days (30–5000).")
    frames = []
    for path in args.input:
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
        raw = raw.drop_duplicates(keys)
    observations = []
    for player_id, frame in raw.groupby("batter"):
        print(f"Evaluando {int(player_id)}...", flush=True)
        rows = player_observations(frame, {"id": int(player_id)}, args.games, args.lookback_days, args.max_games,
                                   lambda done, total: print(f"  {done}/{total}", flush=True) if done % 25 == 0 or done == total else None)
        observations.extend(rows)
    report = cohort_report(observations)
    report["input_files"] = [str(p) for p in args.input]
    args.output.mkdir(parents=True, exist_ok=True)
    if observations:
        with duckdb.connect() as db:
            db.register("observations", pd.json_normalize(observations))
            db.execute("COPY observations TO ? (FORMAT PARQUET)", [str(args.output / "observations.parquet")])
    if args.candidate:
        try:
            report["candidate"] = compare_candidate(observations, args.output / "candidate")
        except ValueError as exc:
            report["candidate"] = {"status": "unavailable", "reason": str(exc), "active_in_app": False}
    atomic_json(args.output / "latest.json", report)
    print(json.dumps({k: report[k] for k in ("status", "predictions", "players", "metrics")}, indent=2))
    print(f"Reporte: {args.output / 'latest.json'}")


if __name__ == "__main__":
    main()
