from datetime import date, datetime, timedelta, timezone
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from baseball_analyzer_v2 import sources, storage, web


@pytest.fixture
def context_payload():
    start = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    own = [{"id": 99 + n, "name": f"Bateador {n + 1}", "position": "OF", "batting_order": n + 1} for n in range(9)]
    return {"game": {"id": 123, "start_time": start, "status": "Scheduled", "status_code": "Preview", "venue": {"id": 1, "name": "Estadio de prueba"},
                     "away": {"id": 1, "name": "Visitante", "abbreviation": "VIS", "score": None, "probable_pitcher": None},
                     "home": {"id": 2, "name": "Local", "abbreviation": "LOC", "score": None, "probable_pitcher": None}},
            "teams": {"away": {"id": 1, "name": "Visitante", "lineup_status": "confirmed", "lineup": own, "roster": own},
                      "home": {"id": 2, "name": "Local", "lineup_status": "pending", "lineup": [], "roster": []}},
            "weather": {"status": "unavailable", "roof": "desconocido", "applied_to_projection": False, "message": "Sin pronóstico de prueba"},
            "source": {"name": "MLB Stats API", "fetched_at": datetime.now(timezone.utc).isoformat(), "stale": False}, "warnings": [], "as_of": datetime.now(timezone.utc).isoformat()}


def test_schedule_api_failure_is_actionable(monkeypatch):
    def fail(day):
        raise sources.SourceUnavailable("offline")
    monkeypatch.setattr(web, "schedule", fail)
    client = TestClient(web.app)
    assert client.get("/api/games?date=bad-date").status_code == 422
    response = client.get("/api/games?date=2025-06-01")
    assert response.status_code == 502
    assert "sin seleccionar partido" in response.json()["detail"]
    assert client.get("/").status_code == 200


def test_api_projection_contract_and_archive(monkeypatch, tmp_path, history_data, context_payload):
    monkeypatch.setattr(web, "CACHE_ROOT", tmp_path)
    monkeypatch.setattr(web, "fetch_batter_statcast", lambda *args: history_data)
    monkeypatch.setattr(web, "game_context", lambda *a: context_payload)
    monkeypatch.setattr(web, "matchup_inputs", lambda *a: {"lineup_confirmed": True, "batting_order": 1, "team_plate_appearances": [37]})
    response = TestClient(web.app).post("/api/analyze", json={"player_id": 99, "player_name": "Prueba", "game_id": 123})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["game_context"]["game"]["id"] == 123
    assert all(k in result["projection"] for k in ("probability_zero_hits", "probability_exactly_one_hit", "probability_two_or_more_hits"))
    assert len(list((tmp_path / "forecasts").glob("*.json"))) == 1
    assert TestClient(web.app).get("/api/health").json()["app"] == "diamond-iq"


def test_started_game_cannot_be_presented_as_pregame(monkeypatch, context_payload):
    context_payload["game"]["status_code"] = "Final"
    monkeypatch.setattr(web, "game_context", lambda *a: context_payload)
    response = TestClient(web.app).post("/api/analyze", json={"player_id": 99, "player_name": "Prueba", "game_id": 123})
    assert response.status_code == 422
    assert "ya inició" in response.json()["detail"]


def test_official_matchup_cannot_use_an_unpublished_starter(monkeypatch, context_payload):
    monkeypatch.setattr(web, "game_context", lambda *args: context_payload)
    monkeypatch.setattr(web, "matchup_inputs", lambda *args: {"starter": None})
    response = TestClient(web.app).post("/api/analyze", json={"player_id": 99, "player_name": "Prueba",
                                                            "game_id": 123, "opponent_pitcher_id": 10})
    assert response.status_code == 422
    assert "no coincide" in response.json()["detail"]


def test_hypothetical_starter_uses_opposing_roster_without_archiving(monkeypatch, tmp_path, history_data, context_payload):
    opponent = context_payload["teams"]["home"]
    opponent["roster"] = [{"id": 10, "name": "Abridor probable", "is_pitcher": True},
                          {"id": 11, "name": "Relevista alternativo", "is_pitcher": True}]
    official = {"id": 10, "name": "Abridor probable"}
    context_payload["game"]["home"]["probable_pitcher"] = official
    monkeypatch.setattr(web, "CACHE_ROOT", tmp_path)
    monkeypatch.setattr(web, "game_context", lambda *args: context_payload)
    monkeypatch.setattr(web, "fetch_batter_statcast", lambda *args: history_data)
    monkeypatch.setattr(sources, "public_json", lambda *args, **kwargs: ({}, {"stale": False}))
    monkeypatch.setattr(sources, "game_logs", lambda *args: ([], False))
    fetched = []

    def pitcher_history(pitcher_id, *args):
        fetched.append(pitcher_id)
        return history_data.assign(pitcher=pitcher_id)

    monkeypatch.setattr(web, "fetch_pitcher_statcast", pitcher_history)
    request = {"player_id": 99, "player_name": "Bateador", "game_id": 123, "scenario_pitcher_id": 11}
    client = TestClient(web.app)
    response = client.post("/api/scenario", json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert fetched == [11]
    assert result["scenario"]["pitcher"] == {"id": 11, "name": "Relevista alternativo"}
    assert result["scenario"]["official_starter"] == official
    assert result["game_context"]["game"]["home"]["probable_pitcher"] == official
    assert result["game_context"]["matchup"]["starter"]["id"] == 11
    assert not list((tmp_path / "forecasts").glob("*.json"))
    assert client.post("/api/scenario", json={**request, "scenario_pitcher_id": 12}).status_code == 422
    assert client.post("/api/scenario", json={**request, "game_id": None}).status_code == 422


def test_http_cache_declares_staleness(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "CACHE_ROOT", tmp_path)
    real_client = httpx.Client
    def respond(request):
        return httpx.Response(200, json={"ok": True})
    monkeypatch.setattr(sources.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(respond)))
    url = "https://statsapi.mlb.com/test-fixture"
    result, freshness = sources.public_json(url)
    assert result == {"ok": True} and not freshness["stale"]
    path = storage.json_cache_path(url + "?")
    cached = json.loads(path.read_text())
    cached["timestamp"] = 0
    path.write_text(json.dumps(cached))
    def denied(request):
        return httpx.Response(503)
    monkeypatch.setattr(sources.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(denied)))
    result, freshness = sources.public_json(url)
    assert result == {"ok": True} and freshness["stale"]
    assert freshness["fetched_at"] == cached["fetched_at"]


def test_pitching_logs_exclude_target_and_future(monkeypatch):
    data = {"stats": [{"splits": [{"date": d, "stat": {"battersFaced": n}} for d, n in [("2025-06-01", 20), ("2025-06-02", 90), ("2025-06-03", 99)]]}]}
    monkeypatch.setattr(sources, "public_json", lambda *a, **kw: (data if kw.get("unused") else data, {"stale": False}))
    rows, stale = sources.game_logs("people", 99, "pitching", date(2025, 6, 2))
    assert rows and all(r["date"] < "2025-06-02" for r in rows)
    assert all(r["battersFaced"] == 20 for r in rows)


def test_unknown_roster_player_is_rejected(context_payload):
    with pytest.raises(ValueError, match="no pertenece"):
        sources.matchup_inputs(context_payload, 999999)


def test_missing_coordinates_are_not_replaced_with_weather():
    result = sources.weather_for({"name": "Unknown"}, "2027-01-01T20:00:00Z")
    assert result["status"] == "unavailable" and result["temperature_c"] is None
    assert result["applied_to_projection"] is False


def test_completed_job_has_result(monkeypatch):
    monkeypatch.setattr(web, "_evaluation", lambda request, progress: {"predictions": 5})
    client = TestClient(web.app)
    job = client.post("/api/backtest/jobs", json={"player_id": 99, "player_name": "Prueba"})
    assert job.status_code == 202
    for _ in range(100):
        state = client.get("/api/jobs/" + job.json()["id"]).json()
        if state["status"] == "complete":
            break
        time.sleep(.01)
    assert state["result"]["predictions"] == 5


def test_prospective_job_writes_report_for_local_archive(monkeypatch, tmp_path):
    monkeypatch.setattr(web, "CACHE_ROOT", tmp_path)
    client = TestClient(web.app)
    report_url = "/api/evaluation/prospective"
    assert client.get(report_url).json()["status"] == "not_run"
    response = client.post(report_url + "/jobs")
    assert response.status_code == 202
    job_url = "/api/jobs/" + response.json()["id"]
    for _ in range(100):
        job = client.get(job_url).json()
        if job["status"] in {"complete", "error"}:
            break
        time.sleep(.01)
    assert job["status"] == "complete", job
    assert client.get(report_url + "?summary=true").json() == job["result"]
    assert client.get(report_url).json()["observations"] == []
    assert job["result"]["predictions"] == 0


def test_service_worker_can_control_root():
    response = TestClient(web.app).get("/service-worker.js")
    assert response.status_code == 200
    assert response.headers["service-worker-allowed"] == "/"
    assert response.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("players,expected", [(9, "confirmed"), (8, "pending")])
def test_mlb_feed_only_publishes_complete_lineup(monkeypatch, players, expected):
    lineup = list(range(100, 100 + players))
    team_box = {"battingOrder": lineup, "players": {f"ID{p}": {"person": {"id": p, "fullName": f"Jugador {p}"}, "position": {"abbreviation": "OF"}} for p in lineup}}
    feed = {"gameData": {"datetime": {"dateTime": "2027-04-01T20:00:00Z", "officialDate": "2027-04-01"}, "game": {"type": "R"},
                        "teams": {"home": {"id": 1, "name": "Local"}, "away": {"id": 2, "name": "Visitante"}},
                        "venue": {"name": "Estadio"}, "status": {"abstractGameState": "Preview"}},
            "liveData": {"boxscore": {"teams": {"home": team_box, "away": team_box}}}}
    monkeypatch.setattr(sources, "public_json", lambda *a, **kw: (feed, {"stale": False, "fetched_at": "2027-04-01T12:00:00Z"}))
    monkeypatch.setattr(sources, "_roster", lambda *a: ([], {"stale": False}))
    monkeypatch.setattr(sources, "weather_for", lambda *a: {"status": "unavailable"})
    result = sources.game_context(123)
    assert result["teams"]["home"]["lineup_status"] == expected
    assert len(result["teams"]["home"]["lineup"]) == (9 if players == 9 else 0)
    assert result["game"]["home"]["probable_pitcher"] is None
    assert result["game"]["game_type"] == "R"


def test_open_meteo_selects_target_hour_in_utc(monkeypatch):
    target = datetime.now(timezone.utc).date() + timedelta(days=1)
    times = [f"{target.isoformat()}T{hour:02d}:00" for hour in range(24)]
    data = {"hourly": {"time": times, "temperature_2m": list(range(24)), "wind_speed_10m": [10] * 24, "wind_direction_10m": [90] * 24}}
    monkeypatch.setattr(sources, "public_json", lambda *a, **kw: (data, {"stale": False, "fetched_at": "fixture"}))
    venue = {"location": {"defaultCoordinates": {"latitude": 40, "longitude": -73}}, "fieldInfo": {"roofType": "Open"}}
    result = sources.weather_for(venue, target.isoformat() + "T14:35:00Z")
    assert result["temperature_c"] == 14
    assert result["wind_kmh"] == 10
    assert result["forecast_for"].endswith("14:00Z")
    assert result["applied_to_projection"] is False


def test_pregame_cutoff_uses_mlb_official_date(monkeypatch, tmp_path, history_data, context_payload):
    official_day = datetime.now(timezone.utc).date()
    context_payload["game"]["official_date"] = official_day.isoformat()
    context_payload["game"]["start_time"] = (datetime.combine(official_day + timedelta(days=1), datetime.min.time(), timezone.utc) + timedelta(hours=3)).isoformat()
    monkeypatch.setattr(web, "CACHE_ROOT", tmp_path)
    monkeypatch.setattr(web, "fetch_batter_statcast", lambda *args: history_data)
    monkeypatch.setattr(web, "game_context", lambda *a: context_payload)
    monkeypatch.setattr(web, "matchup_inputs", lambda *a: {})
    response = TestClient(web.app).post("/api/analyze", json={"player_id": 99, "player_name": "Prueba", "game_id": 123})
    assert response.status_code == 200
    assert response.json()["model"]["data_cutoff_exclusive"] == official_day.isoformat()
