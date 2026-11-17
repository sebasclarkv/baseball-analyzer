"""Exercise MLB snapshots with completed and in-progress plate appearances."""
from copy import deepcopy
from datetime import datetime, timezone

import httpx
import pytest

from baseball_analyzer_v2 import live, sources, storage


def _player(player_id, name):
    return {"id": player_id, "fullName": name}


@pytest.fixture
def feed():
    starter, reliever = _player(20, "Abridor"), _player(21, "Relevista")
    batter, replaced = _player(31, "Bateador actual"), _player(30, "Bateador reemplazado")
    last_pa = {
        "about": {"atBatIndex": 10, "inning": 4, "halfInning": "bottom", "isComplete": True,
                  "endTime": "2025-07-01T20:40:00.940Z"},
        "result": {"description": "Carrera anotada.", "eventType": "double"},
    }
    turn = {"about": {"atBatIndex": 11, "inning": 4, "halfInning": "bottom", "isComplete": False,
                      "endTime": "2025-07-01T20:41:02.135Z"},
            "matchup": {"batter": batter, "pitcher": reliever, "batSide": {"code": "L"}, "pitchHand": {"code": "R"}},
            "result": {"description": "Resultado reservado hasta cerrar el turno."}}
    return {
        "gamePk": 123, "metaData": {"timeStamp": "20250701_204100"},
        "gameData": {"status": {"abstractGameState": "Live", "detailedState": "In Progress"},
                     "game": {"type": "R"}, "datetime": {"officialDate": "2025-07-01"},
                     "teams": {"away": {"id": 1, "name": "Visitante"}, "home": {"id": 2, "name": "Local"}},
                     "players": {"ID21": {"pitchHand": {"code": "R"}}}},
        "liveData": {
            "linescore": {"currentInning": 4, "inningState": "Bottom", "inningHalf": "Bottom",
                          "teams": {"away": {"runs": 2}, "home": {"runs": 1}},
                          "balls": 1, "strikes": 2, "outs": 1,
                          "offense": {"team": {"id": 2}, "batter": batter, "second": _player(37, "Corredor")},
                          "defense": {"team": {"id": 1}, "pitcher": reliever}},
            "boxscore": {"teams": {"away": {"pitchers": [20, 21], "players": {
                "ID20": {"person": starter}, "ID21": {"person": reliever, "stats": {"pitching": {"numberOfPitches": 4}}},
            }}, "home": {"battingOrder": [31], "players": {
                "ID30": {"person": replaced, "battingOrder": "100"},
                "ID31": {"person": batter, "battingOrder": "101", "stats": {"batting": {"hits": 1, "plateAppearances": 2}}},
            }}}},
            "plays": {"allPlays": [last_pa, turn], "currentPlay": turn},
        },
    }


def _probability(play, home):
    return {"atBatIndex": play["about"]["atBatIndex"], "about": play["about"],
            "playEndTime": play["about"]["endTime"], "homeTeamWinProbability": home,
            "awayTeamWinProbability": 100 - home, "homeTeamWinProbabilityAdded": -2.1}


def test_replay_anchors_win_probability_to_completed_play(monkeypatch, feed):
    plays = feed["liveData"]["plays"]["allPlays"]
    reported = [_probability(plays[0], 44.2), _probability(plays[1], 90)]
    requests = []

    def load(url, params, **kwargs):
        requests.append((url, params, kwargs))
        return (reported if kwargs.get("response_type") is list else feed), {"stale": False, "fetched_at": "current"}

    monkeypatch.setattr(live, "public_json", load)
    snapshot = live.game_live(123, datetime(2025, 7, 1, 20, 41, 10, tzinfo=timezone.utc))
    assert snapshot["replay"] is True
    assert snapshot["snapshot_at"] == "2025-07-01T20:41:02.135000+00:00"
    assert snapshot["game"]["phase"] == "live"
    assert snapshot["plate_appearance_active"] is True
    assert snapshot["inning"] == 4 and snapshot["half"] == "bottom"
    assert snapshot["count"] == {"balls": 1, "strikes": 2, "outs": 1}
    assert snapshot["bases"]["second"]["id"] == 37
    assert snapshot["batter"] == {"id": 31, "name": "Bateador actual", "hand": "L"}
    assert snapshot["pitcher"]["id"] == 21
    assert snapshot["pitcher"]["pitches"] == 4
    assert snapshot["game"]["away"]["starter"]["id"] == 20
    assert snapshot["game"]["away"]["current_pitcher"]["id"] == 21
    lineup = snapshot["game"]["home"]["lineup"]
    assert [player["id"] for player in lineup] == [31]
    assert (lineup[0]["hits"], lineup[0]["plate_appearances"]) == (1, 2)
    assert snapshot["win_probability"]["home"] == pytest.approx(.442)
    assert snapshot["win_probability"]["away_change"] == pytest.approx(.021)
    assert snapshot["last_play"]["description"] == "Carrera anotada."
    assert all(params == {"timecode": "20250701_204110"} for _, params, _ in requests)


def test_snapshot_id_ignores_fetch_time_but_not_count(monkeypatch, feed):
    refreshes = iter(("first", "second", "third"))

    def load(url, params, **kwargs):
        if kwargs.get("response_type") is list:
            raise sources.SourceUnavailable("provider unavailable")
        return feed, {"stale": True, "fetched_at": next(refreshes)}

    monkeypatch.setattr(live, "public_json", load)
    before = live.game_live(123)
    feed["metaData"]["timeStamp"] = "20250701_204105"
    after_poll = live.game_live(123)
    feed["liveData"]["linescore"]["balls"] = 2
    after_ball = live.game_live(123)
    assert before["snapshot_id"] == after_poll["snapshot_id"] != after_ball["snapshot_id"]
    assert after_poll["source"]["stale"] is True
    assert after_poll["win_probability"] is None
    assert len(after_poll["warnings"]) == 2


@pytest.mark.parametrize("detailed, inning_state, phase", [
    ("Delayed", "Bottom", "paused"), ("In Progress", "End", "live"), ("Final", "Bottom", "final"),
])
def test_closed_or_paused_turn_never_reuses_count(monkeypatch, feed, detailed, inning_state, phase):
    feed["gameData"]["status"]["detailedState"] = detailed
    if detailed == "Final":
        feed["gameData"]["status"]["abstractGameState"] = "Final"
    feed["liveData"]["linescore"]["inningState"] = inning_state
    monkeypatch.setattr(live, "public_json", lambda *_a, **kw: ([] if kw.get("response_type") is list else feed,
                                                               {"stale": False, "fetched_at": "now"}))
    snapshot = live.game_live(123)
    assert snapshot["game"]["phase"] == phase
    assert snapshot["plate_appearance_active"] is False
    assert snapshot["batter"] is None
    assert snapshot["count"]["balls"] is None and snapshot["count"]["strikes"] is None


def test_replay_rejects_feed_with_later_plays(monkeypatch, feed):
    late = deepcopy(feed["liveData"]["plays"]["allPlays"][0])
    late["about"]["endTime"] = "2025-07-01T20:41:12Z"
    feed["liveData"]["plays"]["allPlays"].append(late)
    monkeypatch.setattr(live, "public_json", lambda *_a, **_kw: (feed, {"stale": False, "fetched_at": "now"}))
    with pytest.raises(sources.SourceUnavailable, match="imagen verificable"):
        live.game_live(123, datetime(2025, 7, 1, 20, 41, 10, tzinfo=timezone.utc))


def test_public_json_arrays_use_explicit_shape_and_stale_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "CACHE_ROOT", tmp_path)
    client = httpx.Client
    calls = [httpx.Response(200, json=[{"homeTeamWinProbability": 50}]), httpx.Response(503), httpx.Response(503)]
    monkeypatch.setattr(sources.httpx, "Client", lambda **_kw: client(transport=httpx.MockTransport(lambda _req: calls.pop(0))))
    url = "https://statsapi.mlb.com/api/v1/game/123/winProbability"
    data, fresh = sources.public_json(url, ttl=0, response_type=list)
    assert data == [{"homeTeamWinProbability": 50}] and fresh["stale"] is False
    cached, stale = sources.public_json(url, ttl=0, response_type=list)
    assert cached == data and stale["stale"] is True
    with pytest.raises(sources.SourceUnavailable):
        sources.public_json(url, ttl=0)
