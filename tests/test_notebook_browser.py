"""Notebook backups and mobile PWA help through the running application."""
from copy import deepcopy
from datetime import date
import json
from pathlib import Path
from urllib.parse import urlsplit

from baseball_analyzer_v2.analytics import build_analysis
from test_api_sources import context_payload
from test_browser import browser, live_app


def test_report_backup_round_trip_preserves_existing_and_validates_before_import(
    live_app, browser, history_data,
):
    from playwright.sync_api import expect

    legacy_analysis = build_analysis(
        history_data, {"id": 99, "name": "Bateador anterior"}, 40, 730, today=date(2025, 7, 1),
    )
    previous = []
    for i in range(9):
        analysis = deepcopy(legacy_analysis)
        analysis["player"] = {"id": 99 + i, "name": f"Bateador anterior {i + 1}"}
        previous.append({"saved_at": f"2025-07-{i + 1:02}T12:00:00Z", "analysis": analysis})

    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.add_init_script(
        "if (!localStorage.getItem('diamond-iq:indexeddb-migrated:v1')) "
        "localStorage.setItem('diamond-iq:saved-reports:v1', " + json.dumps(json.dumps(previous)) + ");"
    )
    page = context.new_page()
    page.goto(live_app)
    report_buttons = page.locator(".saved-report-button")
    expect(report_buttons).to_have_count(9)

    game_analysis = deepcopy(legacy_analysis)
    game_analysis["player"] = {"id": 199, "name": "Bateador del partido"}
    game_analysis["game_context"] = {"game": {"id": 777283, "official_date": "2025-07-01",
        "away": {"name": "Toronto Blue Jays", "abbreviation": "TOR"},
        "home": {"name": "New York Yankees", "abbreviation": "NYY"}}}
    record = {"saved_at": "2025-07-01T12:00:00Z", "analysis": game_analysis}
    backup = {"format": "diamond-iq-report-notebook", "version": 1, "reports": [record]}

    def upload(target_page, contents):
        target_page.locator("#reports-backup-input").set_input_files({
            "name": "diamond-iq-reports.json", "mimeType": "application/json",
            "buffer": json.dumps(contents).encode("utf-8"),
        })

    upload(page, {**backup, "version": 2})
    expect(page.locator("#notebook-status")).to_contain_text("versión")
    expect(report_buttons).to_have_count(9)
    upload(page, {**backup, "reports": [record, {"saved_at": record["saved_at"], "analysis": {}}]})
    expect(page.locator("#notebook-status")).to_contain_text("posición 2")
    expect(report_buttons).to_have_count(9)

    upload(page, backup)
    expect(report_buttons).to_have_count(10)
    page.locator("#reports-game-filter").select_option("777283")
    expect(report_buttons).to_have_count(1)
    expect(report_buttons.first).to_contain_text("TOR @ NYY")
    page.get_by_role("button", name="Fijar reporte de Bateador del partido").click()
    expect(page.get_by_role("button", name="Fijar reporte de Bateador del partido")).to_have_attribute("aria-pressed", "true")
    page.reload()
    expect(report_buttons).to_have_count(10)
    expect(report_buttons.first).to_contain_text("Bateador del partido")
    expect(page.get_by_role("button", name="Fijar reporte de Bateador del partido")).to_have_attribute("aria-pressed", "true")

    with page.expect_download() as event:
        page.get_by_role("button", name="Exportar respaldo").click()
    portable_copy = json.loads(Path(event.value.path()).read_text(encoding="utf-8"))
    assert portable_copy["version"] == 1
    assert len(portable_copy["reports"]) == 10
    context.close()

    new_device = browser.new_context(viewport={"width": 390, "height": 844})
    restored = new_device.new_page()
    restored.goto(live_app)
    upload(restored, portable_copy)
    expect(restored.locator(".saved-report-button")).to_have_count(10)
    restored.get_by_role("button", name="Bateador del partido", exact=False).first.click()
    expect(restored.locator("#player-name")).to_have_text("Bateador del partido")
    assert restored.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    new_device.close()


def test_mobile_install_help_distinguishes_http_lan_from_supported_origin(live_app, browser):
    from playwright.sync_api import expect

    context = browser.new_context(
        viewport={"width": 390, "height": 844}, has_touch=True,
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
    )
    page = context.new_page()
    page.goto(live_app)
    assert page.evaluate("window.isSecureContext")
    page.get_by_role("button", name="Instalar app").click()
    expect(page.get_by_role("dialog")).to_contain_text("Añadir a pantalla de inicio")

    # A non-loopback HTTP origin behaves like a LAN address; serve it from the local fixture.
    def lan_response(route):
        parsed = urlsplit(route.request.url)
        forwarded_url = live_app + parsed.path + ("?" + parsed.query if parsed.query else "")
        route.fulfill(response=route.fetch(url=forwarded_url))

    page.route("http://diamond-lan.test/**", lan_response)
    page.goto("http://diamond-lan.test/")
    assert not page.evaluate("window.isSecureContext")
    page.get_by_role("button", name="Cómo instalar").click()
    dialog = page.get_by_role("dialog")
    expect(dialog).to_contain_text("dirección HTTP de tu red local")
    expect(dialog).to_contain_text("HTTPS válida")
    expect(dialog).not_to_contain_text("Añadir a pantalla de inicio")
    context.close()
