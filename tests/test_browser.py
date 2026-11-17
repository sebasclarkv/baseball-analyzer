"""Real browser + real projection API, with deterministic public-source fixtures."""
import os
from pathlib import Path
import shutil
import socket
import threading
import time

import pytest
import uvicorn

from baseball_analyzer_v2 import web
from test_api_sources import context_payload


@pytest.fixture
def live_app(monkeypatch, tmp_path, history_data, context_payload):
    monkeypatch.setattr(web, "CACHE_ROOT", tmp_path)
    monkeypatch.setattr(web, "schedule", lambda day: {"date": day.isoformat(), "games": [context_payload["game"]],
        "source": {"name": "Datos simulados de prueba", "stale": False, "fetched_at": context_payload["as_of"]}, "warnings": []})
    monkeypatch.setattr(web, "game_context", lambda game: context_payload)
    monkeypatch.setattr(web, "fetch_batter_statcast", lambda *args: history_data)
    monkeypatch.setattr(web, "search_players", lambda query: [{"id": 99, "name": "Bateador 1", "years": "2020–2025"}])
    monkeypatch.setattr(web, "matchup_inputs", lambda *args: {"batting_order": 1, "lineup_confirmed": True,
        "team_plate_appearances": [37, 38, 40, 33], "starter": None, "starter_batters_faced": [], "bullpen": None,
        "warnings": ["Datos simulados para verificar la interfaz; no representan un pronóstico MLB real."]})
    # A short deterministic evaluation makes polling observable without downloading history.
    def evaluate(request, progress):
        progress(2, 3)
        time.sleep(.1)
        return {"status": "limited", "predictions": 3, "mean_absolute_error_hits": .8,
                "recent_rate_baseline_mae_hits": .9, "brier_score_one_or_more_hits": .2, "calibration_gap": .1,
                "message": "Evaluación sintética de interfaz."}
    monkeypatch.setattr(web, "_evaluation", evaluate)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(web.app, log_level="error"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(.05)
    assert server.started
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(5)
    sock.close()


@pytest.fixture
def browser():
    playwright = pytest.importorskip("playwright.sync_api")
    engine = os.environ.get("DIAMOND_TEST_BROWSER", "chromium")
    if engine not in {"chromium", "webkit"}:
        pytest.fail("DIAMOND_TEST_BROWSER debe ser chromium o webkit.")
    with playwright.sync_playwright() as runtime:
        options = {"headless": True}
        if engine == "chromium" and not os.environ.get("CI"):
            options["executable_path"] = (
                os.environ.get("CHROMIUM_PATH") or shutil.which("chromium") or shutil.which("chromium-browser")
            )
        try:
            instance = getattr(runtime, engine).launch(**options)
        except playwright.Error:
            if os.environ.get("CI") or "DIAMOND_TEST_BROWSER" in os.environ:
                raise
            pytest.skip(f"Instala {engine} con python -m playwright install {engine}.")
        yield instance
        instance.close()


@pytest.mark.parametrize("width,height", [(1440, 1000), (390, 844)])
def test_game_to_projection_and_local_persistence(live_app, browser, width, height, tmp_path):
    from playwright.sync_api import expect
    context = browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(live_app)
    page.locator("#game-list .game-card").click()
    expect(page.locator("#team-lineup-status")).to_contain_text("PUBLICADA")
    page.locator("#team-roster .roster-player").first.click()
    expect(page.locator("#analyze-button")).to_be_enabled()
    page.locator("#analyze-button").click()
    expect(page.locator("#dashboard")).to_be_visible(timeout=20000)
    expect(page.locator("#probability-zero-hits")).not_to_have_text("—")
    expect(page.locator("#probability-one-hit")).not_to_have_text("—")
    expect(page.locator("#probability-two-plus-hits")).not_to_have_text("—")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    page.locator("#favorite-player").click()
    expect(page.locator("#favorite-player")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#saved-reports button").first).to_be_visible()
    # Details is user-controlled progressive disclosure.
    page.locator("details").filter(has=page.locator("#run-validation")).locator("summary").click()
    page.locator("#run-validation").click()
    expect(page.locator("#validation-status")).to_contain_text("sintética", timeout=12000)
    page.locator("#load-cohort-evaluation").click()
    expect(page.locator("#cohort-evaluation-status a")).to_have_attribute("href", "https://github.com/sebasclarkv/baseball-analyzer/blob/main/docs/evaluacion.md")
    from baseball_analyzer_v2.evaluation import cohort_report
    from baseball_analyzer_v2.storage import atomic_json
    report = cohort_report([{"date": "2025-06-01", "player_id": 99, "game_id": "test", "forecast_hits": 1,
                            "forecast_any_hit": .6, "actual_hits": 1, "actual_any_hit": 1,
                            "baseline_hits": .8, "baseline_any_hit": .5, "interval_low": 0, "interval_high": 3}])
    atomic_json(tmp_path / "evaluation" / "latest.json", report)
    page.locator("#load-cohort-evaluation").click()
    expect(page.locator("#cohort-evaluation-report")).to_be_visible()
    expect(page.locator("#cohort-report-overview")).to_contain_text("1 proyecciones")
    expect(page.locator("#cohort-baseline-coverage")).to_have_text("No calculada")
    page.reload()
    expect(page.locator("#favorite-list button").first).to_be_visible()
    expect(page.locator("#saved-reports button").first).to_be_visible()
    page.locator("#saved-reports button").first.click()
    expect(page.locator("#dashboard")).to_be_visible()
    # The root-scoped service worker must reopen the app with the server unreachable.
    page.evaluate("navigator.serviceWorker.ready.then(reg => reg.scope)")
    context.set_offline(True)
    page.reload(wait_until="domcontentloaded")
    expect(page.locator("#saved-reports button").first).to_be_visible()
    expect(page.locator("#dashboard")).to_be_visible()
    context.set_offline(False)
    assert not errors, errors
    if os.environ.get("DIAMOND_CAPTURE_UI"):
        target = Path(__file__).resolve().parents[1] / "docs" / f"diamond-iq-{width}.png"
        page.reload()
        expect(page.locator("#game-list .game-card")).to_be_visible()
        expect(page.locator("#dashboard")).to_be_visible()
        page.locator("#dashboard").scroll_into_view_if_needed()
        page.evaluate("document.querySelector('#dashboard').scrollIntoView({block: 'start'})")
        page.screenshot(path=str(target), full_page=False)
    context.close()


def test_schedule_failure_keeps_manual_flow_usable(live_app, browser):
    from playwright.sync_api import expect
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.route("**/api/games?*", lambda route: route.fulfill(status=502, json={"detail": "Fuente no disponible"}))
    page.goto(live_app)
    page.locator("#player-query").fill("Judge Aaron")
    page.locator("#player-suggestions button").first.click()
    page.locator("#analyze-button").click()
    expect(page.locator("#dashboard")).to_be_visible(timeout=20000)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    page.close()


def test_legacy_reports_migrate_without_loss(live_app, browser, history_data):
    import json
    from datetime import date
    from baseball_analyzer_v2.analytics import build_analysis
    from playwright.sync_api import expect
    old = build_analysis(history_data, {"id": 99, "name": "Reporte anterior"}, 40, 730, today=date(2025, 7, 1))
    data = json.dumps([{"saved_at": "2025-07-01T12:00:00Z", "analysis": old}])
    context = browser.new_context()
    context.add_init_script("if (!localStorage.getItem('diamond-iq:indexeddb-migrated:v1')) localStorage.setItem('diamond-iq:saved-reports:v1', JSON.stringify(" + data + "));")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(live_app)
    expect(page.locator("#saved-reports button").first).to_contain_text("Reporte anterior")
    expect(page.locator("#player-name")).to_have_text("Reporte anterior")
    assert page.evaluate("localStorage.getItem('diamond-iq:saved-reports:v1')") is None
    page.reload()
    expect(page.locator("#saved-reports button").first).to_contain_text("Reporte anterior")
    assert not errors, errors
    context.close()
