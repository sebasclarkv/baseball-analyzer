from datetime import date

import pandas as pd
import pytest

from baseball_analyzer_v2 import live_hitter
from baseball_analyzer_v2.analytics import HIT_EVENTS


BATTING_DATE = date(2025, 6, 1)
PLAYER = {"id": 99, "name": "Bateador"}
PITCHER = {"id": 10, "name": "Lanzador"}


def test_live_count_counts_each_completed_appearance_once_and_excludes_target_day(monkeypatch, history_data):
    base = history_data.assign(game_type="R", balls=0, strikes=0)
    reached_count = ~base["events"].isin(HIT_EVENTS)
    first_pitch = base.loc[reached_count].assign(events=None)
    at_count = base.loc[reached_count].assign(balls=1, strikes=2, events=None, pitch_number=2)
    fouls = at_count.assign(pitch_number=3)
    last_pitch = base.assign(
        balls=reached_count.astype(int), strikes=reached_count.astype(int) * 2,
        pitch_number=1 + reached_count.astype(int) * 3,
    )
    history = pd.concat([first_pitch, at_count, fouls, last_pitch], ignore_index=True)
    requested = []

    def fetch_batter(player_id, start, end):
        requested.append((player_id, start, end))
        return history   # Deliberately includes future rows to guard against a leaking loader.

    monkeypatch.setattr(live_hitter, "fetch_batter_statcast", fetch_batter)
    result = live_hitter.project_live_hitter(
        player=PLAYER, pitcher=None, official_date=BATTING_DATE,
        count={"balls": 1, "strikes": 2}, current_plate_appearance=True,
    )
    eligible = base.loc[base["game_date"].lt(BATTING_DATE.isoformat())]
    assert requested[0][0] == 99 and requested[0][2] == "2025-05-31"
    assert result["cutoff_exclusive"] == "2025-06-01"
    assert result["status"] == "available"
    assert result["sample"]["batter_pa"] == len(eligible)
    assert result["sample"]["count_reference_pa"] == len(eligible)
    assert result["sample"]["count_pa"] == (~eligible["events"].isin(HIT_EVENTS)).sum()
    assert result["next_pa"]["count_applied"]
    assert 0 < result["next_pa"]["probability"] < result["rate"]
    chances = [row["probability_one_or_more_hits"] for row in result["scenarios"]]
    assert chances[0] == pytest.approx(result["next_pa"]["probability"])
    assert 0 < chances[0] < chances[1] < chances[2] < 1


def test_another_batter_does_not_use_the_current_count_or_a_sparse_pitcher(monkeypatch, history_data):
    pitch_history = pd.concat([history_data.iloc[:1], history_data.assign(game_date="2025-06-01")])
    monkeypatch.setattr(live_hitter, "fetch_batter_statcast", lambda *args: history_data)
    monkeypatch.setattr(live_hitter, "fetch_pitcher_statcast", lambda *args: pitch_history)
    result = live_hitter.project_live_hitter(
        player=PLAYER, pitcher=PITCHER, official_date=BATTING_DATE,
        count={"balls": 3, "strikes": 0}, current_plate_appearance=False,
    )
    assert result["status"] == "available"
    assert result["sample"]["pitcher_pa"] == 1
    assert not result["sample"]["pitcher_applied"]
    assert result["next_pa"] == {"probability": result["rate"], "scope": "next",
                                  "count": None, "count_applied": False, "count_status": "not_current"}
    assert any("muestra del lanzador" in warning for warning in result["warnings"])


def test_missing_count_sample_uses_probability_from_start(monkeypatch, history_data):
    monkeypatch.setattr(live_hitter, "fetch_batter_statcast", lambda *args: history_data)
    monkeypatch.setattr(live_hitter, "fetch_pitcher_statcast", lambda *args: history_data)
    result = live_hitter.project_live_hitter(
        player=PLAYER, pitcher={**PITCHER, "hand": "L"}, official_date=BATTING_DATE,
        count={"balls": 3, "strikes": 0}, current_plate_appearance=True,
    )
    assert result["sample"]["pitcher_applied"]
    eligible = history_data.loc[history_data["game_date"].lt(BATTING_DATE.isoformat())]
    assert result["sample"]["pitcher_pa"] == eligible["p_throws"].eq("L").sum()
    assert result["next_pa"]["count_status"] == "insufficient"
    assert result["next_pa"]["probability"] == result["rate"]


def test_no_prior_plate_appearances_means_no_probability(monkeypatch, history_data):
    monkeypatch.setattr(live_hitter, "fetch_batter_statcast", lambda *args: history_data.assign(game_date="2025-06-01"))
    result = live_hitter.project_live_hitter(
        player=PLAYER, pitcher=None, official_date=BATTING_DATE,
    )
    assert result["status"] == "insufficient"
    assert result["next_pa"] is None
    assert not result["scenarios"]
