"""Conditional live hit estimates from Statcast data available before the game."""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd

from baseball_analyzer_v2.analytics import HIT_EVENTS, _log5_rate, build_analysis, filter_game_type, normalize_game_type
from baseball_analyzer_v2.data import fetch_batter_statcast, fetch_pitcher_statcast

LOGGER = logging.getLogger(__name__)
LOOKBACK_DAYS = 730
RECENT_GAMES = 40
MIN_BATTER_PA = 20
MIN_PITCHER_PA = 25
MIN_COUNT_PA = 30
MIN_REFERENCE_PA = 75
COUNT_PRIOR_PA = 75
STATCAST_START = date(2015, 1, 1)


def _before_game(frame: pd.DataFrame | None, start: date, end: date) -> pd.DataFrame:
    if frame is None or not {"game_date", "events"}.issubset(frame.columns):
        return pd.DataFrame(columns=["game_date", "events"])
    days = pd.to_datetime(frame["game_date"], errors="coerce", utc=True).dt.date
    return frame.loc[days.between(start, end)].copy().reset_index(drop=True)


def _valid_count(count: dict | None) -> dict | None:
    if not count:
        return None
    balls, strikes = count.get("balls"), count.get("strikes")
    if type(balls) is int and type(strikes) is int and 0 <= balls <= 3 and 0 <= strikes <= 2:
        return {"balls": balls, "strikes": strikes}
    return None


def _adjust_for_count(frame: pd.DataFrame, rate: float, count: dict) -> tuple[float, int, int, bool]:
    """Use one result per PA that reached the count, against the same batter at 0-0."""
    keys = ["game_pk", "at_bat_number"]
    if not set(keys + ["balls", "strikes", "events"]).issubset(frame.columns):
        return rate, 0, 0, False
    frame = frame.dropna(subset=keys)
    balls = pd.to_numeric(frame["balls"], errors="coerce")
    strikes = pd.to_numeric(frame["strikes"], errors="coerce")
    start_counts = frame.loc[balls.eq(0) & strikes.eq(0), keys].drop_duplicates()
    target_counts = frame.loc[balls.eq(count["balls"]) & strikes.eq(count["strikes"]), keys].drop_duplicates()

    # Statcast records the count before each pitch. Fouls can repeat the count.
    # Keep a single terminal outcome per PA and exclude conflicting outcomes.
    outcomes = frame.loc[frame["events"].notna(), keys + ["events"]].copy()
    outcomes["events"] = outcomes["events"].astype(str).str.strip().str.lower()
    outcomes = outcomes.drop_duplicates().drop_duplicates(keys, keep=False)
    reference = outcomes.merge(start_counts, on=keys)
    reached = reference.merge(target_counts, on=keys)
    reference_size, reached_size = len(reference), len(reached)
    if reference_size < MIN_REFERENCE_PA or reached_size < MIN_COUNT_PA:
        return rate, reached_size, reference_size, False

    # The log-odds difference to this batter's own starting count avoids adding
    # their historical skill twice. Pull sparse count effects toward zero.
    reference_rate = (int(reference["events"].isin(HIT_EVENTS).sum()) + 0.5) / (reference_size + 1)
    count_rate = (int(reached["events"].isin(HIT_EVENTS).sum()) + COUNT_PRIOR_PA * reference_rate) / (reached_size + COUNT_PRIOR_PA)
    return _log5_rate(rate, count_rate, reference_rate), reached_size, reference_size, True


def project_live_hitter(
    *,
    player: dict,
    pitcher: dict | None,
    official_date: date,
    game_type: str = "regular",
    count: dict | None = None,
    current_plate_appearance: bool = False,
) -> dict:
    """Estimate a PA and scenarios with a fixed number of additional opportunities."""
    sample = {"batter_pa": 0, "pitcher_pa": 0, "pitcher_applied": False,
              "count_pa": 0, "count_reference_pa": 0}
    warnings = []
    assumptions = [
        "Cada escenario supone que el bateador tiene esa cantidad de apariciones adicionales. El número real de turnos depende del desarrollo del juego.",
        "Los escenarios mantienen el mismo perfil de enfrentamiento y combinan las apariciones como resultados independientes.",
    ]
    result = {
        "status": "unavailable", "player": player, "pitcher": pitcher,
        "cutoff_exclusive": official_date.isoformat(), "history_game_type": None,
        "rate": None, "next_pa": None, "scenarios": [], "sample": sample,
        "warnings": warnings, "assumptions": assumptions, "source": "Baseball Savant / Statcast",
    }
    try:
        season_phase = normalize_game_type(game_type)
    except ValueError as exc:
        warnings.append(str(exc))
        return result

    start = max(STATCAST_START, official_date - timedelta(days=LOOKBACK_DAYS))
    end = min(official_date, date.today()) - timedelta(days=1)
    if start > end:
        warnings.append("La fecha está fuera del historial disponible de Statcast.")
        return result
    interval = (start.isoformat(), end.isoformat())
    try:
        raw_batter = fetch_batter_statcast(player["id"], *interval)
    except Exception:
        LOGGER.warning("Live batter Statcast retrieval failed", exc_info=True)
        warnings.append("No se pudo consultar el historial Statcast del bateador.")
        return result

    history = _before_game(raw_batter, start, end)
    batter = filter_game_type(history, season_phase)
    if season_phase != "regular" and int(batter["events"].notna().sum()) < MIN_BATTER_PA:
        season_phase = "regular"
        batter = filter_game_type(history, season_phase)
        warnings.append("La muestra de esa fase es corta. Se usa solo el historial de temporada regular.")
    result["history_game_type"] = season_phase
    sample["batter_pa"] = int(batter["events"].notna().sum())
    if sample["batter_pa"] < MIN_BATTER_PA:
        result["status"] = "insufficient"
        warnings.append(f"Se necesitan al menos {MIN_BATTER_PA} apariciones completas anteriores al juego.")
        return result
    if raw_batter.attrs.get("provenance", {}).get("stale"):
        warnings.append("El historial del bateador se leyó de la última copia local.")

    pitcher_history = None
    if pitcher:
        try:
            raw_pitcher = fetch_pitcher_statcast(pitcher["id"], *interval)
        except Exception:
            LOGGER.warning("Live pitcher Statcast retrieval failed", exc_info=True)
            warnings.append("No se pudo actualizar el historial Statcast del lanzador. Se usa el perfil del bateador.")
        else:
            pitcher_history = filter_game_type(_before_game(raw_pitcher, start, end), season_phase)
            actual_hand = pitcher.get("hand")
            if actual_hand in {"L", "R"} and "p_throws" in pitcher_history:
                hands = pitcher_history["p_throws"].astype("string").str.upper()
                if hands.isin({"L", "R"}).any():
                    pitcher_history = pitcher_history.loc[hands.eq(actual_hand).fillna(False)]
            sample["pitcher_pa"] = int(pitcher_history["events"].notna().sum())
            if sample["pitcher_pa"] < MIN_PITCHER_PA:
                pitcher_history = None
                warnings.append("La muestra del lanzador es corta. Se usa el perfil del bateador.")
            elif raw_pitcher.attrs.get("provenance", {}).get("stale"):
                warnings.append("El historial del lanzador se leyó de la última copia local.")
    else:
        warnings.append("Sin lanzador identificado por MLB. Se usa el perfil del bateador.")

    sample["pitcher_applied"] = pitcher_history is not None
    analysis = build_analysis(batter, player, RECENT_GAMES, LOOKBACK_DAYS, today=official_date,
                              pitcher_stats=pitcher_history, opponent_pitcher=pitcher if sample["pitcher_applied"] else None,
                              game_type=season_phase)
    rate = float(analysis["per_plate_appearance"]["hits"]["rate"])
    probability = rate
    observed_count = _valid_count(count) if current_plate_appearance else None
    count_status = "unavailable" if current_plate_appearance else "not_current"
    if observed_count == {"balls": 0, "strikes": 0}:
        count_status = "start"
    elif observed_count is not None:
        probability, sample["count_pa"], sample["count_reference_pa"], applied = _adjust_for_count(batter, rate, observed_count)
        count_status = "applied" if applied else "insufficient"
        if not applied:
            warnings.append("La cuenta tiene pocas apariciones comparables. Se muestra la probabilidad estimada al inicio del turno.")
        else:
            assumptions.append("La cuenta modifica el primer turno según cómo terminó el historial del bateador al pasar por esa cuenta.")
    elif current_plate_appearance:
        warnings.append("La cuenta actual no está disponible. Se muestra la probabilidad estimada al inicio del turno.")

    if sample["pitcher_applied"]:
        assumptions.append("El ajuste de lanzador supone que el pitcher mostrado sigue enfrentando al bateador.")
    result.update(
        status="available",
        rate=rate,
        next_pa={"probability": probability, "scope": "current" if current_plate_appearance else "next",
                 "count": observed_count, "count_applied": count_status == "applied", "count_status": count_status},
        scenarios=[{"plate_appearances": turns, "probability_one_or_more_hits":
                    1.0 - (1.0 - probability) * (1.0 - rate) ** (turns - 1)}
                   for turns in (1, 2, 3)],
    )
    return result
