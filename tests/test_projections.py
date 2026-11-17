from datetime import date
import numpy as np
import pandas as pd
import pytest
from baseball_analyzer_v2.projections import beta_binomial, build_projection
from baseball_analyzer_v2.evaluation import evaluate_player, player_observations


def forecast(raw, **kw):
    return build_projection(raw, {"id": 99, "name": "Jugador de prueba"}, 10, 365, forecast_date=date(2025, 7, 1), **kw)


def test_distribution_and_mean_are_coherent(history_data):
    report = forecast(history_data)
    p = report["projection"]
    assert p["probability_zero_hits"] + p["probability_exactly_one_hit"] + p["probability_two_or_more_hits"] == pytest.approx(1)
    assert np.dot(np.arange(len(p["hit_distribution"])), p["hit_distribution"]) == pytest.approx(p["hits"])
    assert 0 <= p["hits_low"] <= p["hits_high"] <= len(p["hit_distribution"]) - 1
    assert not p["predictive_interval_hits"]["calibrated"]
    assert report["data_quality"]["status"] == "provisional"


def test_target_and_future_games_never_enter_forecast(history_data):
    expected = forecast(history_data)["projection"]
    extra = history_data.iloc[:5].copy()
    extra["game_date"] = "2025-07-01"
    extra["events"] = "home_run"
    extra["game_pk"] = 777777
    assert forecast(pd.concat([history_data, extra], ignore_index=True))["projection"] == expected


def test_lineup_position_changes_opportunities(history_data):
    early = forecast(history_data, inputs={"batting_order": 1, "team_plate_appearances": [37] * 10})
    late = forecast(history_data, inputs={"batting_order": 9, "team_plate_appearances": [37] * 10})
    assert early["projection"]["plate_appearances"] == pytest.approx(5)
    assert late["projection"]["plate_appearances"] == pytest.approx(4)
    assert early["projection"]["probability_one_or_more_hits"] > late["projection"]["probability_one_or_more_hits"]


def test_starter_does_not_receive_every_appearance(history_data):
    p = forecast(history_data, pitcher_stats=history_data, opponent_pitcher={"id": 10, "name": "Pitcher"},
                 inputs={"batting_order": 1, "team_plate_appearances": [40], "starter_batters_faced": [18]})["projection"]
    assert p["starter_plate_appearances"] == pytest.approx(2)
    assert p["bullpen_plate_appearances"] == pytest.approx(3)
    assert p["starter_plate_appearances"] + p["bullpen_plate_appearances"] == pytest.approx(p["plate_appearances"])


def test_bullpen_adjustment_is_directional(history_data):
    def result(hits):
        totals = {"battersFaced": 500, "hits": hits, "homeRuns": 15, "baseOnBalls": 40, "strikeOuts": 110}
        return forecast(history_data, inputs={"bullpen": {"totals": totals}})["projection"]["probability_one_or_more_hits"]
    assert result(180) > result(60)


def test_beta_binomial_extremes_and_zero_opportunities():
    assert beta_binomial({"rate": .2, "low": .1, "high": .3}, 0).tolist() == [1]
    for rate in (0, 1, .3):
        pmf = beta_binomial({"rate": rate, "low": max(0, rate - .1), "high": min(1, rate + .1)}, 10)
        assert np.isfinite(pmf).all()
        assert pmf.sum() == pytest.approx(1)


def test_backtest_excludes_target_results_and_reports_calibration(history_data):
    original = player_observations(history_data, {"id": 99}, 10, 365, 2)
    modified = history_data.copy()
    last_date = modified.game_date.max()
    modified.loc[modified.game_date == last_date, "events"] = "home_run"
    after = player_observations(modified, {"id": 99}, 10, 365, 2)
    assert original[-1]["forecast_hits"] == after[-1]["forecast_hits"]
    assert original[-1]["actual_hits"] != after[-1]["actual_hits"]
    report = evaluate_player(history_data, {"id": 99}, 10, 365, 3)
    assert report["predictions"] == 3
    assert sum(b["count"] for b in report["calibration_bins"]) == 3
    assert 0 <= report["predictive_interval_coverage"] <= 1


def test_home_runs_remain_subset_even_when_xba_is_extreme(history_data):
    history_data["events"] = "home_run"
    history_data["estimated_ba_using_speedangle"] = .05
    report = forecast(history_data, pitcher_stats=history_data, opponent_pitcher={"id": 10, "name": "Pitcher"})
    p = report["projection"]
    assert p["home_runs"] <= p["hits"] + 1e-9
    assert p["probability_one_or_more_home_runs"] <= p["probability_one_or_more_hits"] + 1e-9


def test_ancient_sample_does_not_produce_artificial_precision():
    from baseball_analyzer_v2.analytics import _smoothed_rate
    recent = _smoothed_rate(200, 1000, 1000, .22)
    old = _smoothed_rate(.2, 1, 1000, .22)
    assert old["high"] - old["low"] > recent["high"] - recent["low"]
