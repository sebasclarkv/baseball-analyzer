from baseball_analyzer_v2.evaluation import cohort_report
from baseball_analyzer_v2.research import temporal_split
from datetime import date, timedelta


def test_temporal_split_never_splits_players_on_same_date():
    rows = [{"date": (date(2024, 1, 1) + timedelta(days=i)).isoformat(), "player_id": p} for i in range(90) for p in (1, 2, 3)]
    train, valid, test = temporal_split(rows)
    assert max(r["date"] for r in train) < min(r["date"] for r in valid)
    assert max(r["date"] for r in valid) < min(r["date"] for r in test)
    assert len(train) + len(valid) + len(test) == len(rows)


def test_empty_cohort_does_not_claim_accuracy():
    report = cohort_report([])
    assert report["status"] == "limited"
    assert report["precision_claim"] is False
    assert report["metrics"]["predictions"] == 0


def test_candidate_training_stays_experimental(tmp_path):
    import pytest
    pytest.importorskip("catboost")
    import numpy as np
    from baseball_analyzer_v2.research import compare_candidate
    rng = np.random.default_rng(7)
    rows = []
    for day in range(180):
        for player in range(3):
            probability = .55 + .05 * player
            actual = int(rng.random() < probability)
            rows.append({"date": (date(2024, 1, 1) + timedelta(days=day)).isoformat(), "player_id": player,
                         "actual_any_hit": actual, "actual_hits": actual * (1 + int(rng.random() < .2)),
                         "forecast_any_hit": probability, "forecast_hits": .9,
                         "interval_low": 0, "interval_high": 3,
                         "features": {"hit_rate": .2 + player * .02, "recent_rate": .2 + rng.uniform(-.05, .05),
                                      "expected_pa": 4, "hr_rate": .03, "k_rate": .2, "effective_pa": 150 + day,
                                      "exit_velocity": 89 + rng.uniform(-2, 2), "xba": .25}})
    result = compare_candidate(rows, tmp_path)
    assert result["status"] == "research_only"
    assert not result["active_in_app"] and not result["eligible_for_review"]
    assert result["dates"]["train_through"] < result["dates"]["calibration_from"]
    assert result["dates"]["calibration_through"] < result["dates"]["test_from"]
    assert "predictive_interval_coverage" not in result["metrics"]
    assert (tmp_path / "hit-classifier.cbm").is_file()


def test_reference_is_not_assigned_models_interval_coverage():
    from baseball_analyzer_v2.evaluation import score
    rows = [{"baseline_any_hit": .6, "baseline_hits": 1, "actual_any_hit": 1, "actual_hits": 1, "interval_low": 0, "interval_high": 3}]
    result = score(rows, "baseline_any_hit", "baseline_hits")
    assert "predictive_interval_coverage" not in result
