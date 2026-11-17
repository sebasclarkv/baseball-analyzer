"""Local API for game context, explainable projections and temporal evaluation."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from baseball_analyzer_v2 import __version__, jobs
from baseball_analyzer_v2.data import fetch_batter_statcast, fetch_pitcher_statcast, search_players
from baseball_analyzer_v2.evaluation import evaluate_player
from baseball_analyzer_v2.forecast_validation import archive_forecast, evaluate_archived
from baseball_analyzer_v2.live import game_live
from baseball_analyzer_v2.live_hitter import project_live_hitter
from baseball_analyzer_v2.projections import build_projection
from baseball_analyzer_v2.sources import SourceUnavailable, game_context, matchup_inputs, schedule
from baseball_analyzer_v2.storage import CACHE_ROOT, atomic_json

LOGGER = logging.getLogger("baseball_analyzer")
STATIC_ROOT = Path(__file__).resolve().parent / "static"
app = FastAPI(title="Diamond IQ", description="Contexto MLB y proyecciones locales explicables.", version=__version__)
app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")


class AnalysisRequest(BaseModel):
    player_id: int = Field(gt=0)
    player_name: str = Field(min_length=2, max_length=100)
    opponent_pitcher_id: int | None = Field(default=None, gt=0)
    opponent_pitcher_name: str | None = Field(default=None, min_length=2, max_length=100)
    game_id: int | None = Field(default=None, gt=0)
    games: int = Field(default=40, ge=5, le=150)
    lookback_days: int = Field(default=1095, ge=30, le=5000)
    max_predictions: int = Field(default=100, ge=1, le=500)


class ScenarioRequest(AnalysisRequest):
    game_id: int = Field(gt=0)
    scenario_pitcher_id: int = Field(gt=0)
    scenario_pitcher_name: str | None = Field(default=None, min_length=2, max_length=100)


class LiveHitterRequest(BaseModel):
    player_id: int | None = Field(default=None, gt=0)
    snapshot_id: str | None = Field(default=None, min_length=1, max_length=128)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_ROOT / "index.html")


@app.get("/service-worker.js", include_in_schema=False)
def service_worker():
    # A worker under /static/ cannot control the application at /.
    return FileResponse(STATIC_ROOT / "service-worker.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@app.get("/api/health")
def health():
    return {"app": "diamond-iq", "status": "ok", "version": __version__, "data_source": "MLB Stats API / Statcast / Open-Meteo"}


@app.get("/api/players")
def players(query: str = Query(min_length=2, max_length=80)):
    try:
        return {"players": search_players(query)}
    except Exception as exc:
        LOGGER.exception("Player registry failed")
        raise HTTPException(502, "No se pudo cargar el registro público de jugadores.") from exc


@app.get("/api/games")
def games(date: date = Query(...)):
    try:
        return schedule(date)
    except SourceUnavailable as exc:
        raise HTTPException(502, "No se pudo actualizar la jornada MLB. Puedes analizar un jugador sin seleccionar partido.") from exc


@app.get("/api/games/{game_id}/context")
def context(game_id: int):
    if game_id <= 0:
        raise HTTPException(422, "El identificador del partido debe ser positivo.")
    try:
        return game_context(game_id)
    except SourceUnavailable as exc:
        raise HTTPException(502, str(exc)) from exc


def _live_snapshot(game_id: int, at: datetime | None, *, include_win: bool = True) -> dict:
    if game_id <= 0:
        raise HTTPException(422, "El identificador del partido debe ser positivo.")
    if at is not None and at.utcoffset() is None:
        raise HTTPException(422, "La hora de repetición debe incluir su zona horaria.")
    if at is not None and at > datetime.now(timezone.utc):
        raise HTTPException(422, "Elige una hora de repetición que ya haya pasado.")
    try:
        return game_live(game_id, as_of=at, include_win=include_win)
    except SourceUnavailable as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/games/{game_id}/live")
def live_game(game_id: int, response: Response, at: datetime | None = None):
    response.headers["Cache-Control"] = "no-store"
    return _live_snapshot(game_id, at)


@app.post("/api/games/{game_id}/live/hitter")
def live_hitter(game_id: int, request: LiveHitterRequest, at: datetime | None = None):
    snapshot = _live_snapshot(game_id, at, include_win=False)
    if request.snapshot_id is not None and request.snapshot_id != snapshot["snapshot_id"]:
        raise HTTPException(409, "La situación cambió. Actualiza el tablero y vuelve a calcular.")
    if snapshot["game"]["phase"] != "live" or snapshot["source"]["stale"]:
        raise HTTPException(409, "El análisis requiere una actualización de MLB con el partido en juego.")

    current_batter = snapshot.get("batter") or {}
    player_id = request.player_id or current_batter.get("id")
    side, player = next(
        ((side, batter) for side in ("away", "home") for batter in snapshot["game"][side]["lineup"]
         if batter["id"] == player_id),
        (None, None),
    )
    if not player and player_id and player_id == current_batter.get("id") and snapshot.get("plate_appearance_active"):
        side, player = snapshot.get("batting_side"), current_batter
    if side is None or player is None:
        raise HTTPException(422, "Selecciona un bateador que siga en la alineación. El orden puede cambiar durante el juego.")

    batting_now = side == snapshot.get("batting_side")
    opponent = snapshot["game"]["home" if side == "away" else "away"]
    pitcher = (snapshot.get("pitcher") if batting_now else None) or opponent.get("current_pitcher")
    current_pa = bool(batting_now and current_batter.get("id") == player_id and snapshot.get("plate_appearance_active"))
    try:
        cutoff = date.fromisoformat(snapshot["game"]["official_date"])
        estimate = project_live_hitter(
            player={"id": player_id, "name": player["name"]},
            pitcher=pitcher,
            official_date=cutoff,
            game_type=snapshot["game"].get("game_type") or "regular",
            count=snapshot.get("count") if current_pa else None,
            current_plate_appearance=current_pa,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        LOGGER.exception("Live hitter estimate unavailable")
        raise HTTPException(502, "No se pudo consultar el historial del bateador en Statcast.") from exc
    return {"snapshot_id": snapshot["snapshot_id"], "observed_at": snapshot.get("snapshot_at"),
            "player": {"id": player_id, "name": player["name"]}, "pitcher": pitcher,
            "hits": player.get("hits"), "plate_appearances": player.get("plate_appearances"),
            "pitcher_reference": "current" if batting_now and snapshot.get("pitcher") else "last_known",
            "current_plate_appearance": current_pa, "estimate": estimate}


def history(request: AnalysisRequest, cutoff: date):
    end = min(cutoff - timedelta(days=1), datetime.now(timezone.utc).date() - timedelta(days=1))
    start = max(date(2015, 1, 1), cutoff - timedelta(days=request.lookback_days))
    return fetch_batter_statcast(request.player_id, start.isoformat(), end.isoformat()), start, end


@app.post("/api/analyze")
def analyze(request: AnalysisRequest):
    return _project(request)


@app.post("/api/scenario")
def scenario(request: ScenarioRequest):
    return _project(request, scenario_pitcher_id=request.scenario_pitcher_id)


def _project(request: AnalysisRequest, *, scenario_pitcher_id: int | None = None) -> dict:
    try:
        ctx, inputs = None, {}
        cutoff = datetime.now(timezone.utc).date()
        opponent = {"id": request.opponent_pitcher_id, "name": request.opponent_pitcher_name or "Lanzador rival"} if request.opponent_pitcher_id else None
        if request.game_id:
            ctx = game_context(request.game_id)
            start_time = ctx["game"].get("start_time")
            if not start_time:
                raise HTTPException(422, "MLB aún no publica hora para este partido. Usa el perfil sin partido.")
            start_at = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            if ctx["game"]["status_code"] != "Preview" or start_at <= datetime.now(timezone.utc):
                raise HTTPException(422, "Este partido ya inició o terminó. La proyección es previa al juego; puedes consultar el perfil del jugador sin partido.")
            cutoff = date.fromisoformat(ctx["game"].get("official_date") or start_at.date().isoformat())
            if scenario_pitcher_id is not None:
                inputs = matchup_inputs(ctx, request.player_id, scenario_pitcher_id=scenario_pitcher_id)
            else:
                inputs = matchup_inputs(ctx, request.player_id)
            auto = inputs.get("starter")
            if scenario_pitcher_id is None and opponent and (not auto or auto["id"] != opponent["id"]):
                raise HTTPException(422, "El pitcher seleccionado no coincide con el probable de este partido. Actualiza el partido o analiza sin partido.")
            opponent = auto
        stats, start, end = history(request, cutoff)
        pitcher = None
        if opponent:
            try:
                pitcher = fetch_pitcher_statcast(opponent["id"], start.isoformat(), end.isoformat())
            except Exception:
                LOGGER.exception("Pitcher history unavailable")
        result = build_projection(stats, {"id": request.player_id, "name": request.player_name.strip()}, request.games,
                                  request.lookback_days, cutoff, pitcher, opponent, ctx, inputs)
        if scenario_pitcher_id is not None:
            opposing_side = "home" if inputs["own_side"] == "away" else "away"
            result["scenario"] = {"type": "hypothetical_starter", "pitcher": opponent,
                                  "official_starter": ctx["game"][opposing_side]["probable_pitcher"]}
            return result
        # Freeze forecasts before a result exists, allowing future prospective evaluation.
        try:
            archive_forecast(CACHE_ROOT, result, stats, games=request.games, lookback_days=request.lookback_days,
                             cutoff=cutoff, pitcher_stats=pitcher, opponent_pitcher=opponent, inputs=inputs)
        except (OSError, ValueError):
            LOGGER.exception("Forecast archive failed")
            result["data_quality"]["warnings"].append("No se pudo archivar la proyección en disco.")
        return result
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        LOGGER.exception("Projection unavailable")
        raise HTTPException(502, "No se pudo completar la consulta pública. Revisa el acceso a MLB/Statcast; los reportes guardados siguen disponibles.") from exc


def _evaluation(request: AnalysisRequest, progress=None):
    raw, _, _ = history(request, datetime.now(timezone.utc).date())
    return evaluate_player(raw, {"id": request.player_id, "name": request.player_name}, request.games,
                           request.lookback_days, request.max_predictions, progress)


@app.post("/api/backtest")
def backtest(request: AnalysisRequest):
    try:
        return _evaluation(request)
    except Exception as exc:
        LOGGER.exception("Evaluation failed")
        raise HTTPException(502, "No se pudo evaluar el historial de Statcast.") from exc


@app.post("/api/backtest/jobs", status_code=202)
def backtest_job(request: AnalysisRequest):
    try:
        return jobs.submit(lambda progress: _evaluation(request, progress))
    except ValueError as exc:
        raise HTTPException(429, str(exc)) from exc


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Esta evaluación ya no está disponible; inicia otra.")
    return job


@app.get("/api/evaluation")
def evaluation_report():
    try:
        return json.loads((CACHE_ROOT / "evaluation" / "latest.json").read_text())
    except (OSError, ValueError):
        return {"status": "not_run", "predictions": 0, "message": "Aún no se ejecutó la evaluación multijugador. Consulta las instrucciones de evaluación del repositorio."}


@app.get("/api/evaluation/prospective")
def prospective_report(summary: bool = False):
    try:
        report = json.loads((CACHE_ROOT / "evaluation" / "prospective.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "not_run", "predictions": 0, "message": "Aún no se cruzaron los pronósticos con resultados de MLB."}
    if summary:
        report.pop("observations", None)
    return report


def _refresh_prospective(progress):
    report = evaluate_archived(CACHE_ROOT, progress=progress)
    atomic_json(CACHE_ROOT / "evaluation" / "prospective.json", report)
    report.pop("observations", None)
    return report


@app.post("/api/evaluation/prospective/jobs", status_code=202)
def prospective_job():
    try:
        return jobs.submit(_refresh_prospective,
                           error_message="No se pudo completar el cruce con MLB. Revisa la conexión y el archivo local.")
    except ValueError as exc:
        raise HTTPException(429, str(exc)) from exc
