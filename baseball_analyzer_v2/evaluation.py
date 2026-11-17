"""Chronological, multi-player evaluation. Each prediction excludes its entire target date."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Callable

import numpy as np
import pandas as pd

from baseball_analyzer_v2.analytics import HIT_EVENTS
from baseball_analyzer_v2.projections import build_projection


def score(rows: list[dict], probability: str = "forecast_any_hit", expected: str = "forecast_hits") -> dict:
    if not rows:
        return {"predictions": 0}
    p = np.clip([r[probability] for r in rows], 1e-9, 1 - 1e-9)
    y = np.array([r["actual_any_hit"] for r in rows])
    hits = np.array([r["actual_hits"] for r in rows])
    counts = np.array([r[expected] for r in rows])
    bins = []
    for low in np.arange(0, 1, .1):
        mask = (p >= low) & (p < low + .1 + (1e-9 if low > .89 else 0))
        if mask.any():
            bins.append({"from": round(float(low), 1), "through": round(float(low + .1), 1),
                         "count": int(mask.sum()), "predicted": float(p[mask].mean()), "observed": float(y[mask].mean())})
    result = {"predictions": len(rows), "mean_absolute_error_hits": float(np.mean(abs(counts - hits))),
            "brier_score_one_or_more_hits": float(np.mean((p - y) ** 2)),
            "log_loss": float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))),
            "mean_predicted_probability_one_or_more_hits": float(p.mean()),
            "observed_probability_one_or_more_hits": float(y.mean()),
            "calibration_gap": float(abs(p.mean() - y.mean())), "calibration_bins": bins,
            "expected_calibration_error": sum(b["count"] * abs(b["predicted"] - b["observed"]) for b in bins) / len(rows),
            "predictive_interval_coverage": float(np.mean([r["interval_low"] <= r["actual_hits"] <= r["interval_high"] for r in rows])),
            "mean_interval_width": float(np.mean([r["interval_high"] - r["interval_low"] for r in rows]))}
    if probability != "forecast_any_hit" or expected != "forecast_hits":
        result.pop("predictive_interval_coverage", None)
        result.pop("mean_interval_width", None)
    return result


def player_observations(raw: pd.DataFrame, player: dict, games_requested: int = 40, lookback_days: int = 1095,
                        max_predictions: int = 500, progress: Callable | None = None) -> list[dict]:
    if raw.empty or not {"game_date", "events"}.issubset(raw.columns):
        return []
    rows = raw.copy()
    rows["_day"] = pd.to_datetime(rows["game_date"], errors="coerce", utc=True).dt.date
    rows = rows.loc[rows["_day"].notna() & (rows["_day"] < datetime.now(timezone.utc).date())]
    rows["_id"] = rows["game_pk"].astype(str) if "game_pk" in rows else rows["_day"].astype(str)
    pa = rows.loc[rows["events"].notna()]
    targets = pa.groupby("_id").agg(day=("_day", "min"), pa=("events", "size"),
                                   hits=("events", lambda x: int(x.isin(HIT_EVENTS).sum()))).sort_values(["day"])
    eligible = []
    for game_id, target in targets.iterrows():
        days = targets["day"]
        count = ((days < target["day"]) & (days >= target["day"] - timedelta(days=lookback_days))).sum()
        if count >= games_requested:
            eligible.append((game_id, target))
    eligible = eligible[-max_predictions:]
    observations = []
    for i, (game_id, target) in enumerate(eligible):
        history = rows.loc[(rows["_day"] < target["day"]) & (rows["_day"] >= target["day"] - timedelta(days=lookback_days))]
        forecast = build_projection(history, player, games_requested, lookback_days, forecast_date=target["day"])
        p = forecast["projection"]
        baseline = forecast["per_plate_appearance"]["hits"]["recent_sample_rate"]
        # Same predicted opportunity distribution for both models; no target PA is a feature.
        from baseball_analyzer_v2.projections import _opportunities
        opportunities, weights, _ = _opportunities(history, {})
        baseline_probability = float(sum(w * (1 - (1 - baseline) ** n) for n, w in zip(opportunities, weights)))
        observations.append({"player_id": int(player["id"]), "game_id": str(game_id), "date": target["day"].isoformat(),
            "forecast_hits": p["hits"], "forecast_any_hit": p["probability_one_or_more_hits"],
            "baseline_hits": p["plate_appearances"] * baseline, "baseline_any_hit": baseline_probability,
            "actual_hits": int(target["hits"]), "actual_any_hit": int(target["hits"] > 0),
            "forecast_plate_appearances": p["plate_appearances"], "actual_plate_appearances": int(target["pa"]),
            "interval_low": p["predictive_interval_hits"]["low"], "interval_high": p["predictive_interval_hits"]["high"],
            "features": {"hit_rate": forecast["per_plate_appearance"]["hits"]["rate"], "recent_rate": baseline,
                         "expected_pa": p["plate_appearances"], "hr_rate": forecast["per_plate_appearance"]["home_runs"]["rate"],
                         "k_rate": forecast["per_plate_appearance"]["strikeouts"]["rate"],
                         "effective_pa": forecast["sample"]["effective_plate_appearances"],
                         "exit_velocity": forecast["contact"]["average_exit_velocity"],
                         "xba": forecast["contact"]["statcast_xba"]}})
        if progress:
            progress(i + 1, len(eligible))
    return observations


def evaluate_player(raw: pd.DataFrame, player: dict, games_requested: int = 40, lookback_days: int = 1095,
                    max_predictions: int = 100, progress: Callable | None = None) -> dict:
    rows = player_observations(raw, player, games_requested, lookback_days, max_predictions, progress)
    if not rows:
        return {"status": "insufficient", "predictions": 0, "message": "No hay suficientes juegos anteriores para evaluar."}
    result = score(rows)
    baseline = score(rows, "baseline_any_hit", "baseline_hits")
    result.update({"status": "available" if len(rows) >= 30 else "limited", "from": rows[0]["date"], "through": rows[-1]["date"],
                   "recent_rate_baseline_mae_hits": baseline["mean_absolute_error_hits"],
                   "mae_improvement_vs_recent_rate": (baseline["mean_absolute_error_hits"] - result["mean_absolute_error_hits"]) / baseline["mean_absolute_error_hits"] if baseline["mean_absolute_error_hits"] else None,
                   "mean_absolute_error_plate_appearances": float(np.mean([abs(r["forecast_plate_appearances"] - r["actual_plate_appearances"]) for r in rows])),
                   "baseline": baseline, "method": "rolling_origin_exclusive_date",
                   "message": "Evaluación del perfil base por fechas; no reconstruye alineaciones, clima ni bullpen históricos. El mismo día se excluye completo, incluso en dobles juegos."})
    return result


def cohort_report(observations: list[dict]) -> dict:
    rows = sorted(observations, key=lambda r: (r["date"], r["player_id"], r["game_id"]))
    metrics = score(rows)
    players = sorted({r["player_id"] for r in rows})
    seasons = sorted({r["date"][:4] for r in rows})
    return {"status": "available" if len(rows) >= 1000 and len(players) >= 10 else "limited", "model_version": "2.1",
            "generated_at": datetime.now(timezone.utc).isoformat(), "predictions": len(rows), "players": len(players),
            "seasons": seasons, "metrics": metrics, "baseline": score(rows, "baseline_any_hit", "baseline_hits"),
            "by_season": {s: score([r for r in rows if r["date"].startswith(s)]) for s in seasons},
            "by_player": {str(p): score([r for r in rows if r["player_id"] == p]) for p in players},
            "scope": "Perfil del bateador; contexto histórico de partido no reconstruido. Ningún resultado futuro se usa como predictor.",
            "precision_claim": False}
