#!/usr/bin/env python3
"""Live provider checks, without cached fallbacks. Safe to run on a Mac or in cloud."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
from datetime import date, datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sys
import time
import tempfile
from unittest.mock import patch
from urllib.parse import urlparse
import zipfile

import httpx
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from baseball_analyzer_v2 import data, sources, storage
from baseball_analyzer_v2.projections import build_projection
from pybaseball.cache import config as pybaseball_cache


class ProbeError(RuntimeError):
    def __init__(self, category: str, message: str, http_status: int | None = None):
        super().__init__(message)
        self.category, self.http_status = category, http_status


class LiveClient:
    """Use normal proxy/CA configuration; never retry around an access denial."""
    def __init__(self, timeout: float = 30):
        self.timeout = timeout
        self.client = httpx.Client(timeout=httpx.Timeout(timeout, connect=5), follow_redirects=True,
                                   headers={"User-Agent": "DiamondIQ/2.1 (local baseball research)"})
        self.requests = []

    def close(self):
        self.client.close()

    def get(self, url: str, params: dict | None = None):
        record = {"host": urlparse(url).hostname, "path": urlparse(url).path}
        started = time.monotonic()
        try:
            response = self.client.get(url, params=params)
            record.update(http_status=response.status_code, content_type=response.headers.get("content-type", ""))
            if response.status_code == 429:
                raise ProbeError("rate_limit", "El proveedor limita solicitudes. Respeta Retry-After antes de repetir.", 429)
            if response.status_code >= 400:
                raise ProbeError("http_error", f"Respuesta HTTP {response.status_code}; no se ha validado el acceso a los datos.", response.status_code)
            return response
        except httpx.ProxyError as exc:
            record["category"] = "proxy_denied"
            raise ProbeError("proxy_denied", "El proxy rechazó la conexión; revisa los dominios permitidos del entorno.") from exc
        except httpx.TimeoutException as exc:
            record["category"] = "timeout"
            raise ProbeError("timeout", "La solicitud agotó su tiempo de espera.") from exc
        except httpx.HTTPError as exc:
            record["category"] = "transport_error"
            raise ProbeError("transport_error", "Falló el transporte HTTPS; revisa red y certificados sin desactivar TLS.") from exc
        except ProbeError as exc:
            record["category"] = exc.category
            raise
        finally:
            record["elapsed_seconds"] = round(time.monotonic() - started, 3)
            self.requests.append(record)

    def public_json(self, url, params=None, ttl=0):
        # TTL intentionally ignored: all adapter calls in this process must hit the provider.
        try:
            response = self.get(url, params)
            value = response.json()
            if not isinstance(value, dict) or value.get("error"):
                raise ValueError("Expected a JSON object without a provider error")
            return value, {"fetched_at": datetime.now(timezone.utc).isoformat(), "stale": False}
        except ProbeError:
            raise
        except ValueError as exc:
            raise ProbeError("invalid_response", "La respuesta no contiene el JSON esperado.") from exc


@contextmanager
def live_adapters(client):
    original = sources.public_json
    sources.public_json = client.public_json
    try:
        yield
    finally:
        sources.public_json = original


def check_registry(client: LiveClient):
    response = client.get("https://github.com/chadwickbureau/register/archive/refs/heads/master.zip")
    if not zipfile.is_zipfile(io.BytesIO(response.content)):
        raise ProbeError("invalid_response", "El registro de jugadores no devolvió un ZIP válido.")
    rows = []
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = [name for name in archive.namelist() if "/people" in name and name.endswith(".csv")]
        for name in names:
            with archive.open(name) as stream:
                reader = csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8-sig"))
                required = {"name_first", "name_last", "key_mlbam", "mlb_played_first", "mlb_played_last"}
                if not required.issubset(reader.fieldnames or []):
                    continue
                for row in reader:
                    if row.get("key_mlbam", "").isdigit() and row.get("mlb_played_first", "").isdigit():
                        rows.append({"name_first": row["name_first"], "name_last": row["name_last"],
                                     "key_mlbam": int(row["key_mlbam"]),
                                     "mlb_played_first": int(row["mlb_played_first"]),
                                     "mlb_played_last": int(row["mlb_played_last"] or row["mlb_played_first"])})
    if not rows:
        raise ProbeError("invalid_response", "No se encontraron jugadores MLB en el registro descargado.")
    table = pd.DataFrame(rows)
    original = data._register
    try:
        # Use the downloaded registry with the application's real search implementation.
        data._register = lambda: table
        data._prepared_register.cache_clear()
        data._search_players_normalized.cache_clear()
        matches = data.search_players("Judge Aaron")
        if not any(p["id"] == 592450 for p in matches):
            raise ProbeError("adapter_error", "El buscador no identificó a Aaron Judge en el registro real.")
    finally:
        data._register = original
        data._prepared_register.cache_clear()
        data._search_players_normalized.cache_clear()
    return {"mlb_players": len(rows), "search_verified": "Judge Aaron → MLB ID 592450", "cache_used": False}


def check_statcast(client: LiveClient, start: date):
    end = start + timedelta(days=6)
    original_get = requests.get
    before = len(client.requests)
    def observed_get(url, **kwargs):
        record = {"host": urlparse(url).hostname, "path": urlparse(url).path, "client": "pybaseball / requests"}
        started = time.monotonic()
        kwargs.setdefault("timeout", client.timeout)
        try:
            response = original_get(url, **kwargs)
            record.update(http_status=response.status_code, content_type=response.headers.get("content-type", ""))
            if response.status_code >= 400:
                category = "rate_limit" if response.status_code == 429 else "http_error"
                raise ProbeError(category, f"Statcast respondió HTTP {response.status_code} con el cliente pybaseball.", response.status_code)
            return response
        except requests.exceptions.ProxyError as exc:
            record["category"] = "proxy_denied"
            raise ProbeError("proxy_denied", "El proxy rechazó el cliente Statcast de la aplicación.") from exc
        except requests.exceptions.Timeout as exc:
            record["category"] = "timeout"
            raise ProbeError("timeout", "Statcast agotó el tiempo de espera.") from exc
        except requests.exceptions.RequestException as exc:
            record["category"] = "transport_error"
            raise ProbeError("transport_error", "Falló la conexión HTTPS del cliente Statcast.") from exc
        except ProbeError as exc:
            record["category"] = exc.category
            raise
        finally:
            record["elapsed_seconds"] = round(time.monotonic() - started, 3)
            client.requests.append(record)
    # Disable the SDK's independent cache in memory; do not alter its saved configuration.
    # Exercise the application's fetch path, including Parquet, with no saved data.
    with tempfile.TemporaryDirectory(prefix="diamond-check-") as folder:
        with patch.object(storage, "CACHE_ROOT", Path(folder)), \
                patch.object(pybaseball_cache, "enabled", False), \
                patch.object(requests, "get", observed_get):
            frame = data.fetch_batter_statcast(592450, start.isoformat(), end.isoformat())
    if len(client.requests) == before:
        raise ProbeError("cached_response", "No se observó una solicitud de red; el resultado no valida la conexión.")
    required = {"game_date", "events", "batter", "game_pk"}
    if not required.issubset(frame.columns) or frame.empty:
        raise ProbeError("invalid_response", "No llegaron lanzamientos con las columnas requeridas para la muestra elegida.")
    if not pd.to_numeric(frame.batter, errors="coerce").eq(592450).all():
        raise ProbeError("invalid_response", "La respuesta contiene un bateador distinto del solicitado.")
    days = pd.to_datetime(frame.game_date, utc=True).dt.date
    if not days.between(start, end).all():
        raise ProbeError("invalid_response", "La respuesta contiene fechas fuera del periodo solicitado.")
    if not frame.events.notna().any():
        raise ProbeError("invalid_response", "La muestra no contiene apariciones al plato completas.")
    projection = build_projection(frame, {"id": 592450, "name": "Aaron Judge"}, 5, 30, forecast_date=end + timedelta(days=1))
    p = projection["projection"]
    total = p["probability_zero_hits"] + p["probability_exactly_one_hit"] + p["probability_two_or_more_hits"]
    if abs(total - 1) > 1e-8:
        raise ProbeError("adapter_error", "La distribución calculada no suma uno.")
    return {"rows": len(frame), "games": int(frame.game_pk.nunique()), "plate_appearances": int(frame.events.notna().sum()),
            "from": start.isoformat(), "through": end.isoformat(), "projection_pipeline": "passed",
            "scope": "Integración con datos históricos reales; no mide precisión predictiva.", "cache_used": False}


def run_checks(sample_date: date, timeout: float = 30):
    client = LiveClient(timeout)
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "sample_date": sample_date.isoformat(),
              "cache_policy": "disabled_for_all_checks", "checks": [], "requests": client.requests}
    def run(name, action):
        start_count = len(client.requests)
        try:
            result = action()
            errors = [r for r in client.requests[start_count:] if r.get("category")]
            entry = {"name": name, "status": "partial" if errors else "passed", "details": result}
        except ProbeError as exc:
            entry = {"name": name, "status": "blocked" if exc.category == "proxy_denied" else "failed",
                     "category": exc.category, "message": str(exc), "http_status": exc.http_status}
        except Exception as exc:
            entry = {"name": name, "status": "failed", "category": "adapter_error", "message": f"El adaptador no procesó la respuesta ({type(exc).__name__})."}
        report["checks"].append(entry)
        print(f"{name}: {entry['status']}" + (" · " + entry["message"] if entry.get("message") else ""), flush=True)
        return entry
    schedule_data = {}
    try:
        with live_adapters(client):
            def mlb_schedule():
                nonlocal schedule_data
                schedule_data = sources.schedule(sample_date)
                if not schedule_data.get("games"):
                    raise ProbeError("invalid_response", "No hay partidos para la fecha de muestra; elige una jornada MLB con juegos.")
                if schedule_data["source"]["stale"]:
                    raise ProbeError("cached_response", "Una respuesta de caché no valida la conexión actual.")
                return {"games": len(schedule_data["games"]), "cache_used": False}
            result = run("MLB: jornada", mlb_schedule)
            if result["status"] == "passed":
                def mlb_context():
                    context = sources.game_context(schedule_data["games"][0]["id"])
                    for side in ("home", "away"):
                        if not context["teams"][side]["roster"] and not context["teams"][side]["lineup"]:
                            raise ProbeError("invalid_response", "No llegaron jugadores para uno de los equipos.")
                    return {"game_id": context["game"]["id"], "home_lineup": context["teams"]["home"]["lineup_status"],
                            "away_lineup": context["teams"]["away"]["lineup_status"], "cache_used": False}
                run("MLB: contexto y rosters", mlb_context)
                def mlb_history():
                    home = schedule_data["games"][0]["home"]
                    rows, stale = sources.game_logs("teams", home["id"], "hitting", sample_date)
                    if not rows or not any(r.get("plateAppearances") for r in rows) or stale:
                        raise ProbeError("invalid_response", "El historial de equipo no contiene las oportunidades al bate esperadas.")
                    return {"team_id": home["id"], "historical_games": len(rows), "cache_used": False}
                run("MLB: historial del equipo", mlb_history)
            else:
                for name in ("MLB: contexto y rosters", "MLB: historial del equipo"):
                    report["checks"].append({"name": name, "status": "skipped", "message": "Requiere una jornada válida; no se inventa un ID de juego."})
            def weather():
                tomorrow = datetime.now(timezone.utc).date() + timedelta(days=1)
                venue = {"location": {"defaultCoordinates": {"latitude": 40.8296, "longitude": -73.9262}}, "fieldInfo": {"roofType": "Open"}}
                value = sources.weather_for(venue, tomorrow.isoformat() + "T18:00:00Z")
                if value["status"] != "available" or value.get("temperature_c") is None or value.get("wind_kmh") is None:
                    raise ProbeError("invalid_response", "No llegó el pronóstico horario esperado.")
                return {"forecast_for": value["forecast_for"], "temperature_c": value["temperature_c"], "wind_kmh": value["wind_kmh"], "cache_used": False}
            run("Open-Meteo: pronóstico horario", weather)
        run("Statcast: CSV y cálculo de proyección", lambda: check_statcast(client, sample_date))
        run("Chadwick: descarga y búsqueda", lambda: check_registry(client))
    finally:
        client.close()
    report["ok"] = all(check["status"] == "passed" for check in report["checks"])
    return report


def main():
    parser = argparse.ArgumentParser(description="Comprueba fuentes reales y adaptadores sin usar caché.")
    parser.add_argument("--sample-date", type=date.fromisoformat, default=date(2025, 7, 1), help="Inicio de una semana histórica con juegos de Aaron Judge.")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--output", type=Path, default=ROOT / ".cache" / "connection-report.json")
    args = parser.parse_args()
    if args.sample_date + timedelta(days=6) >= date.today() or args.timeout <= 0:
        parser.error("Usa una semana histórica completa y un timeout positivo.")
    report = run_checks(args.sample_date, args.timeout)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Reporte: {args.output}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
