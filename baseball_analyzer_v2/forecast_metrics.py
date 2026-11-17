"""Scoring for the ordered outcome categories shown in the app."""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np


def score_hit_buckets(probabilities: Sequence[Sequence[float]], actual_hits: Sequence[int]) -> dict:
    """Score [P(0), P(1), P(2+)] with lower-is-better proper scores."""
    if len(probabilities) == 0 and len(actual_hits) == 0:
        return {"predictions": 0}
    p = np.asarray(probabilities, dtype=float)
    hits = np.asarray(actual_hits, dtype=float)
    if (hits.ndim != 1 or p.shape != (len(hits), 3) or not np.isfinite(p).all()
            or np.any((p < 0) | (p > 1)) or not np.allclose(p.sum(axis=1), 1, rtol=0, atol=1e-6)
            or not np.isfinite(hits).all() or np.any(hits < 0) or np.any(hits != np.floor(hits))):
        raise ValueError("Se requieren probabilidades válidas de 0/1/2+ hits y conteos enteros no negativos.")
    outcomes = np.minimum(hits, 2).astype(int)
    actual = np.eye(3)[outcomes]
    edges = np.linspace(0, 1, 11)
    calibration = {}
    for i, label in enumerate(("0", "1", "2+")):
        bins = []
        bucket = np.minimum(np.searchsorted(edges, p[:, i], side="right") - 1, 9)
        for b in range(10):
            points = bucket == b
            if points.any():
                bins.append({"from": round(float(edges[b]), 1), "through": round(float(edges[b + 1]), 1),
                             "count": int(points.sum()), "predicted": float(p[points, i].mean()),
                             "observed": float(actual[points, i].mean())})
        calibration[label] = {"predicted": float(p[:, i].mean()), "observed": float(actual[:, i].mean()), "bins": bins}
    cumulative_error = np.cumsum(p - actual, axis=1)[:, :2]
    return {
        "predictions": len(hits),
        "ranked_probability_score": float(np.mean(cumulative_error ** 2)),
        "multiclass_brier_score": float(np.mean(((p - actual) ** 2).sum(axis=1))),
        "multiclass_log_loss": float(-np.log(np.clip(p[np.arange(len(outcomes)), outcomes], 1e-15, 1)).mean()),
        "calibration_by_hit_bucket": calibration,
    }
