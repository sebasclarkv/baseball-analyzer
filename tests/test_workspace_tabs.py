"""Exercise workspace navigation, independent calendars and live refresh isolation."""
from copy import deepcopy
import re

import pytest

from baseball_analyzer_v2 import web
from test_api_sources import context_payload
from test_browser import browser, live_app
from test_live_browser import snapshot


@pytest.mark.parametrize("viewport", [{"width": 390, "height": 844}, {"width": 1440, "height": 1000}])
def test_workspace_tabs_keyboard_and_history(live_app, browser, viewport):
    from playwright.sync_api import expect

    context = browser.new_context(viewport=viewport, reduced_motion="reduce")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(live_app)
    prep = page.get_by_role("tab", name="Previa", exact=True)
    live = page.get_by_role("tab", name="En vivo", exact=True)
    prep_panel = page.locator("#workspace-prep")
    live_panel = page.locator("#workspace-live")
    replay = page.locator("#try-live-replay")

    expect(prep).to_have_attribute("aria-controls", "workspace-prep")
    expect(live).to_have_attribute("aria-controls", "workspace-live")
    expect(prep).to_have_attribute("aria-selected", "true")
    expect(live).to_have_attribute("tabindex", "-1")
    expect(prep_panel).to_be_visible()
    expect(live_panel).to_be_hidden()
    expect(replay).to_be_hidden()

    prep.focus()
    page.keyboard.press("ArrowRight")
    expect(live).to_be_focused()
    expect(live).to_have_attribute("aria-selected", "true")
    expect(prep).to_have_attribute("tabindex", "-1")
    expect(prep_panel).to_be_hidden()
    expect(live_panel).to_be_visible()
    expect(replay).to_be_visible()
    expect(page).to_have_url(re.compile(r"#en-vivo$"))

    page.keyboard.press("Home")
    expect(prep).to_be_focused()
    expect(prep_panel).to_be_visible()
    expect(page).to_have_url(re.compile(r"#previa$"))
    page.keyboard.press("End")
    expect(live).to_be_focused()
    page.keyboard.press("ArrowLeft")
    expect(prep).to_be_focused()

    live.click()
    expect(page).to_have_url(re.compile(r"#en-vivo$"))
    page.go_back()
    expect(prep).to_have_attribute("aria-selected", "true")
    expect(prep_panel).to_be_visible()
    expect(page).to_have_url(re.compile(r"#previa$"))
    page.go_forward()
    expect(live).to_have_attribute("aria-selected", "true")
    expect(live_panel).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")

    direct = context.new_page()
    direct.on("pageerror", lambda error: errors.append(str(error)))
    direct.goto(live_app + "/#en-vivo")
    expect(direct.get_by_role("tabpanel", name="En vivo")).to_be_visible()
    expect(direct.locator("#workspace-prep")).to_be_hidden()
    expect(direct.locator("#try-live-replay")).to_be_visible()
    assert not errors, errors
    context.close()


def test_live_calendar_selects_the_requested_date_and_leaves_pregame_untouched(
    live_app, context_payload, browser, monkeypatch,
):
    from playwright.sync_api import expect

    previous = "2025-07-01"
    today_game = context_payload["game"]
    past_game = deepcopy(today_game)
    past_game.update(id=321, start_time=previous + "T20:00:00Z", status="Final", status_code="Final")
    past_game["away"].update(name="Yankees de Nueva York", abbreviation="NYY")
    past_game["home"].update(name="Azulejos de Toronto", abbreviation="TOR")
    queried_dates = []

    def schedule(day):
        day = day.isoformat()
        queried_dates.append(day)
        games = [past_game] if day == previous else [today_game]
        return {"date": day, "games": games, "source": context_payload["source"], "warnings": []}

    monkeypatch.setattr(web, "schedule", schedule)
    context = browser.new_context(viewport={"width": 390, "height": 844}, service_workers="block")
    page = context.new_page()
    page.route("**/api/games/321/live", lambda route: route.fulfill(json=snapshot(321, revision="after", phase="final")))
    page.goto(live_app)
    pregame_date = page.locator("#game-date").input_value()
    expect(page.locator("#game-list")).to_contain_text("Visitante")

    page.get_by_role("tab", name="En vivo", exact=True).click()
    live_calendar = page.locator("#live-game-list")
    expect(page.locator("#live-picker-status")).to_contain_text("No hay partidos en curso ni finalizados")
    expect(live_calendar.get_by_role("button")).to_have_count(0)
    page.locator("#live-game-date").fill(previous)
    expect(live_calendar).to_contain_text("Yankees de Nueva York")
    live_calendar.get_by_role("button", name=re.compile("Yankees de Nueva York")).click()
    expect(page.locator("#live-mode")).to_have_text("Final")
    expect(page.locator("#live-home-team .live-runs")).to_have_text("4")
    assert previous in queried_dates

    page.get_by_role("tab", name="Previa", exact=True).click()
    expect(page.locator("#game-date")).to_have_value(pregame_date)
    expect(page.locator("#game-list")).to_contain_text("Visitante")
    expect(page.locator("#live-panel")).to_be_hidden()
    page.get_by_role("tab", name="En vivo", exact=True).click()
    expect(page.locator("#live-game-date")).to_have_value(previous)
    expect(page.locator("#live-mode")).to_have_text("Final")
    context.close()


def test_live_handoff_preserves_pregame_selection_and_pauses_hidden_polling(
    live_app, context_payload, browser, monkeypatch,
):
    from playwright.sync_api import expect

    prep_game = context_payload["game"]
    live_game = deepcopy(prep_game)
    live_game.update(id=321, status="In Progress", status_code="Live")
    live_game["away"].update(name="Yankees de Nueva York", abbreviation="NYY")
    live_game["home"].update(name="Azulejos de Toronto", abbreviation="TOR")
    monkeypatch.setattr(web, "schedule", lambda day: {"date": day.isoformat(), "games": [prep_game, live_game],
        "source": context_payload["source"], "warnings": []})
    revision = "before"
    requests = []

    def live_response(route):
        requests.append(revision)
        route.fulfill(json=snapshot(321, revision=revision))

    context = browser.new_context(viewport={"width": 390, "height": 844}, reduced_motion="reduce", service_workers="block")
    page = context.new_page()
    page.route("**/api/games/321/live", live_response)
    page.goto(live_app)
    games = page.locator("#game-list")
    games.get_by_role("button", name=re.compile("Visitante")).press("Enter")
    page.locator("#team-roster .roster-player").first.press("Enter")
    expect(page.locator("#player-query")).to_have_value("Bateador 1")
    page.clock.install()
    games.get_by_role("button", name=re.compile("Yankees de Nueva York")).press("Enter")
    expect(page.get_by_role("tab", name="En vivo", exact=True)).to_have_attribute("aria-selected", "true")
    expect(page).to_have_url(re.compile(r"#en-vivo$"))
    runs = page.locator("#live-home-team .live-runs")
    expect(runs).to_have_text("1")

    page.get_by_role("tab", name="Previa", exact=True).click()
    expect(page.locator("#player-query")).to_have_value("Bateador 1")
    expect(page.locator("#game-context-title")).to_contain_text("Visitante")
    expect(page.locator("#live-panel")).to_be_hidden()
    revision = "after"
    page.clock.fast_forward(30000)
    assert requests == ["before"]
    expect(runs).to_have_text("1")

    page.get_by_role("tab", name="En vivo", exact=True).click()
    expect(runs).to_have_text("4")
    page.locator("#live-pause").click()
    expect(page.locator("#live-status")).to_contain_text("Actualizaciones pausadas")
    request_count = len(requests)
    revision = "before"
    page.get_by_role("tab", name="Previa", exact=True).click()
    page.get_by_role("tab", name="En vivo", exact=True).click()
    page.clock.fast_forward(30000)
    assert len(requests) == request_count
    expect(runs).to_have_text("4")
    context.close()


def test_return_to_live_retries_an_initial_feed_failure_while_hidden(live_app, context_payload, browser):
    from playwright.sync_api import expect

    game = context_payload["game"]
    game.update(status="In Progress", status_code="Live")
    pending = []

    def live_response(route):
        if not pending:
            pending.append(route)
        else:
            route.fulfill(json=snapshot(game["id"], revision="after"))

    context = browser.new_context(service_workers="block", reduced_motion="reduce")
    page = context.new_page()
    page.route(f"**/api/games/{game['id']}/live", live_response)
    page.goto(live_app)
    with page.expect_request(f"**/api/games/{game['id']}/live"):
        page.locator("#game-list .game-card").click()
    page.get_by_role("tab", name="Previa", exact=True).click()
    pending[0].fulfill(status=503, json={"detail": "MLB temporalmente no disponible."})
    expect(page.locator("#live-status")).to_contain_text("MLB temporalmente no disponible")
    expect(page.locator("#live-panel")).to_be_hidden()
    page.get_by_role("tab", name="En vivo", exact=True).click()
    expect(page.locator("#live-home-team .live-runs")).to_have_text("4")
    context.close()
