"""Accessible scorecard, same-team comparison and hypothetical starter flow."""
import pandas as pd
import pytest

from baseball_analyzer_v2 import web
from test_api_sources import context_payload
from test_browser import browser, live_app


@pytest.fixture
def matchup_app(live_app, context_payload, monkeypatch):
    # Give the fixture a completed inning-by-inning line score and a real roster
    # shape so the page exercises the same inputs as MLB's context endpoint.
    away_runs = [0, 1, 0, 0, 0, 0, 0, 1, 0]
    home_runs = [0, 0, 1, 0, 0, 0, 0, 1, 0]
    context_payload["innings"] = [
        {"number": inning + 1, "away": away_runs[inning], "home": home_runs[inning]}
        for inning in range(9)
    ]
    context_payload["game"]["away"]["score"] = sum(away_runs)
    context_payload["game"]["home"]["score"] = sum(home_runs)
    official = {"id": 200, "name": "Abridor oficial"}
    alternate = {"id": 201, "name": "Abridor alternativo"}
    context_payload["game"]["home"]["probable_pitcher"] = official
    context_payload["teams"]["home"]["roster"] = [
        {**official, "position": "P", "is_pitcher": True},
        {**alternate, "position": "P", "is_pitcher": True},
    ]

    def inputs(context, player_id, *, scenario_pitcher_id=None):
        starter = alternate if scenario_pitcher_id == alternate["id"] else official
        return {
            "own_side": "away",
            "batting_order": player_id - 98,
            "lineup_confirmed": True,
            "starter": starter,
            "starter_batters_faced": [],
            "team_plate_appearances": [37, 38, 40, 33],
            "bullpen": None,
            "warnings": [],
        }

    monkeypatch.setattr(web, "matchup_inputs", inputs)
    monkeypatch.setattr(web, "fetch_pitcher_statcast", lambda *args: pd.DataFrame())
    return live_app


def test_scorecard_comparison_and_hypothetical_starter(matchup_app, browser):
    from playwright.sync_api import expect

    context = browser.new_context(
        viewport={"width": 390, "height": 844},
        reduced_motion="reduce",
        service_workers="block",
    )
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(matchup_app)
    page.locator("#game-list .game-card").click()
    expect(page.locator("#matchup-tools")).to_be_visible()
    expect(page.locator("#inning-scorecard tbody tr")).to_have_count(2)
    expect(page.locator("#inning-scorecard thead th")).to_have_count(11)

    # The R column stays in the viewport while a keyboard-focusable region can
    # scroll through all nine innings.
    scorecard_scroll = page.locator(".scorecard-scroll")
    scorecard_scroll.evaluate("element => element.scrollLeft = element.scrollWidth")
    total = page.locator("#inning-scorecard tbody .scorecard-total").first
    total_box = total.bounding_box()
    scroll_box = scorecard_scroll.bounding_box()
    assert total_box and scroll_box
    assert total_box["x"] >= scroll_box["x"] - 1
    assert total_box["x"] + total_box["width"] <= scroll_box["x"] + scroll_box["width"] + 1

    page.locator("#compare-player-a").select_option("99")
    page.locator("#compare-player-b").select_option("100")
    page.locator("#run-hitter-comparison").click()
    expect(page.locator("#compare-results .comparison-card")).to_have_count(2, timeout=20000)
    expect(page.locator("#compare-status")).to_contain_text("mismo abridor probable")

    page.locator("#scenario-hitter").select_option("99")
    page.locator("#scenario-pitcher").select_option("201")
    page.locator("#run-pitcher-scenario").click()
    expect(page.locator("#scenario-results")).to_be_visible(timeout=20000)
    expect(page.locator("#scenario-status")).to_contain_text("Escenario de esta sesión")
    expect(page.locator("#analyze-official-scenario")).to_be_visible()

    # Comparison analyses are archived; the what-if scenario is not.
    forecasts = web.CACHE_ROOT / "forecasts"
    assert len(list(forecasts.glob("*.json"))) == 2
    page.locator("#analyze-official-scenario").click()
    expect(page.locator("#dashboard")).to_be_visible(timeout=20000)
    expect(page.locator(".scenario-delta")).to_be_visible()
    assert len(list(forecasts.glob("*.json"))) == 3
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    assert not errors, errors
    context.close()
