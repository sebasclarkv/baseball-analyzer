"""Regression coverage for slow responses and saved projections in a browser."""

from test_api_sources import context_payload
from test_browser import browser, live_app

from baseball_analyzer_v2 import web


def choose_batter(page):
    page.locator("#player-query").fill("Bateador 1")
    page.locator("#player-suggestions button").first.click()


def defer_requests(page):
    # Deliver a response even after abort to exercise the stale-response guard.
    page.add_init_script("""(() => {
        const fetch = window.fetch.bind(window);
        window.deferredRequests = [];
        window.fetch = (url, options) => {
            if (!['/api/analyze', '/api/backtest/jobs'].includes(url)) return fetch(url, options);
            return new Promise(resolve => window.deferredRequests.push({ url, options, resolve }));
        };
    })();""")


def resolve_request(page, index, payload):
    page.evaluate("""async ({ index, payload }) => {
        window.deferredRequests[index].resolve(new Response(JSON.stringify(payload)));
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    }""", {"index": index, "payload": payload})


def test_slow_projection_cannot_replace_new_parameters(live_app, browser):
    from playwright.sync_api import expect

    page = browser.new_page()
    defer_requests(page)
    page.goto(live_app)
    choose_batter(page)
    analyze = page.locator("#analyze-button")
    analyze.click()
    page.wait_for_function("window.deferredRequests.length === 1")

    page.locator("#games-select").select_option("20")
    assert page.evaluate("window.deferredRequests[0].options.signal.aborted")
    analyze.click()
    page.wait_for_function("window.deferredRequests.length === 2")

    requests = page.evaluate("window.deferredRequests.map(r => JSON.parse(r.options.body))")
    assert [request["games"] for request in requests] == [40, 20]
    old = page.request.post(live_app + "/api/analyze", data={**requests[0], "player_name": "Respuesta anterior"})
    assert old.ok, old.text()
    resolve_request(page, 0, old.json())
    expect(analyze).to_be_disabled()
    expect(page.locator("#dashboard")).to_be_hidden()
    expect(page.locator("#saved-reports .saved-report-button")).to_have_count(0)

    current = page.request.post(live_app + "/api/analyze", data=requests[1])
    assert current.ok, current.text()
    resolve_request(page, 1, current.json())
    expect(page.locator("#player-name")).to_have_text("Bateador 1")
    expect(page.locator("#saved-reports button").first).to_contain_text("20 juegos")
    expect(page.locator("#saved-reports")).not_to_contain_text("Respuesta anterior")
    expect(analyze).to_be_enabled()
    page.close()


def test_recovery_requires_matching_window_and_game_count(live_app, browser, monkeypatch):
    from playwright.sync_api import expect

    page = browser.new_page()
    page.goto(live_app)
    choose_batter(page)
    analyze = page.locator("#analyze-button")
    analyze.click()
    expect(page.locator("#saved-reports button").first).to_contain_text("40 juegos", timeout=12000)

    def fail_unavailable(request):
        detail = f"Fuente indisponible para {request.games} juegos y {request.lookback_days} días"
        raise web.HTTPException(502, detail)

    project = web._project
    monkeypatch.setattr(web, "_project", fail_unavailable)
    notice = page.locator("#notice")
    page.locator("#games-select").select_option("20")
    analyze.click()
    expect(notice).to_have_text("Fuente indisponible para 20 juegos y 730 días")

    page.locator("#games-select").select_option("40")
    page.locator("#window-select").select_option("365")
    analyze.click()
    expect(notice).to_have_text("Fuente indisponible para 40 juegos y 365 días")

    page.locator("#window-select").select_option("730")
    analyze.click()
    expect(notice).to_contain_text("Reporte guardado localmente")

    monkeypatch.setattr(web, "_project", project)
    page.context.set_offline(True)
    analyze.click()
    expect(notice).to_contain_text("Reporte guardado localmente")
    page.context.set_offline(False)

    def reject_request(request):
        raise web.HTTPException(422, "Parámetros no aplicables")

    monkeypatch.setattr(web, "_project", reject_request)
    analyze.click()
    expect(notice).to_have_text("Parámetros no aplicables")
    page.close()


def test_saved_game_recovery_matches_the_probable_opponent(live_app, browser, monkeypatch, context_payload, history_data):
    from playwright.sync_api import expect

    home = context_payload["game"]["home"]
    home["probable_pitcher"] = {"id": 10, "name": "Abridor local"}
    matchup_inputs = web.matchup_inputs
    monkeypatch.setattr(web, "matchup_inputs", lambda *args: {
        **matchup_inputs(*args), "starter": home["probable_pitcher"],
    })
    monkeypatch.setattr(web, "fetch_pitcher_statcast", lambda *args: history_data)

    page = browser.new_page()
    page.goto(live_app)
    game = page.locator("#game-list .game-card")
    batter = page.locator("#team-roster .roster-player").first
    analyze = page.locator("#analyze-button")
    notice = page.locator("#notice")
    game.click()
    batter.click()
    analyze.click()
    expect(page.locator("#player-subtitle")).to_contain_text("Abridor local", timeout=12000)

    def fail_unavailable(request):
        raise web.HTTPException(502, "Fuente indisponible")

    monkeypatch.setattr(web, "_project", fail_unavailable)
    page.locator("#team-tab-home").click()
    analyze.click()
    expect(notice).to_contain_text("Reporte guardado localmente")

    home["probable_pitcher"] = {"id": 11, "name": "Abridor actualizado"}
    game.click()
    expect(page.locator("#context-probables")).to_contain_text("Abridor actualizado")
    batter.click()
    analyze.click()
    expect(notice).to_have_text("Fuente indisponible")
    page.close()


def test_changing_pitcher_cancels_the_previous_historical_validation(live_app, browser):
    from playwright.sync_api import expect

    page = browser.new_page()
    defer_requests(page)
    page.goto(live_app)
    choose_batter(page)
    page.locator("#analyze-button").click()
    page.wait_for_function("window.deferredRequests.length === 1")
    request = page.evaluate("JSON.parse(window.deferredRequests[0].options.body)")
    response = page.request.post(live_app + "/api/analyze", data=request)
    assert response.ok, response.text()
    resolve_request(page, 0, response.json())
    page.wait_for_function("window.deferredRequests[1]?.url === '/api/backtest/jobs'")

    page.locator("#pitcher-query").fill("Otro abridor")
    assert page.evaluate("window.deferredRequests[1].options.signal.aborted")
    resolve_request(page, 1, {
        "id": "old-job",
        "status": "complete",
        "result": {"status": "limited", "message": "Resultado obsoleto", "predictions": 24},
    })
    expect(page.locator("#validation-status")).not_to_contain_text("Resultado obsoleto")
    expect(page.locator("#validation-metrics")).to_have_attribute("hidden", "")
    expect(page.locator("#run-validation")).to_be_enabled()
    page.close()
