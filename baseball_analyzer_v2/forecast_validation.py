"""Archive pregame variants and compare them with final MLB batting lines."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
from math import isfinite
from pathlib import Path
from uuid import uuid4

from baseball_analyzer_v2.analytics import normalize_game_type
from baseball_analyzer_v2.forecast_metrics import score_hit_buckets
from baseball_analyzer_v2.projections import build_projection
from baseball_analyzer_v2.sources import MLB, SourceUnavailable, public_json
from baseball_analyzer_v2.storage import CACHE_ROOT, atomic_json

BUCKET_FIELDS = ("probability_zero_hits", "probability_exactly_one_hit", "probability_two_or_more_hits")
SCORES = ("ranked_probability_score", "multiclass_log_loss", "multiclass_brier_score",
          "mean_absolute_error_hits", "mean_absolute_error_plate_appearances")


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Se requiere una hora con zona horaria.")
    return parsed.astimezone(timezone.utc)


def _valid_pregame(context: dict | None, instant: datetime) -> bool:
    game = (context or {}).get("game", {})
    if game.get("status_code") != "Preview" or not game.get("start_time"):
        return False
    return instant < _utc(game["start_time"])


def archive_forecast(root: Path, result: dict, raw_stats, *, games: int, lookback_days: int, cutoff: date,
                     pitcher_stats=None, opponent_pitcher: dict | None = None, inputs: dict | None = None) -> Path:
    """Archive the served forecast and exact pregame alternatives with one factor removed."""
    context = result.get("game_context")
    inputs = inputs or {}
    archive = {"schema": 1, "kind": "profile" if not context else "pregame", "variants": {}}
    if _valid_pregame(context, datetime.now(timezone.utc)):
        has_order = bool(inputs.get("batting_order"))
        has_starter_duration = bool(opponent_pitcher and inputs.get("starter_batters_faced"))
        has_starter_history = (result.get("opponent_pitcher") or {}).get("status") == "available"
        bullpen_batters = ((inputs.get("bullpen") or {}).get("totals") or {}).get("battersFaced", 0)
        factors = {
            "lineup": has_order and (bool(inputs.get("team_plate_appearances")) or has_starter_duration),
            "starter": has_starter_duration or has_starter_history,
            "bullpen": bullpen_batters > 0,
        }
        for factor, supported in factors.items():
            if not supported:
                continue
            variant_inputs = deepcopy(inputs)
            variant_pitcher, variant_opponent = pitcher_stats, opponent_pitcher
            if factor == "lineup":
                for key in ("batting_order", "lineup_confirmed", "team_plate_appearances"):
                    variant_inputs.pop(key, None)
            elif factor == "starter":
                variant_inputs.pop("starter_batters_faced", None)
                variant_inputs.pop("starter", None)
                variant_pitcher, variant_opponent = None, None
            else:
                variant_inputs.pop("bullpen", None)
            variant = build_projection(raw_stats, result["player"], games, lookback_days, cutoff,
                                       variant_pitcher, variant_opponent, context, variant_inputs)
            archive["variants"][f"without_{factor}"] = {
                key: variant["projection"][key] for key in (*BUCKET_FIELDS, "hits", "plate_appearances")
            }
    # This timestamp bounds both the served forecast and the ablations conservatively.
    archived_at = datetime.now(timezone.utc)
    archive["issued_at"] = archived_at.isoformat()
    if context and not _valid_pregame(context, archived_at):
        archive["kind"] = "late"
    path = root / "forecasts" / (uuid4().hex + ".json")
    atomic_json(path, {**result, "forecast_archive": archive})
    return path


@dataclass(frozen=True)
class ArchivedForecast:
    archive_id: str
    game_id: int
    player_id: int
    model_version: str
    issued_at: datetime
    data_cutoff: date
    projection: dict
    variants: dict


def _prediction(projection: dict) -> dict:
    fields = {key: float(projection[key]) for key in (*BUCKET_FIELDS, "hits", "plate_appearances")}
    probabilities = [fields[key] for key in BUCKET_FIELDS]
    if (any(not isfinite(value) or value < 0 for value in fields.values())
            or any(value > 1 for value in probabilities) or abs(sum(probabilities) - 1) > 1e-6):
        raise ValueError("Distribución o conteos inválidos.")
    interval = projection.get("predictive_interval_hits")
    if isinstance(interval, dict):
        low, high = interval.get("low"), interval.get("high")
        if isinstance(low, int) and isinstance(high, int) and 0 <= low <= high:
            fields["predictive_interval_hits"] = {"low": low, "high": high}
    return fields


def _read_archive(path: Path) -> tuple[ArchivedForecast | None, str | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        context = data.get("game_context")
        archive = data.get("forecast_archive", {})
        kind = archive.get("kind")
        if not context or kind == "profile":
            return None, "profile_only"
        if kind is not None and kind != "pregame":
            return None, "not_pregame"
        game, model = context["game"], data["model"]
        # The 2.1 archive stored the projection directly, with the same pregame timestamps.
        generated = _utc(model["generated_at"])
        issued = _utc(archive["issued_at"]) if "issued_at" in archive else generated
        context_at = _utc(context["as_of"]) if context.get("as_of") else generated
        if issued < max(generated, context_at) or not _valid_pregame(context, issued):
            return None, "not_pregame"
        game_id, player_id = int(game["id"]), int(data["player"]["id"])
        version = model["version"]
        if game_id <= 0 or player_id <= 0 or not isinstance(version, str) or not version.strip():
            raise ValueError("Identidad del pronóstico incompleta.")
        variants = {}
        for factor in ("lineup", "starter", "bullpen"):
            name = f"without_{factor}"
            raw_variant = archive.get("variants", {}).get(name)
            if raw_variant is None:
                continue
            try:
                variants[name] = _prediction(raw_variant)
            except (TypeError, ValueError, KeyError, OverflowError):
                # A damaged optional ablation cannot invalidate a valid primary forecast.
                continue
        return ArchivedForecast(path.stem, game_id, player_id, version.strip(), issued,
                                date.fromisoformat(model["data_cutoff_exclusive"]), _prediction(data["projection"]),
                                variants), None
    except (OSError, ValueError, AttributeError, TypeError, KeyError, OverflowError):
        return None, "invalid_archive"


def _first_pitch(game_data: dict, live_data: dict) -> datetime | None:
    value = game_data.get("gameInfo", {}).get("firstPitch")
    if isinstance(value, str):
        try:
            return _utc(value)
        except ValueError:
            pass
    times = []
    for play in live_data.get("plays", {}).get("allPlays", []):
        for event in play.get("playEvents", []):
            time = event.get("startTime")
            if event.get("isPitch") is True and isinstance(time, str):
                try:
                    times.append(_utc(time))
                except ValueError:
                    continue
    return min(times, default=None)


def fetch_final_game(game_id: int) -> dict:
    """Resolve a final game's actual first pitch and official batting counts by player ID."""
    feed, freshness = public_json(f"{MLB}/api/v1.1/game/{game_id}/feed/live", ttl=300)
    gd, ld = feed.get("gameData", {}), feed.get("liveData", {})
    if (not isinstance(gd, dict) or not isinstance(ld, dict)
            or feed.get("gamePk", gd.get("game", {}).get("pk")) != game_id):
        return {"status": "missing_game_data"}
    status = gd.get("status", {})
    if status.get("abstractGameState") != "Final":
        return {"status": "not_final"}
    first_pitch = _first_pitch(gd, ld)
    official_date = gd.get("datetime", {}).get("officialDate")
    teams = ld.get("boxscore", {}).get("teams", {})
    if not first_pitch:
        return {"status": "missing_actual_first_pitch"}
    if not official_date or not all(side in teams for side in ("home", "away")):
        return {"status": "missing_boxscore"}
    game_type_code = gd.get("game", {}).get("type")
    try:
        game_type = normalize_game_type(game_type_code)
    except ValueError:
        game_type = "unknown"
    batters = {}
    for side in ("home", "away"):
        team = teams[side]
        batting_players = set(team.get("batters", []))
        for key, player in team.get("players", {}).items():
            player_id = player.get("person", {}).get("id")
            if type(player_id) is not int or key != f"ID{player_id}":
                continue
            batting = player.get("stats", {}).get("batting", {})
            hits, pa = batting.get("hits"), batting.get("plateAppearances")
            if type(hits) is int and type(pa) is int and 0 <= hits <= pa:
                batters[player_id] = {"hits": hits, "plate_appearances": pa}
            elif batting == {} and player_id not in batting_players:
                # Empty batting statistics occur on bench and pitching-only players.
                batters[player_id] = {"hits": 0, "plate_appearances": 0}
    return {"status": "final", "first_pitch": first_pitch, "official_date": date.fromisoformat(official_date),
            "game_type": game_type, "game_type_code": game_type_code, "batters": batters,
            "source": {"provider": "MLB Stats API", **freshness}}


def _score(rows: list[dict], key: str = "projection") -> dict:
    if not rows:
        return {"predictions": 0}
    projections = [row[key] for row in rows]
    metrics = score_hit_buckets([[p[field] for field in BUCKET_FIELDS] for p in projections],
                                [row["actual_hits"] for row in rows])
    metrics["mean_absolute_error_hits"] = sum(abs(p["hits"] - row["actual_hits"])
                                              for p, row in zip(projections, rows)) / len(rows)
    metrics["mean_absolute_error_plate_appearances"] = sum(abs(p["plate_appearances"] - row["actual_plate_appearances"])
                                                           for p, row in zip(projections, rows)) / len(rows)
    ranges = [(p["predictive_interval_hits"], row["actual_hits"]) for p, row in zip(projections, rows)
              if "predictive_interval_hits" in p]
    if ranges:
        metrics["predictive_interval_coverage"] = sum(r["low"] <= actual <= r["high"] for r, actual in ranges) / len(ranges)
        metrics["predictive_interval_predictions"] = len(ranges)
    return metrics


def _version_summary(rows: list[dict]) -> dict:
    types = sorted({row["game_type"] for row in rows})
    comparisons = {}
    for factor in ("lineup", "starter", "bullpen"):
        key = f"without_{factor}"
        paired = [{**row, key: row["variants"][key]} for row in rows if key in row["variants"]]
        full, without = _score(paired), _score(paired, key)
        comparisons[factor] = {"paired_predictions": len(paired), "with_factor": full, "without_factor": without,
                               "delta_with_minus_without": {k: full[k] - without[k] for k in SCORES if k in full}}
    return {"metrics": _score(rows), "games": len({row["game_id"] for row in rows}),
            "players": len({row["player_id"] for row in rows}),
            "by_game_type": {kind: _score([row for row in rows if row["game_type"] == kind]) for kind in types},
            "factor_comparisons": comparisons}


def evaluate_archived(root: Path, lead_minutes: int = 30, max_age_hours: int = 24,
                      *, progress: Callable[[int, int], None] | None = None) -> dict:
    """Select the latest eligible forecast per game/player/model and join final MLB stats."""
    if lead_minutes < 0 or max_age_hours <= 0 or lead_minutes > max_age_hours * 60:
        raise ValueError("La antelación debe estar entre cero y la edad máxima del pronóstico.")
    exclusions: Counter = Counter()
    groups = defaultdict(list)
    paths = sorted((root / "forecasts").glob("*.json"))
    for path in paths:
        forecast, reason = _read_archive(path)
        if forecast:
            groups[(forecast.game_id, forecast.player_id, forecast.model_version)].append(forecast)
        else:
            exclusions[reason] += 1
    lead, max_age = timedelta(minutes=lead_minutes), timedelta(hours=max_age_hours)
    games = {}
    rows = []
    for i, ((game_id, player_id, version), candidates) in enumerate(sorted(groups.items())):
        if progress:
            progress(i, len(groups))
        if game_id not in games:
            try:
                games[game_id] = fetch_final_game(game_id)
            except SourceUnavailable:
                games[game_id] = {"status": "source_unavailable"}
            except (AttributeError, KeyError, TypeError, ValueError):
                games[game_id] = {"status": "missing_game_data"}
        final = games[game_id]
        if final["status"] != "final":
            exclusions[final["status"]] += len(candidates)
            continue
        first_pitch = final["first_pitch"]
        actual = final["batters"].get(player_id)
        if not actual:
            exclusions["missing_batting_line"] += len(candidates)
            continue
        if not actual["plate_appearances"]:
            exclusions["no_plate_appearances"] += len(candidates)
            continue
        eligible = []
        for forecast in candidates:
            elapsed = first_pitch - forecast.issued_at
            if forecast.data_cutoff > final["official_date"] or elapsed <= timedelta(0):
                exclusions["after_actual_cutoff"] += 1
            elif not lead <= elapsed <= max_age:
                exclusions["outside_horizon"] += 1
            else:
                eligible.append(forecast)
        if not eligible:
            continue
        # Stable tie-break when two files carry the exact same timestamp.
        selected = max(eligible, key=lambda f: (f.issued_at, f.archive_id))
        exclusions["superseded"] += len(eligible) - 1
        rows.append({"archive_id": selected.archive_id, "game_id": game_id, "player_id": player_id,
                     "model_version": version, "issued_at": selected.issued_at.isoformat(),
                     "first_pitch": first_pitch.isoformat(), "official_date": final["official_date"].isoformat(),
                     "game_type": final["game_type"], "game_type_code": final.get("game_type_code"),
                     "actual_hits": actual["hits"],
                     "actual_plate_appearances": actual["plate_appearances"], "projection": selected.projection,
                     "variants": selected.variants, "outcome_source": final["source"]})
    if progress:
        progress(len(groups), len(groups))
    versions = sorted({row["model_version"] for row in rows})
    return {"status": "available" if rows else "insufficient", "predictions": len(rows),
            "generated_at": datetime.now(timezone.utc).isoformat(), "archive_files": len(paths),
            "horizon": {"minimum_lead_minutes": lead_minutes, "maximum_age_hours": max_age_hours,
                        "relative_to": "actual_first_pitch", "selection": "latest_by_game_player_model_version"},
            "excluded_archives": {k: v for k, v in sorted(exclusions.items()) if v},
            "by_model_version": {v: _version_summary([row for row in rows if row["model_version"] == v]) for v in versions},
            "observations": rows,
            "scope": "Resultados oficiales cuando el bateador tuvo al menos una aparición. Comparaciones emparejadas "
                     "en pronósticos archivados antes del juego. Menor error favorece la variante comparada; muestras "
                     "de usuarios y temporadas no necesariamente representativas. No se reconstruyen pronósticos pasados."}


def main():
    parser = argparse.ArgumentParser(description="Evalúa pronósticos guardados contra resultados finales de MLB.")
    parser.add_argument("--data-dir", type=Path, default=CACHE_ROOT)
    parser.add_argument("--lead-minutes", type=int, default=30)
    parser.add_argument("--max-age-hours", type=int, default=24)
    parser.add_argument("--output", type=Path, help="Por defecto, evaluation/prospective.json dentro de --data-dir.")
    args = parser.parse_args()
    try:
        report = evaluate_archived(args.data_dir, args.lead_minutes, args.max_age_hours)
    except ValueError as exc:
        parser.error(str(exc))
    output = args.output or args.data_dir / "evaluation" / "prospective.json"
    atomic_json(output, report)
    print(f"{report['predictions']} pronósticos con resultado final, de {report['archive_files']} archivos examinados.")
    print(f"Reporte: {output}")


if __name__ == "__main__":
    main()
