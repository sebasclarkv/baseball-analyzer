"""FastAPI application for Baseball Analyzer 2.0."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from baseball_analyzer_v2 import __version__
from baseball_analyzer_v2.analytics import build_analysis, build_rolling_backtest
from baseball_analyzer_v2.data import (
    fetch_batter_statcast,
    fetch_pitcher_statcast,
    search_players,
)

LOGGER = logging.getLogger("baseball_analyzer")
APP_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = APP_ROOT / "static"

app = FastAPI(
    title="Baseball Analyzer",
    description="Proyecciones de bateo y matchup explicables con Statcast público.",
    version=__version__,
)
app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")


class AnalysisRequest(BaseModel):
    player_id: int = Field(gt=0)
    player_name: str = Field(min_length=2, max_length=100)
    opponent_pitcher_id: int | None = Field(default=None, gt=0)
    opponent_pitcher_name: str | None = Field(default=None, min_length=2, max_length=100)
    games: int = Field(default=40, ge=5, le=150)
    lookback_days: int = Field(default=730, ge=30, le=1095)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_ROOT / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__, "data_source": "Baseball Savant"}


@app.get("/api/players")
def players(
    query: str = Query(min_length=2, max_length=80),
) -> dict[str, list[dict[str, Any]]]:
    try:
        results = search_players(query)
    except Exception as exc:
        LOGGER.exception("Player search failed")
        raise HTTPException(
            status_code=502,
            detail="No se pudo cargar el registro público de jugadores.",
        ) from exc
    return {"players": results}


@app.post("/api/analyze")
def analyze(request: AnalysisRequest) -> dict[str, Any]:
    end_day = date.today()
    start_day = end_day - timedelta(days=request.lookback_days)
    try:
        stats = fetch_batter_statcast(
            request.player_id,
            start_day.isoformat(),
            end_day.isoformat(),
        )
        pitcher_stats = None
        opponent_pitcher = None
        if request.opponent_pitcher_id:
            opponent_pitcher = {
                "id": request.opponent_pitcher_id,
                "name": (request.opponent_pitcher_name or "Lanzador rival").strip(),
            }
            try:
                pitcher_stats = fetch_pitcher_statcast(
                    request.opponent_pitcher_id,
                    start_day.isoformat(),
                    end_day.isoformat(),
                )
            except Exception:
                LOGGER.exception(
                    "Opponent pitcher Statcast lookup failed for id %s",
                    request.opponent_pitcher_id,
                )
        player = {
            "id": request.player_id,
            "name": request.player_name.strip(),
        }
        return build_analysis(
            stats,
            player,
            games_requested=request.games,
            lookback_days=request.lookback_days,
            today=end_day,
            pitcher_stats=pitcher_stats,
            opponent_pitcher=opponent_pitcher,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        LOGGER.exception("Statcast analysis failed for player id %s", request.player_id)
        raise HTTPException(
            status_code=502,
            detail=(
                "No se pudo consultar Baseball Savant. Revisa la conexión "
                "a Statcast e inténtalo de nuevo."
            ),
        ) from exc


@app.post("/api/backtest")
def backtest(request: AnalysisRequest) -> dict[str, Any]:
    end_day = date.today()
    start_day = end_day - timedelta(days=request.lookback_days)
    try:
        stats = fetch_batter_statcast(
            request.player_id,
            start_day.isoformat(),
            end_day.isoformat(),
        )
        player = {
            "id": request.player_id,
            "name": request.player_name.strip(),
        }
        return build_rolling_backtest(
            stats,
            player,
            games_requested=request.games,
            lookback_days=request.lookback_days,
            today=end_day,
        )
    except Exception as exc:
        LOGGER.exception("Rolling backtest failed for player id %s", request.player_id)
        raise HTTPException(
            status_code=502,
            detail=(
                "No se pudo calcular la validación histórica con Baseball Savant. "
                "Revisa la conexión a Statcast e inténtalo de nuevo."
            ),
        ) from exc
