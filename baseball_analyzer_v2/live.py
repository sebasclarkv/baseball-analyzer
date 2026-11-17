"""MLB scoreboard snapshots and provider win probability for completed plays."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from math import isfinite

from baseball_analyzer_v2.sources import MLB, SourceUnavailable, _person, public_json

LIVE_TTL = 10
REPLAY_TTL = 3600
SIDES = ("away", "home")
BASES = ("first", "second", "third")


def _time(value: str | None) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        if "_" in value:
            return datetime.strptime(value, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except ValueError:
        return None


def _iso(time: datetime | None) -> str | None:
    return time.isoformat() if time else None


def _integer(value, maximum: int | None = None) -> int | None:
    if type(value) is int and value >= 0 and (maximum is None or value <= maximum):
        return value
    return None


def _phase(status: dict) -> str:
    detailed = (status.get("detailedState") or "").lower()
    if any(word in detailed for word in ("cancel", "postpon", "forfeit")):
        return "unavailable"
    if any(word in detailed for word in ("delay", "suspend")):
        return "paused"
    return {"Live": "live", "Preview": "preview", "Final": "final"}.get(status.get("abstractGameState"), "unavailable")


def _lineup(box: dict, people: dict) -> list[dict]:
    lineup = []
    for spot, player_id in enumerate(box.get("battingOrder", []), 1):
        record = box.get("players", {}).get(f"ID{player_id}", {})
        person = _person(record.get("person") or people.get(f"ID{player_id}"))
        if person is None:
            continue
        stats = record.get("stats", {}).get("batting", {})
        hand = people.get(f"ID{player_id}", {}).get("batSide", {}).get("code")
        lineup.append({**person, "batting_order": spot, "position": record.get("position", {}).get("abbreviation"),
                       "hits": _integer(stats.get("hits")), "plate_appearances": _integer(stats.get("plateAppearances")),
                       "hand": hand if hand in {"L", "R", "S"} else None})
    return lineup


def _pitcher(person: dict | None, box: dict, people: dict, hand: str | None = None) -> dict | None:
    player = _person(person)
    if player is None:
        return None
    pid = f"ID{player['id']}"
    pitches = box.get("players", {}).get(pid, {}).get("stats", {}).get("pitching", {}).get("numberOfPitches")
    hand = hand or people.get(pid, {}).get("pitchHand", {}).get("code")
    return {**player, "hand": hand if hand in {"L", "R", "S"} else None, "pitches": _integer(pitches)}


def _team_pitcher(team_id: int, linescore: dict, box: dict, people: dict) -> dict | None:
    for group in ("defense", "offense"):
        active = linescore.get(group, {})
        if active.get("team", {}).get("id") == team_id and active.get("pitcher"):
            return _pitcher(active["pitcher"], box, people)
    pitchers = box.get("pitchers", [])
    return _pitcher(box.get("players", {}).get(f"ID{pitchers[-1]}", {}).get("person"), box, people) if pitchers else None


def _game_team(team: dict, box: dict, linescore: dict, probable: dict | None, people: dict,
               score: dict, phase: str) -> dict:
    pitchers = box.get("pitchers", [])
    starter = box.get("players", {}).get(f"ID{pitchers[0]}", {}).get("person") if pitchers else None
    current = _team_pitcher(team["id"], linescore, box, people) if phase in {"live", "paused", "final"} else None
    return {"id": team["id"], "name": team["name"], "abbreviation": team.get("abbreviation", ""),
            "score": _integer(score.get("runs")), "starter": _pitcher(starter or probable, box, people),
            "current_pitcher": current, "lineup": _lineup(box, people)}


def _completed_plays(plays: dict) -> dict[int, tuple[datetime, dict]]:
    completed = {}
    for play in plays.get("allPlays", []):
        about = play.get("about", {})
        index = _integer(about.get("atBatIndex"))
        ended = _time(about.get("endTime"))
        if about.get("isComplete") is True and index is not None and ended is not None:
            completed[index] = ended, play
    return completed


def _last_play(completed: dict[int, tuple[datetime, dict]]) -> dict | None:
    if not completed:
        return None
    index = max(completed)
    ended, play = completed[index]
    about = play["about"]
    result = play.get("result", {})
    return {"description": result.get("description"), "event_type": result.get("eventType"),
            "at": _iso(ended), "at_bat_index": index, "inning": _integer(about.get("inning")),
            "half": about.get("halfInning") if about.get("halfInning") in {"top", "bottom"} else None}


def _percentage(value, minimum: float = 0.0) -> float | None:
    return value / 100 if type(value) in {int, float} and isfinite(value) and minimum <= value <= 100 else None


def _win_probability(game_id: int, params: dict | None, ttl: int,
                     completed: dict[int, tuple[datetime, dict]]) -> dict | None:
    rows, freshness = public_json(f"{MLB}/api/v1/game/{game_id}/winProbability", params, ttl=ttl,
                                   response_type=list, timeout=4)
    best = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        about = row.get("about", {})
        index = _integer(row.get("atBatIndex", about.get("atBatIndex")))
        played = _time(row.get("playEndTime") or about.get("endTime"))
        if index not in completed or about.get("isComplete") is not True or completed[index][0] != played:
            continue
        home, away = _percentage(row.get("homeTeamWinProbability")), _percentage(row.get("awayTeamWinProbability"))
        if home is None or away is None or abs(home + away - 1.0) > 0.002:
            continue
        if best is not None and index <= best["at_bat_index"]:
            continue
        delta = _percentage(row.get("homeTeamWinProbabilityAdded"), -100.0)
        result = completed[index][1].get("result", {})
        best = {"home": home, "away": away, "home_change": delta, "away_change": -delta if delta is not None else None,
                "play_at": _iso(played), "at_bat_index": index, "play_description": result.get("description"),
                "source": {"name": "MLB Stats API", **freshness}}
    return best


def _matchup(phase: str, half: str | None, inning: int | None, inning_state: str | None, linescore: dict,
             plays: dict, box: dict, people: dict) -> tuple[dict | None, dict | None, bool]:
    if phase != "live" or inning_state != "playing" or half is None:
        return None, None, False
    fielding_side = "home" if half == "top" else "away"
    defense = linescore.get("defense", {}).get("pitcher")
    pitcher = _pitcher(defense, box.get(fielding_side, {}), people)
    turn = plays.get("currentPlay", {})
    about = turn.get("about", {})
    if about.get("isComplete") is not False or about.get("inning") != inning or about.get("halfInning") != half:
        return None, pitcher, False
    matchup = turn.get("matchup", {})
    batter = _person(matchup.get("batter"))
    turn_pitcher = _pitcher(matchup.get("pitcher"), box.get(fielding_side, {}), people,
                            matchup.get("pitchHand", {}).get("code"))
    if batter is None or turn_pitcher is None:
        return None, pitcher, False
    # Scoreboard and play must agree during substitutions or a new at-bat.
    offense = _person(linescore.get("offense", {}).get("batter"))
    if (offense and offense["id"] != batter["id"]) or (pitcher and pitcher["id"] != turn_pitcher["id"]):
        return None, pitcher, False
    if linescore.get("balls") == 4 or linescore.get("strikes") == 3 or linescore.get("outs") == 3:
        return None, pitcher, False
    hand = matchup.get("batSide", {}).get("code")
    return {**batter, "hand": hand if hand in {"L", "R", "S"} else None}, turn_pitcher, True


def game_live(game_id: int, as_of: datetime | None = None, *, include_win: bool = True) -> dict:
    """Read the current feed or an MLB timecode without exposing future completed plays."""
    if game_id <= 0:
        raise ValueError("El identificador del partido debe ser positivo.")
    params = None
    ttl = LIVE_TTL
    if as_of is not None:
        if as_of.utcoffset() is None or as_of > datetime.now(timezone.utc):
            raise ValueError("La repetición requiere una fecha pasada con zona horaria.")
        as_of = as_of.astimezone(timezone.utc)
        params = {"timecode": as_of.strftime("%Y%m%d_%H%M%S")}
        ttl = REPLAY_TTL
    feed, freshness = public_json(f"{MLB}/api/v1.1/game/{game_id}/feed/live", params, ttl=ttl, timeout=7)
    data, live = feed.get("gameData", {}), feed.get("liveData", {})
    if feed.get("gamePk") != game_id or not all(side in data.get("teams", {}) for side in SIDES):
        raise SourceUnavailable("MLB no devolvió un marcador utilizable para este partido.")
    status = data.get("status", {})
    phase = _phase(status)
    linescore = live.get("linescore", {})
    box = live.get("boxscore", {}).get("teams", {})
    plays = live.get("plays", {})
    people = data.get("players", {})
    provider_at = _time(feed.get("metaData", {}).get("timeStamp"))
    completed = _completed_plays(plays)
    turn = plays.get("currentPlay", {}).get("about", {})
    turn_at = _time(turn.get("endTime") or turn.get("startTime"))
    times = [instant for instant in (provider_at, turn_at, *(ended for ended, _ in completed.values())) if instant]
    snapshot_at = max(times, default=None)
    if as_of is not None and (snapshot_at is None or snapshot_at > as_of or provider_at is None):
        raise SourceUnavailable("MLB no devolvió una imagen verificable para la hora de repetición solicitada.")

    game_datetime = data.get("datetime", {})
    game = {"id": game_id, "official_date": game_datetime.get("officialDate"),
            "game_type": data.get("game", {}).get("type"), "status": status.get("detailedState", "Pendiente"), "phase": phase}
    for side in SIDES:
        game[side] = _game_team(data["teams"][side], box.get(side, {}), linescore, data.get("probablePitchers", {}).get(side),
                                people, linescore.get("teams", {}).get(side, {}), phase)
    inning = _integer(linescore.get("currentInning"))
    inning_value = linescore.get("inningState", "").lower()
    inning_state = None
    if phase in {"live", "paused"}:
        inning_state = {"top": "playing", "bottom": "playing", "middle": "between", "end": "between"}.get(inning_value)
    half = {"top": "top", "bottom": "bottom"}.get(linescore.get("inningHalf", "").lower())
    batting_side = {"top": "away", "bottom": "home"}.get(half) if inning_state == "playing" else None
    batter, pitcher, active = _matchup(phase, half, inning, inning_state, linescore, plays, box, people)
    count = {"balls": _integer(linescore.get("balls"), 3) if active else None,
             "strikes": _integer(linescore.get("strikes"), 2) if active else None,
             "outs": _integer(linescore.get("outs"), 3)}
    offense = linescore.get("offense", {})
    bases = {base: _person(offense.get(base)) if batting_side and count["outs"] != 3 else None for base in BASES}
    snapshot = {"game": game, "inning": inning, "half": half, "inning_state": inning_state,
                "count": count, "bases": bases, "batter": batter, "pitcher": pitcher, "batting_side": batting_side,
                "plate_appearance_active": active, "last_play": _last_play(completed)}
    snapshot_id = sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]

    warnings = []
    if freshness["stale"]:
        warnings.append("Se conserva la última imagen disponible de MLB.")
    probability = None
    if include_win and completed:
        try:
            probability = _win_probability(game_id, params, ttl, completed)
        except SourceUnavailable:
            warnings.append("MLB no pudo actualizar la probabilidad de victoria.")
    if probability and probability["source"]["stale"]:
        warnings.append("Se conserva la última probabilidad de victoria disponible.")
    return {**snapshot, "snapshot_id": snapshot_id, "snapshot_at": _iso(snapshot_at),
            "win_probability": probability, "source": {"name": "MLB Stats API", **freshness},
            "replay": as_of is not None, "replay_at": _iso(as_of), "warnings": warnings}
