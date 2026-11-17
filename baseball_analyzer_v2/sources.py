"""Public MLB and Open-Meteo adapters with explicit freshness and missing-data states."""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import TypeVar, overload
from urllib.parse import urlencode

import httpx

from baseball_analyzer_v2.storage import LOCK, atomic_json, json_cache_path

MLB = "https://statsapi.mlb.com"
WEATHER = "https://api.open-meteo.com/v1/forecast"
POSTSEASON_GAME_TYPES = {"F", "D", "L", "W", "C", "P"}


class SourceUnavailable(RuntimeError):
    pass


JSONResponse = TypeVar("JSONResponse", dict, list)


@overload
def public_json(url: str, params: dict | None = None, ttl: int = 300, *,
                timeout: float = 12) -> tuple[dict, dict]: ...


@overload
def public_json(url: str, params: dict | None = None, ttl: int = 300, *,
                response_type: type[JSONResponse], timeout: float = 12) -> tuple[JSONResponse, dict]: ...


def public_json(url: str, params: dict | None = None, ttl: int = 300, *,
                response_type: type[dict] | type[list] = dict, timeout: float = 12) -> tuple[dict | list, dict]:
    """Use inherited proxy/CA settings. Failed requests can only reuse labelled cache."""
    key = url + "?" + urlencode(sorted((params or {}).items()))
    path = json_cache_path(key)
    cached = None
    try:
        cached = json.loads(path.read_text())
    except (OSError, ValueError):
        pass
    if not isinstance(cached, dict) or not isinstance(cached.get("data"), response_type):
        cached = None
    now = time.time()
    if cached and now - cached["timestamp"] < ttl:
        return cached["data"], {"fetched_at": cached["fetched_at"], "stale": False}
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=min(4, timeout)), follow_redirects=True) as client:
            response = client.get(url, params=params, headers={"User-Agent": "DiamondIQ/2.1 (local baseball research)"})
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, response_type) or (isinstance(data, dict) and data.get("error")):
            raise ValueError("Invalid public data response")
        record = {"data": data, "timestamp": now, "fetched_at": datetime.now(timezone.utc).isoformat()}
        with LOCK:
            atomic_json(path, record)
        return data, {"fetched_at": record["fetched_at"], "stale": False}
    except (httpx.HTTPError, ValueError, OSError) as exc:
        if cached:
            return cached["data"], {"fetched_at": cached["fetched_at"], "stale": True}
        raise SourceUnavailable("La fuente pública no está disponible. Revisa conexión y acceso al proveedor.") from exc


def _person(value: dict | None) -> dict | None:
    if not value or not value.get("id"):
        return None
    return {"id": int(value["id"]), "name": value.get("fullName", value.get("name", "Jugador MLB"))}


def schedule(day: date) -> dict:
    data, freshness = public_json(MLB + "/api/v1/schedule", {
        "sportId": 1, "date": day.isoformat(), "hydrate": "probablePitcher,team,venue(location,fieldInfo,timeZone)"}, ttl=90)
    games = []
    for bucket in data.get("dates", []):
        for raw in bucket.get("games", []):
            sides = {}
            for side in ("away", "home"):
                entry = raw.get("teams", {}).get(side, {})
                team = entry.get("team", {})
                sides[side] = {"id": team.get("id"), "name": team.get("name", side),
                               "abbreviation": team.get("abbreviation", ""), "score": entry.get("score"),
                               "probable_pitcher": _person(entry.get("probablePitcher"))}
            games.append({"id": raw["gamePk"], "start_time": raw.get("gameDate"), "official_date": raw.get("officialDate", day.isoformat()),
                          "game_type": raw.get("gameType"),
                          "status": raw.get("status", {}).get("detailedState", "Pendiente"),
                          "status_code": raw.get("status", {}).get("abstractGameState", "Preview"),
                          "venue": raw.get("venue", {}), **sides})
    return {"date": day.isoformat(), "games": games, "source": {"name": "MLB Stats API", **freshness},
            "warnings": ["Jornada guardada: no se pudo actualizar MLB."] if freshness["stale"] else []}


def _roster(team_id: int, day: str) -> tuple[list, dict]:
    data, source = public_json(MLB + f"/api/v1/teams/{team_id}/roster", {"rosterType": "active", "date": day}, ttl=1800)
    return [{**_person(row["person"]), "position": row.get("position", {}).get("abbreviation", ""),
             "is_pitcher": row.get("position", {}).get("type") == "Pitcher"}
            for row in data.get("roster", []) if _person(row.get("person"))], source


def weather_for(venue: dict, start_time: str) -> dict:
    roof = venue.get("fieldInfo", {}).get("roofType", "desconocido")
    base = {"status": "unavailable", "temperature_c": None, "wind_kmh": None, "wind_direction": None,
            "roof": roof, "applied_to_projection": False,
            "message": "Contexto meteorológico; no se aplica un coeficiente sin validar."}
    if str(roof).lower() in {"dome", "domed", "indoor"}:
        return {**base, "status": "not_applicable", "message": "Estadio cubierto; clima exterior no aplicado."}
    coords = venue.get("location", {}).get("defaultCoordinates", {})
    if coords.get("latitude") is None or coords.get("longitude") is None or not start_time:
        return {**base, "message": "Faltan coordenadas del estadio o la hora del juego."}
    target = datetime.fromisoformat(start_time.replace("Z", "+00:00")).astimezone(timezone.utc)
    if not 0 <= (target.date() - datetime.now(timezone.utc).date()).days <= 15:
        return {**base, "message": "La fecha está fuera del horizonte de pronóstico meteorológico."}
    try:
        data, source = public_json(WEATHER, {"latitude": coords["latitude"], "longitude": coords["longitude"],
            "hourly": "temperature_2m,wind_speed_10m,wind_direction_10m", "timezone": "UTC",
            "start_date": target.date().isoformat(), "end_date": target.date().isoformat()}, ttl=1800)
        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        key = target.strftime("%Y-%m-%dT%H:00")
        if key not in times:
            return base
        i = times.index(key)
        def number(column):
            values = hourly.get(column, [])
            return values[i] if i < len(values) else None
        return {**base, "status": "available", "temperature_c": number("temperature_2m"),
                "wind_kmh": number("wind_speed_10m"), "wind_direction": number("wind_direction_10m"),
                "forecast_for": key + "Z", "source": {"name": "Open-Meteo", **source}}
    except SourceUnavailable:
        return {**base, "message": "No se pudo actualizar Open-Meteo; el análisis sigue sin ajuste climático."}


def game_context(game_id: int) -> dict:
    feed, freshness = public_json(MLB + f"/api/v1.1/game/{int(game_id)}/feed/live", ttl=90)
    gd = feed.get("gameData", {})
    if not gd.get("teams"):
        raise SourceUnavailable("MLB no devolvió el contexto de este juego.")
    start = gd.get("datetime", {}).get("dateTime")
    day = gd.get("datetime", {}).get("officialDate") or (start or "")[:10]
    box = feed.get("liveData", {}).get("boxscore", {}).get("teams", {})
    linescore = feed.get("liveData", {}).get("linescore", {})
    venue = gd.get("venue", {})
    warnings = []
    teams, game_sides = {}, {}
    for side in ("away", "home"):
        team = gd["teams"][side]
        raw = box.get(side, {})
        players = raw.get("players", {})
        order = raw.get("battingOrder", [])
        lineup = []
        for slot, player_id in enumerate(order[:9], start=1):
            info = players.get(f"ID{player_id}", {})
            person = _person(info.get("person"))
            if person:
                lineup.append({**person, "batting_order": slot, "position": info.get("position", {}).get("abbreviation", "")})
        published = len(lineup) == 9 and len({p["id"] for p in lineup}) == 9
        try:
            roster, roster_source = _roster(team["id"], day)
            if roster_source["stale"]:
                warnings.append(f"Roster de {team['name']} guardado sin actualización.")
        except SourceUnavailable:
            roster = [{**_person(p["person"]), "position": p.get("position", {}).get("abbreviation", ""),
                       "is_pitcher": p.get("position", {}).get("type") == "Pitcher"}
                      for p in players.values() if _person(p.get("person"))]
            warnings.append(f"No se pudo actualizar el roster de {team['name']}.")
        teams[side] = {"id": team["id"], "name": team["name"], "lineup_status": "confirmed" if published else "pending",
                       "lineup": lineup if published else [], "roster": roster}
        game_sides[side] = {"id": team["id"], "name": team["name"], "abbreviation": team.get("abbreviation", ""),
                            "score": linescore.get("teams", {}).get(side, {}).get("runs"),
                            "probable_pitcher": _person(gd.get("probablePitchers", {}).get(side))}
    if not venue.get("location") and venue.get("id"):
        try:
            details, _ = public_json(MLB + f"/api/v1/venues/{venue['id']}", {"hydrate": "location,fieldInfo,timeZone"}, ttl=86400 * 30)
            venue = next(iter(details.get("venues", [])), venue)
        except SourceUnavailable:
            warnings.append("No se pudo obtener la ficha del estadio.")
    if freshness["stale"]:
        warnings.append("Contexto guardado: alineaciones y pitchers pueden haber cambiado.")
    game = {"id": int(game_id), "start_time": start, "official_date": day, "game_type": gd.get("game", {}).get("type"),
            "status": gd.get("status", {}).get("detailedState", "Pendiente"),
            "status_code": gd.get("status", {}).get("abstractGameState", "Preview"), "venue": venue, **game_sides}
    innings = [{"number": inning.get("num"), "away": inning.get("away", {}).get("runs"), "home": inning.get("home", {}).get("runs")}
               for inning in linescore.get("innings", [])]
    return {"game": game, "teams": teams, "innings": innings, "weather": weather_for(venue, start),
            "source": {"name": "MLB Stats API", **freshness}, "warnings": warnings,
            "as_of": datetime.now(timezone.utc).isoformat()}


def game_logs(entity: str, entity_id: int, group: str, cutoff: date, game_type: str = "R") -> tuple[list, bool]:
    rows, stale = [], False
    # Include preceding season for an early-season projection.
    for season in (cutoff.year - 1, cutoff.year):
        data, source = public_json(MLB + f"/api/v1/{entity}/{int(entity_id)}/stats", {
            "stats": "gameLog", "group": group, "season": season, "gameType": game_type}, ttl=21600)
        stale |= source["stale"]
        for block in data.get("stats", []):
            for split in block.get("splits", []):
                try:
                    played = date.fromisoformat(split["date"])
                except (KeyError, ValueError):
                    continue
                if cutoff - timedelta(days=365) <= played < cutoff:
                    rows.append({"date": played.isoformat(), **split.get("stat", {})})
    return sorted(rows, key=lambda x: x["date"]), stale


def matchup_inputs(context: dict, player_id: int, *, scenario_pitcher_id: int | None = None) -> dict:
    """Collect historical opportunity/workload inputs, excluding target-date results."""
    game = context["game"]
    cutoff = date.fromisoformat(game.get("official_date") or game["start_time"][:10])
    game_type = game.get("game_type") or "R"
    if game_type in POSTSEASON_GAME_TYPES:
        game_type = "P"
    if game_type not in {"R", "P", "S"}:
        raise ValueError("Diamond IQ proyecta juegos de temporada regular, postemporada y pretemporada MLB.")
    own_side = next((side for side, team in context["teams"].items()
                     if any(p["id"] == player_id for p in team["lineup"] + team["roster"])), None)
    if not own_side:
        raise ValueError("El bateador no pertenece al roster disponible de este partido. Usa el análisis sin partido.")
    own = context["teams"][own_side]
    opponent_side = "home" if own_side == "away" else "away"
    opponent = context["teams"][opponent_side]
    entry = next((p for p in own["lineup"] if p["id"] == player_id), None)
    if own["lineup_status"] == "confirmed" and entry is None:
        raise ValueError("El jugador no aparece en la alineación publicada. Selecciona un titular o analiza su perfil sin partido.")
    starter = game[opponent_side]["probable_pitcher"]
    if scenario_pitcher_id is not None:
        if starter and starter["id"] == scenario_pitcher_id:
            raise ValueError("Elige un lanzador distinto del abridor probable.")
        alternative = next((p for p in opponent["roster"] if p.get("is_pitcher") and p["id"] == scenario_pitcher_id), None)
        if alternative is None:
            raise ValueError("El abridor hipotético debe ser un lanzador del roster rival disponible.")
        starter = {"id": alternative["id"], "name": alternative["name"]}
    result = {"batting_order": entry["batting_order"] if entry else None, "lineup_confirmed": bool(entry),
              "starter": starter, "team_plate_appearances": [], "starter_batters_faced": [],
              "bullpen": None, "warnings": [], "own_side": own_side}
    result["player_profile"] = {"age": None, "days_since_last_game": None, "applied_to_projection": False}
    try:
        profile, _ = public_json(MLB + f"/api/v1/people/{player_id}", ttl=86400 * 30)
        person = next(iter(profile.get("people", [])), {})
        birth = date.fromisoformat(person["birthDate"])
        result["player_profile"]["age"] = cutoff.year - birth.year - ((cutoff.month, cutoff.day) < (birth.month, birth.day))
    except (SourceUnavailable, KeyError, ValueError):
        pass
    try:
        hitting, stale = game_logs("teams", own["id"], "hitting", cutoff, game_type)
        result["team_plate_appearances"] = [int(r["plateAppearances"]) for r in hitting[-40:] if r.get("plateAppearances")]
        if stale:
            result["warnings"].append("Oportunidades de equipo obtenidas de caché sin actualizar.")
    except SourceUnavailable:
        result["warnings"].append("Sin historial de apariciones del equipo; se usa el historial del bateador.")
    if starter:
        try:
            pitching, stale = game_logs("people", starter["id"], "pitching", cutoff, game_type)
            result["starter_batters_faced"] = [int(r["battersFaced"]) for r in pitching if r.get("gamesStarted") == 1 and r.get("battersFaced")][-15:]
            if stale:
                result["warnings"].append("Carga del abridor obtenida de caché sin actualizar.")
        except SourceUnavailable:
            result["warnings"].append("Sin historial de duración del abridor; se usa un escenario de dos apariciones.")
    relievers = [p for p in opponent["roster"] if p.get("is_pitcher") and (not starter or p["id"] != starter["id"])]
    def relief(player):
        try:
            logs, stale = game_logs("people", player["id"], "pitching", cutoff, game_type)
            rows = [r for r in logs if r.get("gamesStarted") == 0 and r["date"] >= (cutoff - timedelta(days=180)).isoformat()]
            workload = sum(int(r.get("numberOfPitches", 0)) for r in logs if r["date"] >= (cutoff - timedelta(days=3)).isoformat())
            return {"id": player["id"], "name": player["name"], "rows": rows, "pitches_last_3_days": workload, "stale": stale}
        except SourceUnavailable:
            return None
    with ThreadPoolExecutor(max_workers=3) as pool:
        available = [p for p in pool.map(relief, relievers) if p is not None]
    if available:
        totals = {k: 0.0 for k in ("battersFaced", "hits", "homeRuns", "baseOnBalls", "strikeOuts")}
        for p in available:
            for row in p.pop("rows"):
                weight = 0.5 ** ((cutoff - date.fromisoformat(row["date"])).days / 60)
                for key in totals:
                    totals[key] += float(row.get(key, 0)) * weight
        result["bullpen"] = {"totals": totals, "relievers": available, "coverage": len(available), "roster_pitchers": len(relievers),
                             "workload_applied": False}
        if len(available) < len(relievers) or any(p["stale"] for p in available):
            result["warnings"].append("El historial de bullpen está incompleto o incluye datos en caché sin actualizar.")
    else:
        result["warnings"].append("Sin historial de bullpen; los relevistas usan la referencia de liga.")
    return result
