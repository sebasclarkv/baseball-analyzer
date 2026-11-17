"""Check that in-game state and estimates stay attached to their actual snapshot."""
import re
from urllib.parse import parse_qs, urlsplit

from test_browser import browser, live_app
from test_api_sources import context_payload


def snapshot(game_id, *, revision, phase="live", replay=False):
    home_score = 1 if revision == "before" else 4
    home_probability = .421 if revision == "before" else .759
    batter = {"id": 99, "name": "Andrés Giménez", "hits": int(home_score == 4), "plate_appearances": 2}
    return {
        "game": {
            "id": game_id, "phase": phase,
            "away": {"name": "New York Yankees", "abbreviation": "NYY", "score": 2, "lineup": []},
            "home": {"name": "Toronto Blue Jays", "abbreviation": "TOR", "score": home_score, "lineup": [batter]},
        },
        "inning": 4, "half": "bottom", "inning_state": "playing", "batting_side": "home",
        "count": {"balls": int(revision == "before"), "strikes": 0, "outs": 2},
        "bases": {"first": {"id": 123, "name": "Myles Straw"} if revision == "before" else None, "second": None, "third": None},
        "batter": batter, "pitcher": {"id": 101, "name": "Max Fried"}, "plate_appearance_active": True,
        "snapshot_id": revision, "snapshot_at": "2025-07-01T20:45:40Z", "replay": replay,
        "win_probability": {"home": home_probability, "away": 1 - home_probability,
                            "home_change": .338 if revision == "after" else -.015,
                            "away_change": -.338 if revision == "after" else .015,
                            "play_at": "2025-07-01T20:44:00Z"},
        "last_play": {"description": "Home run de tres carreras" if revision == "after" else "Bateador llega a primera"},
        "source": {"name": "MLB Stats API", "stale": False, "fetched_at": "2025-07-01T20:45:42Z"},
    }


def estimate(revision):
    return {
        "snapshot_id": revision, "observed_at": "2025-07-01T20:45:40Z",
        "player": {"id": 99, "name": "Andrés Giménez"}, "pitcher": {"id": 101, "name": "Max Fried"},
        "estimate": {
            "status": "available", "next_pa": {"probability": .25, "scope": "current", "count_applied": True, "count": {"balls": 1, "strikes": 0}},
            "scenarios": [{"plate_appearances": 2, "probability_one_or_more_hits": .44}],
            "sample": {"batter_pa": 540},
        },
    }


def test_live_game_updates_and_estimate_stays_with_its_snapshot(live_app, context_payload, browser):
    from playwright.sync_api import expect

    game = context_payload["game"]
    game.update(status="In Progress", status_code="Live")
    state = {"phase": "live", "revision": "before", "failure": False}
    requests = []
    context = browser.new_context(viewport={"width": 390, "height": 844}, reduced_motion="reduce", service_workers="block")
    page = context.new_page()
    page.clock.install()

    def respond(route):
        request = route.request
        requests.append(request.method)
        if request.method == "POST":
            assert request.post_data_json == {"snapshot_id": "before"}
            route.fulfill(json=estimate("before"))
        elif state["failure"]:
            route.fulfill(status=502, json={"detail": "MLB dejó de responder."})
        else:
            route.fulfill(json=snapshot(game["id"], phase=state["phase"], revision=state["revision"]))

    page.route(re.compile(r"/api/games/\d+/live(?:/hitter)?(?:\?.*)?$"), respond)
    page.goto(live_app)
    page.locator("#game-list .game-card").click()
    expect(page.locator("#live-mode")).to_have_text("En vivo")
    expect(page.locator("#live-inning")).to_have_text("Baja de la 4ª")
    expect(page.locator("#live-bases-label")).to_contain_text("1ª Myles Straw")
    expect(page.locator("#live-win-grid")).to_contain_text("42.1%")
    expect(page.locator("#live-outs")).to_have_text("2")
    page.locator("#live-estimate").click()
    expect(page.locator("#live-estimate-result")).to_contain_text("25% de hit en este turno")
    expect(page.locator("#live-estimate-result")).to_contain_text("Incluye la cuenta 1–0")
    expect(page.locator("#live-player-totals")).to_contain_text("0 hits en 2 PA")

    state["revision"] = "after"
    page.clock.fast_forward(16000)
    expect(page.locator("#live-home-team .live-runs")).to_have_text("4")
    expect(page.locator("#live-win-grid")).to_contain_text("+33.8 pp")
    expect(page.locator("#live-estimate-status")).to_contain_text("Cálculo anterior")
    expect(page.locator("#live-player-totals")).to_contain_text("1 hit en 2 PA")

    page.locator("#live-pause").click()
    expect(page.locator("#live-status")).to_contain_text("Actualizaciones pausadas")
    request_count = len(requests)
    page.clock.fast_forward(20000)
    assert len(requests) == request_count
    state["failure"] = True
    page.locator("#live-refresh").click()
    expect(page.locator("#live-status")).to_contain_text("Se conserva el último dato")
    expect(page.locator("#live-home-team .live-runs")).to_have_text("4")
    expect(page.locator("#live-estimate")).to_be_disabled()

    state.update(failure=False, phase="final")
    page.locator("#live-refresh").click()
    expect(page.locator("#live-mode")).to_have_text("Final")
    expect(page.locator("#live-hitter-panel")).to_be_hidden()
    expect(page.locator("#live-pause")).to_be_hidden()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    context.close()


def test_historical_replay_loads_two_fixed_snapshots(live_app, browser):
    from playwright.sync_api import expect

    queries = []
    context = browser.new_context(viewport={"width": 390, "height": 844}, reduced_motion="reduce", service_workers="block")
    page = context.new_page()
    page.clock.install()

    def respond(route):
        query = parse_qs(urlsplit(route.request.url).query)
        moment = query.get("at", [""])[0]
        queries.append((route.request.method, moment))
        revision = "before" if moment == "2025-07-01T20:45:40Z" else "after"
        result = estimate(revision) if route.request.method == "POST" else snapshot(777283, revision=revision, replay=True)
        route.fulfill(json=result)

    page.route(re.compile(r"/api/games/777283/live(?:/hitter)?(?:\?.*)?$"), respond)
    page.goto(live_app)
    page.locator("#try-live-replay").click()
    expect(page.locator("#live-mode")).to_have_text("Repetición")
    expect(page.locator("#live-home-team .live-runs")).to_have_text("1")
    expect(page.locator("#live-pause")).to_be_hidden()
    page.get_by_role("button", name="Después del jonrón").click()
    expect(page.locator("#live-home-team .live-runs")).to_have_text("4")
    expect(page.get_by_role("button", name="Después del jonrón")).to_have_attribute("aria-pressed", "true")
    page.locator("#live-estimate").click()
    expect(page.locator("#live-estimate-result")).to_contain_text("25% de hit en este turno")
    page.clock.fast_forward(32000)
    assert queries == [("GET", "2025-07-01T20:45:40Z"), ("GET", "2025-07-01T20:47:10Z"), ("POST", "2025-07-01T20:47:10Z")]
    page.locator("#close-live-panel").click()
    expect(page.locator("#live-panel")).to_be_hidden()
    expect(page.locator("#try-live-replay")).to_be_focused()
    context.close()
