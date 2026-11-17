from datetime import date, datetime, timezone

from fastapi.testclient import TestClient

from baseball_analyzer_v2 import web


def test_live_hitter_uses_only_official_matchup_and_count(monkeypatch):
    snapshot = {
        "snapshot_id": "third-inning-at-bat",
        "snapshot_at": "2025-07-01T20:02:10Z",
        "source": {"stale": False},
        "game": {
            "phase": "live", "official_date": "2025-07-01", "game_type": "R",
            "away": {"lineup": [{"id": 10, "name": "Primer bate", "hits": 1, "plate_appearances": 2}],
                     "current_pitcher": {"id": 30, "name": "Pitcher visitante"}},
            "home": {"lineup": [{"id": 20, "name": "Bate local", "hits": 0, "plate_appearances": 1}],
                     "current_pitcher": {"id": 40, "name": "Pitcher anterior"}},
        },
        "batting_side": "away", "batter": {"id": 10, "name": "Primer bate"},
        "pitcher": {"id": 41, "name": "Relevista activo"}, "plate_appearance_active": True,
        "count": {"balls": 2, "strikes": 1, "outs": 1},
    }
    snapshot_requests, estimates = [], []

    def feed(game_id, *, as_of, include_win):
        snapshot_requests.append((game_id, as_of, include_win))
        return snapshot

    def estimate(**inputs):
        estimates.append(inputs)
        return {"status": "available"}

    monkeypatch.setattr(web, "game_live", feed)
    monkeypatch.setattr(web, "project_live_hitter", estimate)
    client = TestClient(web.app)
    query = {"at": "2025-07-01T20:02:10Z"}
    board = client.get("/api/games/123/live", params=query)
    assert board.json() == snapshot
    assert board.headers["Cache-Control"] == "no-store"
    url = "/api/games/123/live/hitter"

    turn = client.post(url, params=query, json={"snapshot_id": snapshot["snapshot_id"]})
    assert turn.status_code == 200, turn.text
    assert turn.json()["hits"] == 1
    assert turn.json()["plate_appearances"] == 2
    assert turn.json()["pitcher_reference"] == "current"
    assert estimates[-1]["pitcher"]["id"] == 41
    assert estimates[-1]["official_date"] == date(2025, 7, 1)
    assert estimates[-1]["count"]["strikes"] == 1
    instant = datetime(2025, 7, 1, 20, 2, 10, tzinfo=timezone.utc)
    assert snapshot_requests == [(123, instant, True), (123, instant, False)]

    next_batter = client.post(url, json={"player_id": 20})
    assert next_batter.status_code == 200, next_batter.text
    assert next_batter.json()["pitcher_reference"] == "last_known"
    assert estimates[-1]["pitcher"]["id"] == 30
    assert estimates[-1]["count"] is None

    assert client.post(url, json={"player_id": 99}).status_code == 422
    assert client.post(url, json={"snapshot_id": "previous-play"}).status_code == 409
    snapshot["source"]["stale"] = True
    assert client.post(url, json={}).status_code == 409
    snapshot["source"]["stale"] = False
    snapshot["game"]["phase"] = "final"
    assert client.post(url, json={}).status_code == 409
    assert len(estimates) == 2
