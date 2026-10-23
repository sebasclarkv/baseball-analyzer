"""Explainable multi-season hitter estimates and pitcher-aware projections."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from math import exp, exp2, lgamma, log, sqrt
from typing import Any

import numpy as np
import pandas as pd

PRIOR_PLATE_APPEARANCES = 45.0
LONG_TERM_HALF_LIFE_DAYS = 365.0
FORM_HALF_LIFE_DAYS = 45.0
FORM_PRIOR_APPEARANCES = 45.0
PLATOON_PRIOR_APPEARANCES = 80.0
PITCHER_PRIOR_APPEARANCES = 120.0
LEAGUE_PRIORS = {
    "hits": 0.220,
    "home_runs": 0.035,
    "walks": 0.085,
    "strikeouts": 0.225,
    "outs": 0.62,
}
HIT_EVENTS = {"single", "double", "triple", "home_run"}
HOME_RUN_EVENTS = {"home_run"}
WALK_EVENTS = {"walk", "intent_walk"}
STRIKEOUT_EVENTS = {"strikeout", "strikeout_double_play"}
OUT_EVENTS = {
    "field_out",
    "force_out",
    "grounded_into_double_play",
    "fielders_choice_out",
    "double_play",
    "triple_play",
    "sac_fly",
    "sac_bunt",
    "other_out",
    *STRIKEOUT_EVENTS,
}


def _wilson_interval(rate: float, sample_size: float, z: float = 1.645) -> tuple[float, float]:
    if sample_size <= 0:
        return 0.0, 1.0
    denominator = 1.0 + z * z / sample_size
    center = (rate + z * z / (2.0 * sample_size)) / denominator
    margin = (
        z
        * sqrt((rate * (1.0 - rate) / sample_size) + z * z / (4.0 * sample_size**2))
        / denominator
    )
    return max(0.0, center - margin), min(1.0, center + margin)


def _smoothed_rate(
    weighted_successes: float,
    weighted_total: float,
    effective_sample_size: float,
    prior_rate: float,
    prior_appearances: float = PRIOR_PLATE_APPEARANCES,
) -> dict[str, float]:
    posterior = (
        weighted_successes + prior_rate * prior_appearances
    ) / (weighted_total + prior_appearances)
    low, high = _wilson_interval(
        posterior, effective_sample_size + prior_appearances
    )
    return {
        "rate": float(posterior),
        "low": float(low),
        "high": float(high),
    }


def _effective_sample_size(weights: pd.Series) -> float:
    total = float(weights.sum())
    squared = float((weights**2).sum())
    return total**2 / squared if squared > 0 else 0.0


def _outcome_values(frame: pd.DataFrame, metric: str) -> pd.Series:
    events = frame["_event"]
    event_sets = {
        "hits": HIT_EVENTS,
        "home_runs": HOME_RUN_EVENTS,
        "walks": WALK_EVENTS,
        "strikeouts": STRIKEOUT_EVENTS,
        "outs": OUT_EVENTS,
    }
    values = events.isin(event_sets[metric]).astype(float)
    if metric == "hits" and "estimated_ba_using_speedangle" in frame:
        xba = pd.to_numeric(frame["estimated_ba_using_speedangle"], errors="coerce")
        values.loc[xba.notna()] = xba.loc[xba.notna()].clip(0.0, 1.0)
    return values


def _estimate_metric(
    frame: pd.DataFrame,
    metric: str,
    weights: pd.Series,
    prior_rate: float,
    prior_appearances: float = PRIOR_PLATE_APPEARANCES,
) -> dict[str, float]:
    values = _outcome_values(frame, metric)
    aligned_weights = weights.reindex(frame.index).fillna(0.0)
    total = float(aligned_weights.sum())
    estimate = _smoothed_rate(
        float((values * aligned_weights).sum()),
        total,
        _effective_sample_size(aligned_weights),
        prior_rate,
        prior_appearances,
    )
    estimate["weighted_plate_appearances"] = total
    estimate["effective_sample_size"] = _effective_sample_size(aligned_weights)
    return estimate


def _date_weights(
    frame: pd.DataFrame, today: date, half_life_days: float
) -> pd.Series:
    ages = np.array(
        [max(0, (today - day).days) for day in frame["_game_day"]], dtype=float
    )
    return pd.Series(
        [exp2(-age / half_life_days) for age in ages],
        index=frame.index,
        dtype=float,
    )


def _prepare_statcast(
    raw_stats: pd.DataFrame, today: date
) -> tuple[pd.DataFrame, pd.DataFrame]:
    stats = raw_stats.copy()
    stats["_game_day"] = pd.to_datetime(
        stats["game_date"], errors="coerce", utc=True
    ).dt.tz_convert(None).dt.date
    stats = stats.dropna(subset=["_game_day"])
    stats = stats[stats["_game_day"] <= today].copy()
    if "game_pk" in stats:
        game_keys = stats["game_pk"].astype("string")
        stats["_game_id"] = game_keys.fillna(stats["_game_day"].astype("string"))
    else:
        stats["_game_id"] = stats["_game_day"].astype("string")
    plate_appearances = stats.loc[stats["events"].notna()].copy()
    plate_appearances["_event"] = (
        plate_appearances["events"].astype("string").str.lower()
    )
    return stats, plate_appearances


def _game_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    game_id = str(row["game_id"])
    if game_id.isdigit():
        return row["day"], 0, int(game_id)
    return row["day"], 1, game_id


def _log5_rate(batter_rate: float, pitcher_allowed_rate: float, league_rate: float) -> float:
    """Combine independent batter and pitcher skill on the odds scale."""
    epsilon = 1e-6
    batter_rate = min(1.0 - epsilon, max(epsilon, batter_rate))
    pitcher_allowed_rate = min(1.0 - epsilon, max(epsilon, pitcher_allowed_rate))
    league_rate = min(1.0 - epsilon, max(epsilon, league_rate))
    log_odds = (
        log(batter_rate / (1.0 - batter_rate))
        + log(pitcher_allowed_rate / (1.0 - pitcher_allowed_rate))
        - log(league_rate / (1.0 - league_rate))
    )
    return 1.0 / (1.0 + exp(-log_odds))


def _predictive_any_event_probability(
    estimate: dict[str, float], appearances: float
) -> float:
    """Integrate rate uncertainty with a beta-binomial probability of 1+ events."""
    if appearances <= 0:
        return 0.0
    rate = min(1.0 - 1e-6, max(1e-6, estimate["rate"]))
    standard_error = max(
        1e-5,
        (estimate["high"] - estimate["low"]) / (2.0 * 1.645),
    )
    variance = standard_error**2
    concentration = rate * (1.0 - rate) / variance - 1.0
    concentration = min(100000.0, max(2.0, concentration))
    alpha = max(1e-6, rate * concentration)
    beta = max(1e-6, (1.0 - rate) * concentration)
    log_no_event = (
        lgamma(beta + appearances)
        + lgamma(alpha + beta)
        - lgamma(alpha + beta + appearances)
        - lgamma(beta)
    )
    return min(1.0, max(0.0, 1.0 - exp(log_no_event)))


def _weighted_quantile(
    values: list[float], weights: list[float], quantile: float
) -> float:
    if not values:
        return 0.0
    ordered = sorted(zip(values, weights), key=lambda pair: pair[0])
    total_weight = sum(weight for _, weight in ordered)
    if total_weight <= 0:
        return float(np.quantile(values, quantile))
    threshold = total_weight * quantile
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= threshold:
            return float(value)
    return float(ordered[-1][0])


def build_analysis(
    raw_stats: pd.DataFrame,
    player: dict[str, Any],
    games_requested: int,
    lookback_days: int,
    today: date | None = None,
    pitcher_stats: pd.DataFrame | None = None,
    opponent_pitcher: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if raw_stats.empty:
        raise ValueError("Statcast no devolvió registros para este jugador.")
    if not {"game_date", "events"}.issubset(raw_stats.columns):
        raise ValueError("La respuesta de Statcast no contiene las columnas esperadas.")

    today = today or date.today()
    stats, plate_appearances = _prepare_statcast(raw_stats, today)
    if plate_appearances.empty:
        raise ValueError("No se encontraron apariciones al plato completas.")

    game_rows = []
    for game_id, frame in plate_appearances.groupby("_game_id", sort=False):
        events = frame["_event"]
        game_rows.append(
            {
                "game_id": str(game_id),
                "day": frame["_game_day"].min(),
                "pa": int(len(frame)),
                "hits": int(events.isin(HIT_EVENTS).sum()),
                "home_runs": int(events.isin(HOME_RUN_EVENTS).sum()),
                "walks": int(events.isin(WALK_EVENTS).sum()),
                "strikeouts": int(events.isin(STRIKEOUT_EVENTS).sum()),
                "outs": int(events.isin(OUT_EVENTS).sum()),
            }
        )
    game_rows.sort(key=_game_sort_key)
    selected_games = game_rows[-games_requested:]
    selected_game_ids = {row["game_id"] for row in selected_games}
    selected_pa = plate_appearances[
        plate_appearances["_game_id"].isin(selected_game_ids)
    ].copy()

    history_pa = plate_appearances[
        ~plate_appearances["_game_id"].isin(selected_game_ids)
    ].copy()
    all_weights = _date_weights(plate_appearances, today, LONG_TERM_HALF_LIFE_DAYS)
    long_weights = _date_weights(history_pa, today, LONG_TERM_HALF_LIFE_DAYS)
    form_weights = _date_weights(selected_pa, today, FORM_HALF_LIFE_DAYS)
    long_term = {
        metric: _estimate_metric(
            history_pa,
            metric,
            long_weights,
            LEAGUE_PRIORS[metric],
            PRIOR_PLATE_APPEARANCES,
        )
        for metric in LEAGUE_PRIORS
    }
    form = {
        metric: _estimate_metric(
            selected_pa,
            metric,
            form_weights,
            long_term[metric]["rate"],
            FORM_PRIOR_APPEARANCES,
        )
        for metric in LEAGUE_PRIORS
    }
    form_effective_pa = _effective_sample_size(form_weights)
    form_weight = min(
        0.45,
        0.45 * form_effective_pa / (form_effective_pa + FORM_PRIOR_APPEARANCES),
    )
    estimates: dict[str, dict[str, float]] = {}
    for metric in LEAGUE_PRIORS:
        base = long_term[metric]
        recent = form[metric]
        form_delta = form_weight * (recent["rate"] - base["rate"])
        estimates[metric] = {
            "rate": min(1.0, max(0.0, base["rate"] + form_delta)),
            "low": min(1.0, max(0.0, base["low"] + form_delta)),
            "high": min(1.0, max(0.0, base["high"] + form_delta)),
            "long_term_rate": base["rate"],
            "recent_form_rate": recent["rate"],
            "form_weight": form_weight,
        }

    pitcher_info = dict(opponent_pitcher or {})
    pitcher_hand = None
    pitcher_estimates = None
    pitcher_pa_count = 0
    pitcher_effective_pa = 0.0
    pitcher_status = "not_selected"
    if pitcher_info:
        pitcher_info.setdefault("name", "Lanzador rival")
        pitcher_info["id"] = int(pitcher_info["id"])
        if (
            pitcher_stats is not None
            and not pitcher_stats.empty
            and {"game_date", "events"}.issubset(pitcher_stats.columns)
        ):
            _, pitcher_pa = _prepare_statcast(pitcher_stats, today)
            pitcher_pa_count = len(pitcher_pa)
            if not pitcher_pa.empty:
                pitcher_weights = _date_weights(
                    pitcher_pa, today, LONG_TERM_HALF_LIFE_DAYS
                )
                pitcher_effective_pa = _effective_sample_size(pitcher_weights)
                pitcher_estimates = {
                    metric: _estimate_metric(
                        pitcher_pa,
                        metric,
                        pitcher_weights,
                        LEAGUE_PRIORS[metric],
                        PITCHER_PRIOR_APPEARANCES,
                    )
                    for metric in LEAGUE_PRIORS
                }
                if "p_throws" in pitcher_pa:
                    hands = pitcher_pa["p_throws"].dropna().astype(str).str.upper()
                    hands = hands[hands.isin({"L", "R"})]
                    if not hands.empty:
                        pitcher_hand = str(hands.mode().iloc[0])
                pitcher_status = "available"
            else:
                pitcher_status = "no_plate_appearances"
        else:
            pitcher_status = "unavailable"

    base_hit_rate = estimates["hits"]["rate"]
    platoon_hit_rate = None
    platoon_pa_count = 0
    if pitcher_hand and "p_throws" in history_pa:
        hands = history_pa["p_throws"].astype("string").str.upper()
        split_pa = history_pa.loc[hands == pitcher_hand].copy()
        platoon_pa_count = len(split_pa)
        if not split_pa.empty:
            split_weights = long_weights.reindex(split_pa.index).fillna(0.0)
            for metric in LEAGUE_PRIORS:
                split = _estimate_metric(
                    split_pa,
                    metric,
                    split_weights,
                    estimates[metric]["rate"],
                    PLATOON_PRIOR_APPEARANCES,
                )
                estimates[metric]["rate"] = split["rate"]
                estimates[metric]["low"] = split["low"]
                estimates[metric]["high"] = split["high"]
                estimates[metric]["platoon_rate"] = split["rate"]
            platoon_hit_rate = estimates["hits"]["rate"]

    before_pitcher = {metric: dict(value) for metric, value in estimates.items()}
    if pitcher_estimates:
        for metric in LEAGUE_PRIORS:
            batter = estimates[metric]
            allowed = pitcher_estimates[metric]
            batter["rate"] = _log5_rate(
                batter["rate"], allowed["rate"], LEAGUE_PRIORS[metric]
            )
            batter["low"] = _log5_rate(
                batter["low"], allowed["low"], LEAGUE_PRIORS[metric]
            )
            batter["high"] = _log5_rate(
                batter["high"], allowed["high"], LEAGUE_PRIORS[metric]
            )
            batter["pitcher_allowed_rate"] = allowed["rate"]

    def weighted_mean(frame: pd.DataFrame, column: str) -> float | None:
        if column not in frame:
            return None
        values = pd.to_numeric(frame[column], errors="coerce")
        weights = all_weights.reindex(frame.index).fillna(0.0)
        valid = values.notna() & weights.gt(0)
        denominator = float(weights[valid].sum())
        if denominator <= 0:
            return None
        return float((values[valid] * weights[valid]).sum() / denominator)

    recent_games = selected_games[-10:]
    recent_pa_values = [float(row["pa"]) for row in recent_games]
    game_weights = [0.85 ** (len(recent_games) - i - 1) for i in range(len(recent_games))]
    weight_total = sum(game_weights)
    game_weights = [weight / weight_total for weight in game_weights]
    expected_pa = sum(value * weight for value, weight in zip(recent_pa_values, game_weights))
    expected_pa = min(8.0, max(1.0, expected_pa))
    expected_pa_low = min(
        expected_pa,
        max(1.0, _weighted_quantile(recent_pa_values, game_weights, 0.20)),
    )
    expected_pa_high = max(
        expected_pa,
        min(8.0, _weighted_quantile(recent_pa_values, game_weights, 0.80)),
    )

    def chance_for(metric: str) -> float:
        return sum(
            weight * _predictive_any_event_probability(estimates[metric], appearances)
            for appearances, weight in zip(recent_pa_values, game_weights)
        )

    projection = {
        "plate_appearances": expected_pa,
        "plate_appearances_low": expected_pa_low,
        "plate_appearances_high": expected_pa_high,
        "hits": expected_pa * estimates["hits"]["rate"],
        "hits_low": expected_pa_low * estimates["hits"]["low"],
        "hits_high": expected_pa_high * estimates["hits"]["high"],
        "home_runs": expected_pa * estimates["home_runs"]["rate"],
        "walks": expected_pa * estimates["walks"]["rate"],
        "strikeouts": expected_pa * estimates["strikeouts"]["rate"],
        "probability_one_or_more_hits": chance_for("hits"),
        "probability_one_or_more_home_runs": chance_for("home_runs"),
    }

    launch_speed = (
        pd.to_numeric(stats["launch_speed"], errors="coerce").dropna()
        if "launch_speed" in stats
        else pd.Series(dtype=float)
    )
    xba = (
        pd.to_numeric(plate_appearances["estimated_ba_using_speedangle"], errors="coerce")
        if "estimated_ba_using_speedangle" in plate_appearances
        else pd.Series(np.nan, index=plate_appearances.index, dtype=float)
    )
    metrics = {
        "average_exit_velocity": weighted_mean(stats, "launch_speed"),
        "hard_hit_percent": None,
        "average_launch_angle": weighted_mean(stats, "launch_angle"),
        "batted_balls": int(len(launch_speed)),
        "statcast_xba": weighted_mean(plate_appearances.assign(_xba=xba), "_xba"),
        "statcast_xba_contacts": int(xba.notna().sum()),
    }
    if "launch_speed" in stats:
        speed = pd.to_numeric(stats["launch_speed"], errors="coerce")
        hard_hit = stats.assign(
            _hard_hit=(speed >= 95).where(speed.notna())
        )
        hard_hit_rate = weighted_mean(hard_hit, "_hard_hit")
        metrics["hard_hit_percent"] = hard_hit_rate * 100 if hard_hit_rate is not None else None
    if "launch_speed_angle" in stats:
        speed_angle = pd.to_numeric(stats["launch_speed_angle"], errors="coerce")
        barrels = stats.assign(
            _barrel=(speed_angle == 6).where(speed_angle.notna())
        )
        barrel_rate = weighted_mean(barrels, "_barrel")
        metrics["barrel_percent"] = barrel_rate * 100 if barrel_rate is not None else None
    else:
        metrics["barrel_percent"] = None

    effective_sample_size = _effective_sample_size(long_weights)
    confidence_sample_size = min(
        effective_sample_size, pitcher_effective_pa
    ) if pitcher_estimates else effective_sample_size
    confidence = (
        "alta" if confidence_sample_size >= 150
        else "media" if confidence_sample_size >= 60
        else "inicial"
    )
    trend = [
        {
            "date": row["day"].isoformat(),
            "plate_appearances": row["pa"],
            "hits": row["hits"],
            "home_runs": row["home_runs"],
            "hit_rate": row["hits"] / row["pa"] if row["pa"] else 0.0,
        }
        for row in selected_games[-14:]
    ]
    season_trend = []
    plate_appearances_by_season = plate_appearances.assign(
        _season=plate_appearances["_game_day"].map(lambda day: day.year)
    )
    for season, frame in plate_appearances_by_season.groupby("_season", sort=True):
        events = frame["_event"]
        expected_hits = _outcome_values(frame, "hits")
        season_xba = (
            pd.to_numeric(frame["estimated_ba_using_speedangle"], errors="coerce")
            if "estimated_ba_using_speedangle" in frame
            else pd.Series(dtype=float)
        )
        season_speed = (
            pd.to_numeric(frame["launch_speed"], errors="coerce").dropna()
            if "launch_speed" in frame
            else pd.Series(dtype=float)
        )
        season_trend.append(
            {
                "season": int(season),
                "plate_appearances": int(len(frame)),
                "hits": int(events.isin(HIT_EVENTS).sum()),
                "hit_rate": float(events.isin(HIT_EVENTS).mean()),
                "xba_adjusted_rate": float(expected_hits.mean()),
                "home_runs": int(events.isin(HOME_RUN_EVENTS).sum()),
                "strikeouts": int(events.isin(STRIKEOUT_EVENTS).sum()),
                "average_exit_velocity": (
                    float(season_speed.mean()) if not season_speed.empty else None
                ),
                "xba_contacts": int(season_xba.notna().sum()),
            }
        )
    recent_totals = {
        metric: int(sum(row[metric] for row in selected_games))
        for metric in ("pa", "hits", "home_runs", "walks", "strikeouts", "outs")
    }
    totals = {
        metric: int(sum(row[metric] for row in game_rows))
        for metric in ("pa", "hits", "home_runs", "walks", "strikeouts", "outs")
    }
    rate_results = {
        metric: {
            **estimate,
            "count": totals[metric],
            "sample_rate": totals[metric] / totals["pa"] if totals["pa"] else 0.0,
            "recent_count": recent_totals[metric],
            "recent_sample_rate": (
                recent_totals[metric] / recent_totals["pa"] if recent_totals["pa"] else 0.0
            ),
        }
        for metric, estimate in estimates.items()
    }

    insight_difference = estimates["hits"]["rate"] - rate_results["hits"]["sample_rate"]
    if metrics["statcast_xba_contacts"] < 20:
        insight = (
            "Hay pocos contactos con xBA en esta ventana; la proyección combina los "
            "resultados observados con el promedio MLB y reduce el peso de la forma reciente."
        )
    elif insight_difference >= 0.025:
        insight = (
            "La proyección supera el promedio de hits observado en el historial; la calidad "
            "de contacto, la forma y el rival seleccionado explican parte de la diferencia."
        )
    elif insight_difference <= -0.025:
        insight = (
            "La proyección queda debajo del promedio de hits observado; la calidad de "
            "contacto, la forma y el rival moderan la tasa."
        )
    else:
        insight = "La proyección ajustada y el promedio observado del historial están cerca."

    drivers = [
        {
            "label": "Historial de largo plazo",
            "description": (
                f"{len(game_rows)} juegos y {totals['pa']} PA; los datos antiguos pierden "
                "peso gradualmente con una vida media de un año."
            ),
        },
        {
            "label": "Forma reciente",
            "description": (
                f"Últimos {len(selected_games)} juegos y {form_effective_pa:.0f} PA efectivas; "
                "la señal se reduce hacia el nivel histórico según su tamaño de muestra."
            ),
            "hit_rate_change": base_hit_rate - long_term["hits"]["rate"],
        },
    ]
    if pitcher_hand:
        drivers.append(
            {
                "label": f"Bateador contra pitcher {pitcher_hand}HP",
                "description": (
                    f"{platoon_pa_count} PA contra esa mano; el split se regresa hacia la "
                    f"tasa general con un prior de {int(PLATOON_PRIOR_APPEARANCES)} PA."
                ),
                "hit_rate_change": (platoon_hit_rate or base_hit_rate) - base_hit_rate,
            }
        )
    if pitcher_info:
        if pitcher_estimates:
            drivers.append(
                {
                    "label": f"Rival: {pitcher_info['name']}",
                    "description": (
                        f"{pitcher_pa_count} PA ({pitcher_effective_pa:.0f} efectivas) "
                        "en el historial del lanzador; su tasa "
                        "permitida se combina con la del bateador mediante log5."
                    ),
                    "hit_rate_change": (
                        estimates["hits"]["rate"] - before_pitcher["hits"]["rate"]
                    ),
                }
            )
        else:
            drivers.append(
                {
                    "label": f"Rival: {pitcher_info['name']}",
                    "description": (
                        "Statcast no devolvió apariciones suficientes del lanzador; se usa "
                        "el perfil del bateador sin ajuste de rival."
                    ),
                }
            )

    return {
        "player": player,
        "opponent_pitcher": {
            **pitcher_info,
            "status": pitcher_status,
            "hand": pitcher_hand,
            "historical_plate_appearances": pitcher_pa_count,
        } if pitcher_info else None,
        "window": {
            "games_requested": games_requested,
            "games_found": len(selected_games),
            "games_available": len(game_rows),
            "lookback_days": lookback_days,
            "from": selected_games[0]["day"].isoformat(),
            "through": selected_games[-1]["day"].isoformat(),
            "historical_from": game_rows[0]["day"].isoformat(),
            "historical_through": game_rows[-1]["day"].isoformat(),
            "seasons": len({row["day"].year for row in game_rows}),
        },
        "sample": {
            "plate_appearances": totals["pa"],
            "recent_plate_appearances": recent_totals["pa"],
            "effective_plate_appearances": effective_sample_size,
            "form_effective_plate_appearances": form_effective_pa,
            "pitcher_effective_plate_appearances": pitcher_effective_pa or None,
            "confidence_basis": "batter_pitcher_minimum" if pitcher_estimates else "batter_history",
            "confidence": confidence,
        },
        "per_plate_appearance": rate_results,
        "projection": projection,
        "contact": metrics,
        "insight": insight,
        "drivers": drivers,
        "trend": trend,
        "season_trend": season_trend,
        "model": {
            "name": "Modelo bayesiano de trayectoria + matchup log5 + beta-binomial",
            "half_life_days": LONG_TERM_HALF_LIFE_DAYS,
            "form_half_life_days": FORM_HALF_LIFE_DAYS,
            "prior_plate_appearances": PRIOR_PLATE_APPEARANCES,
            "league_hit_prior": "0.220 H/PA; prior is on plate appearances, not at-bats.",
            "confidence_interval": "intervalo Wilson aproximado del 90%",
            "hit_estimate": (
                "xBA reemplaza el resultado observado en contactos rastreados; las demás "
                "PA conservan su resultado. La forma reciente se reduce según su muestra."
            ),
            "plate_appearance_forecast": (
                "Distribución ponderada de las últimas diez apariciones por juego."
            ),
            "matchup_method": (
                "La mano usa el split histórico del bateador; log5 combina sus tasas con "
                "las tasas permitidas por el pitcher, ambas estabilizadas hacia MLB."
            ),
            "event_probability_method": (
                "Beta-binomial posterior predictive; integrates the rate uncertainty "
                "and the recent distribution of plate appearances."
            ),
            "out_rate_definition": "Outs incluye ponches; K aparece aparte como subconjunto.",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "source": "Baseball Savant Statcast",
        "note": (
            "El modelo usa todo el historial de la ventana, con vida media de un año, "
            "y una forma reciente suavizada hacia ese nivel. El rango combina una banda "
            "Wilson aproximada del 90% con la variación reciente de PA. La probabilidad "
            "de 1+ evento integra la incertidumbre de tasa con un modelo beta-binomial; "
            "el ajuste de pitcher solo se aplica si seleccionas un rival."
        ),
    }

def build_rolling_backtest(
    raw_stats: pd.DataFrame,
    player: dict[str, Any],
    games_requested: int,
    lookback_days: int,
    today: date | None = None,
    max_predictions: int = 25,
) -> dict[str, Any]:
    """Evaluate future games using only the preceding games in each forecast window."""
    if raw_stats.empty or not {"game_date", "events"}.issubset(raw_stats.columns):
        return {
            "status": "insufficient",
            "predictions": 0,
            "message": "No hay datos suficientes para validar el modelo.",
        }

    today = today or date.today()
    stats = raw_stats.copy()
    stats["_game_day"] = pd.to_datetime(
        stats["game_date"], errors="coerce", utc=True
    ).dt.tz_convert(None).dt.date
    stats = stats.dropna(subset=["_game_day"])
    stats = stats[stats["_game_day"] <= today].copy()
    if "game_pk" in stats:
        stats["_game_id"] = stats["game_pk"].astype("string").fillna(
            stats["_game_day"].astype("string")
        )
    else:
        stats["_game_id"] = stats["_game_day"].astype("string")

    plate_appearances = stats.loc[stats["events"].notna()].copy()
    if plate_appearances.empty:
        return {
            "status": "insufficient",
            "predictions": 0,
            "message": "No se encontraron apariciones completas para validar.",
        }
    plate_appearances["_event"] = (
        plate_appearances["events"].astype("string").str.lower()
    )

    game_rows = []
    for game_id, frame in plate_appearances.groupby("_game_id", sort=False):
        events = frame["_event"]
        game_rows.append(
            {
                "game_id": str(game_id),
                "day": frame["_game_day"].min(),
                "plate_appearances": int(len(frame)),
                "hits": int(events.isin(HIT_EVENTS).sum()),
            }
        )
    game_rows.sort(key=_game_sort_key)

    eligible_targets = []
    for index, target in enumerate(game_rows):
        cutoff = target["day"] - timedelta(days=lookback_days)
        history = [
            row
            for row in game_rows[:index]
            if cutoff <= row["day"]
        ]
        if len(history) >= games_requested:
            eligible_targets.append((target, history))

    eligible_targets = eligible_targets[-max_predictions:]
    if not eligible_targets:
        return {
            "status": "insufficient",
            "predictions": 0,
            "message": (
                "Se necesitan más juegos previos dentro de la ventana seleccionada "
                "para validar estas proyecciones."
            ),
        }

    observations = []
    for target, history in eligible_targets:
        history_ids = {row["game_id"] for row in history}
        history_stats = stats[stats["_game_id"].isin(history_ids)].copy()
        forecast = build_analysis(
            history_stats,
            player,
            games_requested=games_requested,
            lookback_days=lookback_days,
            today=target["day"],
        )
        projection = forecast["projection"]
        actual_any_hit = int(target["hits"] > 0)
        baseline_rate = forecast["per_plate_appearance"]["hits"]["recent_sample_rate"]
        observations.append(
            {
                "forecast_hits": float(projection["hits"]),
                "baseline_hits": float(projection["plate_appearances"] * baseline_rate),
                "forecast_any_hit": float(projection["probability_one_or_more_hits"]),
                "actual_hits": float(target["hits"]),
                "actual_plate_appearances": float(target["plate_appearances"]),
                "actual_any_hit": actual_any_hit,
                "forecast_plate_appearances": float(projection["plate_appearances"]),
                "date": target["day"],
            }
        )

    count = len(observations)
    hit_mae = float(np.mean([
        abs(row["forecast_hits"] - row["actual_hits"]) for row in observations
    ]))
    baseline_mae = float(np.mean([
        abs(row["baseline_hits"] - row["actual_hits"]) for row in observations
    ]))
    pa_mae = float(np.mean([
        abs(row["forecast_plate_appearances"] - row["actual_plate_appearances"])
        for row in observations
    ]))
    brier = float(np.mean([
        (row["forecast_any_hit"] - row["actual_any_hit"]) ** 2
        for row in observations
    ]))
    forecast_hit_rate = float(np.mean([row["forecast_any_hit"] for row in observations]))
    actual_hit_rate = float(np.mean([row["actual_any_hit"] for row in observations]))
    improvement = (
        float((baseline_mae - hit_mae) / baseline_mae)
        if baseline_mae > 0
        else None
    )
    status = "available" if count >= 8 else "limited"
    if status == "available":
        comparison = (
            "El modelo tuvo menor error de hits que la referencia reciente."
            if hit_mae < baseline_mae
            else "La referencia de tasa reciente tuvo menor o igual error de hits en esta muestra."
        )
        message = comparison + " Es una validación histórica, no una garantía para el próximo juego."
    else:
        message = (
            f"Solo hay {count} juegos de validación; la lectura es preliminar y no concluyente."
        )

    return {
        "status": status,
        "predictions": count,
        "from": observations[0]["date"].isoformat(),
        "through": observations[-1]["date"].isoformat(),
        "mean_absolute_error_hits": hit_mae,
        "recent_rate_baseline_mae_hits": baseline_mae,
        "mae_improvement_vs_recent_rate": improvement,
        "mean_absolute_error_plate_appearances": pa_mae,
        "brier_score_one_or_more_hits": brier,
        "mean_predicted_probability_one_or_more_hits": forecast_hit_rate,
        "observed_probability_one_or_more_hits": actual_hit_rate,
        "calibration_gap": abs(forecast_hit_rate - actual_hit_rate),
        "method": "rolling_origin",
        "message": message,
    }
