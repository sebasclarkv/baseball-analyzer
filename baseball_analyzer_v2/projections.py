"""Game-level distributions: opportunities, starter exposure and bullpen uncertainty."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from math import exp, lgamma

import numpy as np
import pandas as pd

from baseball_analyzer_v2.analytics import LEAGUE_PRIORS, _log5_rate, build_analysis


def beta_binomial(estimate: dict, n: int) -> np.ndarray:
    """Exact finite PMF for an approximate beta posterior; rates are not certainties."""
    if n <= 0:
        return np.array([1.0])
    p = float(np.clip(estimate["rate"], 1e-6, 1 - 1e-6))
    variance = max(1e-10, ((estimate["high"] - estimate["low"]) / 3.29) ** 2)
    concentration = float(np.clip(p * (1 - p) / variance - 1, 2, 100000))
    a, b = p * concentration, (1 - p) * concentration
    pmf = np.array([exp(lgamma(n + 1) - lgamma(k + 1) - lgamma(n - k + 1)
                         + lgamma(k + a) + lgamma(n - k + b) - lgamma(n + a + b)
                         + lgamma(a + b) - lgamma(a) - lgamma(b)) for k in range(n + 1)])
    return pmf / pmf.sum()


def _quantile(pmf: np.ndarray, q: float) -> int:
    return min(len(pmf) - 1, int(np.searchsorted(np.cumsum(pmf), q)))


def _home_runs_from_hits(hit_pmf: np.ndarray, rates: dict) -> np.ndarray:
    """HR counts are a subset of hit counts in every outcome, not just on average."""
    denominator = max(1e-6, rates["hits"]["rate"])
    conditional = {field: min(1.0, max(0.0, rates["home_runs"][field] / denominator)) for field in ("rate", "low", "high")}
    result = np.zeros(len(hit_pmf))
    for count, weight in enumerate(hit_pmf):
        result[:count + 1] += weight * beta_binomial(conditional, count)
    return result / result.sum()


def _opportunities(stats: pd.DataFrame, inputs: dict) -> tuple[list[int], np.ndarray, str]:
    slot = inputs.get("batting_order")
    team_pa = inputs.get("team_plate_appearances", [])
    if slot and team_pa:
        values = [int(t // 9 + (slot <= t % 9)) for t in team_pa if 9 <= t <= 100]
        method = f"Apariciones derivadas del puesto {slot} y de los últimos {len(values)} juegos del equipo; supone que inicia y permanece en el juego."
    else:
        rows = stats.loc[stats["events"].notna()].copy()
        rows["_day"] = pd.to_datetime(rows["game_date"], utc=True)
        rows["_id"] = rows["game_pk"].astype(str) if "game_pk" in rows else rows["_day"].astype(str)
        games = rows.groupby("_id").agg(pa=("events", "size"), day=("_day", "min")).sort_values("day")
        values = games["pa"].tail(10).astype(int).tolist()
        method = "Distribución reciente de apariciones del bateador; titularidad y sustituciones futuras no confirmadas."
    values = [min(20, max(0, n)) for n in values]
    if not values:
        values = [4]
        method = "Escenario de cuatro apariciones por falta de historial de oportunidades."
    weights = np.array([0.95 ** (len(values) - i - 1) for i in range(len(values))])
    return values, weights / weights.sum(), method


def _exposures(n: int, inputs: dict, has_starter: bool) -> list[tuple[int, float]]:
    if not has_starter:
        return [(0, 1.0)]
    bf = inputs.get("starter_batters_faced", [])
    slot = inputs.get("batting_order")
    if bf:
        counts = {}
        for total in bf:
            # Unknown batting order is marginalized uniformly across nine slots.
            for order in ([slot] if slot else range(1, 10)):
                k = min(n, max(0, int(total // 9 + (order <= total % 9))))
                counts[k] = counts.get(k, 0) + 1
        return [(k, count / sum(counts.values())) for k, count in counts.items()]
    return [(min(2, n), 1.0)]


def _repertoire(raw: pd.DataFrame | None, cutoff: date) -> list:
    if raw is None or raw.empty or "pitch_type" not in raw:
        return []
    rows = raw.copy()
    days = pd.to_datetime(rows["game_date"], utc=True).dt.date
    rows = rows.loc[(days < cutoff) & (days >= cutoff - timedelta(days=180))]
    tracked = rows.loc[rows["pitch_type"].notna()]
    result = []
    for kind, group in tracked.groupby("pitch_type"):
        speed = pd.to_numeric(group.get("release_speed", pd.Series(dtype=float)), errors="coerce").dropna()
        result.append({"pitch_type": str(kind), "share": len(group) / len(tracked), "pitches": len(group),
                       "velocity_mph": float(speed.mean()) if len(speed) else None, "applied_to_projection": False})
    return sorted(result, key=lambda x: -x["share"])


def build_projection(raw_stats: pd.DataFrame, player: dict, games_requested: int, lookback_days: int,
                     forecast_date: date | None = None, pitcher_stats: pd.DataFrame | None = None,
                     opponent_pitcher: dict | None = None, context: dict | None = None,
                     inputs: dict | None = None) -> dict:
    cutoff = forecast_date or datetime.now(timezone.utc).date()
    inputs = inputs or {}
    days = pd.to_datetime(raw_stats["game_date"], errors="coerce", utc=True).dt.date
    stats = raw_stats.loc[(days < cutoff) & (days >= cutoff - timedelta(days=lookback_days))].copy()
    if pitcher_stats is not None and not pitcher_stats.empty:
        pdays = pd.to_datetime(pitcher_stats["game_date"], errors="coerce", utc=True).dt.date
        pitcher_stats = pitcher_stats.loc[(pdays < cutoff) & (pdays >= cutoff - timedelta(days=lookback_days))].copy()
    base = build_analysis(stats, player, games_requested, lookback_days, today=cutoff)
    starter_report = build_analysis(stats, player, games_requested, lookback_days, today=cutoff,
                                   pitcher_stats=pitcher_stats, opponent_pitcher=opponent_pitcher) if opponent_pitcher else base
    result = deepcopy(base)
    result["opponent_pitcher"] = starter_report.get("opponent_pitcher")
    starter_rates = starter_report["per_plate_appearance"]
    relief_rates = deepcopy(base["per_plate_appearance"])
    bullpen = inputs.get("bullpen")
    if bullpen and bullpen["totals"].get("battersFaced", 0) > 0:
        totals = bullpen["totals"]
        for metric, column in {"hits": "hits", "home_runs": "homeRuns", "walks": "baseOnBalls", "strikeouts": "strikeOuts"}.items():
            # A substantial prior prevents a few recent relief innings dominating a forecast.
            prior = LEAGUE_PRIORS[metric]
            allowed = (totals[column] + prior * 200) / (totals["battersFaced"] + 200)
            for field in ("rate", "low", "high"):
                relief_rates[metric][field] = _log5_rate(relief_rates[metric][field], allowed, prior)
            # Include uncertainty of the pooled relief sample, not just the hitter.
            error = 1.645 * np.sqrt(allowed * (1 - allowed) / (totals["battersFaced"] + 200))
            relief_rates[metric]["low"] = max(0.0, relief_rates[metric]["low"] - error)
            relief_rates[metric]["high"] = min(1.0, relief_rates[metric]["high"] + error)
    # A home run is a hit, and a strikeout is included in outs. Preserve these subsets
    # even when independently estimated rates have extreme, sparse samples.
    for rates in (starter_rates, relief_rates):
        for subset, total in (("home_runs", "hits"), ("strikeouts", "outs")):
            for field in ("rate", "low", "high"):
                rates[subset][field] = min(rates[subset][field], rates[total][field])
    values, weights, opportunity_method = _opportunities(stats, inputs)
    expected_pa = float(np.dot(values, weights))
    start_pa = 0.0
    distributions = {m: np.zeros(max(values) + 1) for m in ("hits", "home_runs")}
    for n, weight in zip(values, weights):
        for first, share in _exposures(n, inputs, bool(opponent_pitcher)):
            start_pa += weight * share * first
            start_hits = beta_binomial(starter_rates["hits"], first)
            relief_hits = beta_binomial(relief_rates["hits"], n - first)
            for metric in distributions:
                start_dist = start_hits if metric == "hits" else _home_runs_from_hits(start_hits, starter_rates)
                relief_dist = relief_hits if metric == "hits" else _home_runs_from_hits(relief_hits, relief_rates)
                combined = np.convolve(start_dist, relief_dist)
                distributions[metric][:len(combined)] += float(weight * share) * combined
    for pmf in distributions.values():
        pmf /= pmf.sum()
    pmf = distributions["hits"]
    fraction = start_pa / expected_pa if expected_pa else 0
    for metric, entry in result["per_plate_appearance"].items():
        for field in ("rate", "low", "high"):
            entry[field] = float(fraction * starter_rates[metric][field] + (1 - fraction) * relief_rates[metric][field])
    for subset, total in (("home_runs", "hits"), ("strikeouts", "outs")):
        result["per_plate_appearance"][subset]["rate"] = min(result["per_plate_appearance"][subset]["rate"], result["per_plate_appearance"][total]["rate"])
    interval = {"low": _quantile(pmf, .05), "high": _quantile(pmf, .95), "level": .90, "calibrated": False}
    pa_pmf = np.zeros(max(values) + 1)
    for n, w in zip(values, weights):
        pa_pmf[n] += w
    result["projection"].update({
        "plate_appearances": expected_pa, "plate_appearances_low": _quantile(pa_pmf, .10), "plate_appearances_high": _quantile(pa_pmf, .90),
        "hits": float(np.dot(np.arange(len(pmf)), pmf)), "hits_low": interval["low"], "hits_high": interval["high"],
        "home_runs": float(np.dot(np.arange(len(distributions["home_runs"])), distributions["home_runs"])),
        "walks": expected_pa * result["per_plate_appearance"]["walks"]["rate"],
        "strikeouts": expected_pa * result["per_plate_appearance"]["strikeouts"]["rate"],
        "probability_one_or_more_hits": float(1 - pmf[0]), "probability_zero_hits": float(pmf[0]),
        "probability_exactly_one_hit": float(pmf[1]) if len(pmf) > 1 else 0.0,
        "probability_two_or_more_hits": float(pmf[2:].sum()),
        "probability_one_or_more_home_runs": float(1 - distributions["home_runs"][0]),
        "predictive_interval_hits": interval, "hit_distribution": pmf.tolist(),
        "starter_plate_appearances": float(start_pa), "bullpen_plate_appearances": float(expected_pa - start_pa),
    })
    missing = []
    last_game_age = (cutoff - pd.to_datetime(stats["game_date"], utc=True).max().date()).days
    if last_game_age > 30:
        missing.append(f"El último juego disponible fue hace {last_game_age} días; no se conoce la condición actual del jugador.")
    if not context:
        missing.append("Partido, estadio y alineación sin seleccionar.")
    if not inputs.get("lineup_confirmed"):
        missing.append("Titularidad y orden al bate sin confirmar.")
    if not opponent_pitcher:
        missing.append("Abridor rival sin confirmar.")
    elif not result["opponent_pitcher"] or result["opponent_pitcher"]["status"] != "available":
        missing.append("Historial del abridor no disponible; se usa el perfil del bateador.")
    if not inputs.get("starter_batters_faced") and opponent_pitcher:
        missing.append("Duración del abridor: escenario de dos apariciones, sin historial de carga.")
    if not bullpen or not bullpen["totals"].get("battersFaced"):
        missing.append("Bullpen estimado con referencia de liga.")
    if context and context.get("weather", {}).get("status") == "unavailable":
        missing.append("Pronóstico meteorológico no disponible.")
    if context and "retract" in str(context.get("weather", {}).get("roof", "")).lower():
        missing.append("Estado abierto o cerrado del techo retráctil sin confirmar.")
    warnings = list(inputs.get("warnings", [])) + (context or {}).get("warnings", [])
    for raw in (raw_stats, pitcher_stats):
        if raw is not None and raw.attrs.get("provenance", {}).get("stale"):
            warnings.append("Statcast guardado: la descarga no se pudo actualizar.")
    limitations = ["El rango de hits es predictivo y aproximado; su cobertura debe evaluarse históricamente.",
                   "Estadio, clima, repertorio y carga reciente se muestran como contexto; no aplican efectos numéricos entrenados.",
                   "No se modelan lesiones ni cambios de alineación posteriores a esta consulta."]
    probability = result["projection"]["probability_one_or_more_hits"]
    result["explanation"] = {"summary": f"El modelo estima al menos un hit en unas {round(100 * probability)} de cada 100 repeticiones de este escenario, y ningún hit en unas {100 - round(100 * probability)}.",
                             "limitations": limitations}
    result["data_quality"] = {"status": "provisional" if missing or warnings else "ready", "missing": missing,
                              "warnings": warnings, "sources": ["Baseball Savant / Statcast"] + (["MLB Stats API"] if context else []),
                              "as_of": datetime.now(timezone.utc).isoformat()}
    result["game_context"] = deepcopy(context)
    result["sample"]["confidence_basis"] = "batter_history_only"
    result["sample"]["pitcher_effective_plate_appearances"] = starter_report["sample"].get("pitcher_effective_plate_appearances")
    if result["game_context"] is not None:
        inputs.setdefault("player_profile", {"age": None, "applied_to_projection": False})
        inputs["player_profile"]["days_since_last_game"] = last_game_age
        result["game_context"]["matchup"] = {**inputs, "pitch_repertoire": _repertoire(pitcher_stats, cutoff)}
    result["drivers"].append({"label": "Oportunidades al bate", "description": opportunity_method})
    if opponent_pitcher:
        result["drivers"].append({"label": "Abridor y relevistas", "description": f"{start_pa:.1f} apariciones esperadas contra el abridor y {expected_pa - start_pa:.1f} contra el bullpen.",
                                  "hit_rate_change": result["per_plate_appearance"]["hits"]["rate"] - base["per_plate_appearance"]["hits"]["rate"]})
    result["model"].update({"name": "Trayectoria bayesiana + exposición a abridor/bullpen + distribución de hits", "version": "2.1",
                           "plate_appearance_forecast": opportunity_method, "data_cutoff_exclusive": cutoff.isoformat(),
                           "event_probability_method": "Convolución de beta-binomiales por abridor y bullpen, mezclada sobre oportunidades al bate.",
                           "confidence_interval": "Rango predictivo discreto aproximado del 90%; cobertura aún no calibrada."})
    result["note"] = opportunity_method + " " + limitations[0]
    return result
