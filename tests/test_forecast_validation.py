from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json

import pytest

from baseball_analyzer_v2 import forecast_validation as validation
from baseball_analyzer_v2.forecast_metrics import score_hit_buckets
from baseball_analyzer_v2.projections import build_projection
from baseball_analyzer_v2.storage import atomic_json


def test_ordered_bucket_score_penalizes_misses_farther_from_actual():
    near = score_hit_buckets([[0.0, 1.0, 0.0]], [0])
    far = score_hit_buckets([[0.0, 0.0, 1.0]], [0])
    assert near["ranked_probability_score"] == 0.5
    assert far["ranked_probability_score"] == 1
    assert near["calibration_by_hit_bucket"]["1"]["bins"][0]["count"] == 1
    with pytest.raises(ValueError):
        score_hit_buckets([[0.6, 0.6, 0.0]], [0])


def test_final_adapter_reads_first_pitch_and_game_batting_totals(monkeypatch):
    def player(player_id, batting, season_hits=0):
        return {"person": {"id": player_id}, "stats": {"batting": batting},
                "seasonStats": {"batting": {"hits": season_hits}}}

    feed = {"gamePk": 123, "gameData": {"game": {"type": "R"},
             "datetime": {"officialDate": "2025-07-01", "dateTime": "2025-07-01T18:45:00Z"},
             "status": {"abstractGameState": "Final"}},
             "liveData": {"plays": {"allPlays": [{"playEvents": [
                 {"isPitch": False, "startTime": "2025-07-01T17:00:00Z"},
                 {"isPitch": True, "startTime": "2025-07-01T19:00:00Z"},
             ]}]}, "boxscore": {"teams": {"home": {"batters": [99, 101], "players": {
                 "ID99": player(99, {"hits": 2, "plateAppearances": 5}, season_hits=90),
                 "ID100": player(100, {}), "ID101": player(101, {}),
             }}, "away": {"players": {}}}}}}
    monkeypatch.setattr(validation, "public_json", lambda *a, **k: (feed, {"stale": False}))
    result = validation.fetch_final_game(123)
    assert result["first_pitch"] == datetime(2025, 7, 1, 19, tzinfo=timezone.utc)
    assert (result["game_type"], result["game_type_code"]) == ("regular", "R")
    assert result["batters"][99] == {"hits": 2, "plate_appearances": 5}
    assert result["batters"][100]["plate_appearances"] == 0
    assert 101 not in result["batters"]
    feed["gameData"]["status"]["abstractGameState"] = "Live"
    assert validation.fetch_final_game(123)["status"] == "not_final"


def test_reconciliation_uses_official_horizon_selects_versions_and_pairs_variants(monkeypatch, tmp_path):
    projection = dict(zip(validation.BUCKET_FIELDS, (0.2, 0.3, 0.5)))
    projection.update(hits=1.5, plate_appearances=4.5)
    variant = {**projection, **dict(zip(validation.BUCKET_FIELDS, (0.45, 0.4, 0.15)))}
    variant.update(hits=0.8, plate_appearances=3.0)

    def write(name, issued, *, player=99, version="2.2", game=123, legacy=False, cutoff="2025-07-01"):
        payload = {"player": {"id": player}, "projection": projection,
                   "model": {"version": version, "generated_at": issued, "data_cutoff_exclusive": cutoff},
                   "game_context": {"game": {"id": game, "start_time": "2025-07-01T19:15:00Z", "status_code": "Preview"}}}
        if not legacy:
            payload["forecast_archive"] = {"kind": "pregame", "issued_at": issued,
                                           "variants": {"without_lineup": variant, "without_bullpen": {"hits": 5}}}
        atomic_json(tmp_path / "forecasts" / f"{name}.json", payload)

    write("too-old", "2025-06-30T16:00:00Z")
    write("earlier", "2025-07-01T17:00:00Z")
    write("selected", "2025-07-01T18:25:00Z")
    write("too-close", "2025-07-01T18:35:00Z")
    write("after-actual-first-pitch", "2025-07-01T19:05:00Z")
    write("future-input", "2025-07-01T18:29:00Z", cutoff="2025-07-02")
    write("legacy", "2025-07-01T18:27:00Z", version="2.1", legacy=True)
    write("no-pa", "2025-07-01T18:20:00Z", player=100)
    write("missing-line", "2025-07-01T18:20:00Z", player=101)
    write("pending", "2025-07-01T18:20:00Z", game=456)
    atomic_json(tmp_path / "forecasts" / "profile.json", {"game_context": None})
    (tmp_path / "forecasts" / "damaged.json").write_text("{", encoding="utf-8")
    calls = []

    def final(game):
        calls.append(game)
        if game == 456:
            return {"status": "not_final"}
        return {"status": "final", "first_pitch": datetime(2025, 7, 1, 19, tzinfo=timezone.utc),
                "official_date": date(2025, 7, 1), "game_type": "regular", "source": {"provider": "MLB Stats API"},
                "batters": {99: {"hits": 3, "plate_appearances": 5}, 100: {"hits": 0, "plate_appearances": 0}}}

    monkeypatch.setattr(validation, "fetch_final_game", final)
    progress = []
    report = validation.evaluate_archived(tmp_path, progress=lambda done, total: progress.append((done, total)))
    assert report["predictions"] == 2
    assert calls == [123, 456]
    assert progress[-1] == (5, 5)
    assert [row["archive_id"] for row in report["observations"]] == ["legacy", "selected"]
    assert report["excluded_archives"] == {"after_actual_cutoff": 2, "invalid_archive": 1, "missing_batting_line": 1,
                                           "no_plate_appearances": 1, "not_final": 1, "outside_horizon": 2,
                                           "profile_only": 1, "superseded": 1}
    modern = report["by_model_version"]["2.2"]
    comparison = modern["factor_comparisons"]["lineup"]
    assert comparison["paired_predictions"] == 1
    assert comparison["delta_with_minus_without"]["ranked_probability_score"] < 0
    assert modern["factor_comparisons"]["bullpen"]["paired_predictions"] == 0
    assert report["by_model_version"]["2.1"]["factor_comparisons"]["lineup"]["paired_predictions"] == 0


def test_archive_uses_same_pregame_inputs_and_keeps_original_response(history_data, tmp_path):
    start = datetime.now(timezone.utc) + timedelta(days=1)
    cutoff = start.date()
    context = {"game": {"id": 123, "start_time": start.isoformat(), "status_code": "Preview"}}
    player, opponent = {"id": 99, "name": "Bateador"}, {"id": 10, "name": "Abridor"}
    inputs = {"lineup_confirmed": True, "batting_order": 1, "team_plate_appearances": [61, 61],
              "starter_batters_faced": [25, 26], "bullpen": {"totals": {
                  "battersFaced": 800, "hits": 143, "homeRuns": 16, "baseOnBalls": 61, "strikeOuts": 211,
              }}}
    full = build_projection(history_data, player, 40, 1095, cutoff, history_data, opponent, context, inputs)
    original_inputs, original_full = deepcopy(inputs), deepcopy(full)
    path = validation.archive_forecast(tmp_path, full, history_data, games=40, lookback_days=1095, cutoff=cutoff,
                                       pitcher_stats=history_data, opponent_pitcher=opponent, inputs=inputs)
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert inputs == original_inputs and full == original_full
    assert stored["projection"] == full["projection"]
    variants = stored["forecast_archive"]["variants"]
    assert set(variants) == {"without_lineup", "without_starter", "without_bullpen"}
    assert variants["without_lineup"]["plate_appearances"] < full["projection"]["plate_appearances"]
    assert variants["without_bullpen"]["plate_appearances"] == full["projection"]["plate_appearances"]
    assert variants["without_bullpen"]["hits"] != full["projection"]["hits"]
