from datetime import date

import pandas as pd
import pytest

from baseball_analyzer_v2 import cohorts
from baseball_analyzer_v2.evaluation import cohort_report, player_observations
from baseball_analyzer_v2.projections import build_projection
from baseball_analyzer_v2.research import temporal_split


def test_cohort_is_reproducible_and_never_uses_the_holdout(monkeypatch):
    def player(player_id, appearances, debut):
        return {"player": {"id": player_id, "fullName": f"Player {player_id}", "mlbDebutDate": f"{debut}-06-01"},
                "stat": {"plateAppearances": appearances}}

    source_rows = [player(i, 520, 2019) for i in range(1, 21)]
    source_rows += [player(i, 240, 2024) for i in range(21, 41)]
    source_rows += [player(i, 170, 2022) for i in range(41, 61)]
    source_rows += [player(61, 500, 2025)]

    def get(url, params, ttl):
        assert params["season"] == 2024 and params["gameType"] == "R"
        return {"stats": [{"totalSplits": len(source_rows), "splits": source_rows}]}, {}

    monkeypatch.setattr(cohorts, "public_json", get)
    first = cohorts.select_cohort(2024, 40, 42)
    source_rows.reverse()
    second = cohorts.select_cohort(2024, 40, 42)
    assert first == second
    assert first["holdout_season"] == 2025
    assert all(p["id"] != 61 for p in first["players"])
    assert sum(p["group"] == "regular_volume" for p in first["players"]) == 14
    assert sum(p["group"] == "debut_season" for p in first["players"]) == 13


def test_projection_uses_only_selected_phase_and_supports_legacy(history_data):
    regular = history_data.loc[history_data.game_pk < 1030].assign(game_type="R")
    playoffs = history_data.loc[history_data.game_pk >= 1030].assign(game_type="D", events="home_run")
    combined = pd.concat([regular, playoffs], ignore_index=True)
    player = {"id": 99}
    cutoff = date(2025, 7, 1)
    regular_report = build_projection(regular, player, 10, 365, forecast_date=cutoff)
    mixed_report = build_projection(combined, player, 10, 365, forecast_date=cutoff)
    assert mixed_report["projection"] == regular_report["projection"]
    playoff_report = build_projection(combined, player, 10, 365, forecast_date=cutoff,
                                      context={"game": {"game_type": "D"}})
    assert playoff_report["sample"]["plate_appearances"] == len(playoffs)
    assert playoff_report["model"]["history_game_type"] == "postseason"
    legacy_report = build_projection(regular.drop(columns="game_type"), player, 10, 365,
                                     forecast_date=cutoff, game_type="spring")
    assert legacy_report["model"]["game_type"] == "spring"
    assert legacy_report["model"]["history_game_type"] == "regular"
    assert legacy_report["sample"]["plate_appearances"] == len(regular)
    assert legacy_report["data_quality"]["status"] == "provisional"


def test_season_holdout_scores_distribution_on_held_out_games_only(history_data):
    first = history_data.assign(game_date=history_data.game_date.str.replace("2025", "2024"), game_type="R")
    second = history_data.assign(game_pk=history_data.game_pk + 100, game_type="R")
    spring = history_data.assign(game_pk=history_data.game_pk + 200, game_type="S")
    rows = player_observations(pd.concat([first, second, spring], ignore_index=True),
                               {"id": 99, "group": "low_pa"}, 10, 550, 4, seasons=(2024, 2025))
    assert len(rows) == 8
    assert [r["game_id"] for r in rows] == ["1010", "1029", "1049", "1069", "1100", "1123", "1146", "1169"]
    report = cohort_report(rows, 2025)
    assert report["predictions"] == 4 and report["total_observations"] == 8
    assert report["by_season"]["2024"]["predictions"] == 4
    assert report["by_group"]["low_pa"]["players"] == 1
    assert report["metrics"]["hit_buckets"]["predictions"] == 4
    assert report["baseline"]["hit_buckets"]["ranked_probability_score"] == pytest.approx(
        cohort_report(rows[-4:])["baseline"]["hit_buckets"]["ranked_probability_score"])


def test_candidate_excludes_reserved_season_from_fit_and_calibration():
    development = [{"date": f"2024-04-{day:02d}"} for day in range(1, 31)]
    holdout = [{"date": "2025-04-01"}, {"date": "2025-09-28"}]
    train, validation, test = temporal_split([*development, *holdout, {"date": "2026-04-01"}], 2025)
    assert train + validation == development
    assert test == holdout
