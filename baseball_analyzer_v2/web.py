"""Local API for game context, explainable projections and temporal evaluation."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from baseball_analyzer_v2 import __version__, jobs
from baseball_analyzer_v2.data import fetch_batter_statcast, fetch_pitcher_statcast, search_players
from baseball_analyzer_v2.evaluation import evaluate_player
from baseball_analyzer_v2.projections import build_projection
from baseball_analyzer_v2.sources import SourceUnavailable, game_context, matchup_inputs, schedule
from baseball_analyzer_v2.storage import CACHE_ROOT, LOCK, atomic_json

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


def history(request: AnalysisRequest, cutoff: date):
    end = min(cutoff - timedelta(days=1), datetime.now(timezone.utc).date() - timedelta(days=1))
    start = max(date(2015, 1, 1), cutoff - timedelta(days=request.lookback_days))
    return fetch_batter_statcast(request.player_id, start.isoformat(), end.isoformat()), start, end


@app.post("/api/analyze")
def analyze(request: AnalysisRequest):
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
            inputs = matchup_inputs(ctx, request.player_id)
            auto = inputs.get("starter")
            if auto and opponent and auto["id"] != opponent["id"]:
                raise HTTPException(422, "El pitcher seleccionado no coincide con el probable de este partido. Actualiza el partido o analiza sin partido.")
            opponent = auto or opponent
        stats, start, end = history(request, cutoff)
        pitcher = None
        if opponent:
            try:
                pitcher = fetch_pitcher_statcast(opponent["id"], start.isoformat(), end.isoformat())
            except Exception:
                LOGGER.exception("Pitcher history unavailable")
        result = build_projection(stats, {"id": request.player_id, "name": request.player_name.strip()}, request.games,
                                  request.lookback_days, cutoff, pitcher, opponent, ctx, inputs)
        # Freeze forecasts before a result exists, allowing future prospective evaluation.
        try:
            with LOCK:
                atomic_json(CACHE_ROOT / "forecasts" / (uuid4().hex + ".json"), result)
        except OSError:
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
