"""Local Parquet history and an expiring JSON cache; no external database."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Callable
from uuid import uuid4

import duckdb
import pandas as pd
from filelock import FileLock

CACHE_ROOT = Path(os.environ.get("DIAMOND_DATA_DIR", Path(__file__).resolve().parent.parent / ".cache" / "diamond"))
LOCK = threading.RLock()
RECENT_WINDOW = timedelta(days=7)
REFRESH_AFTER_SECONDS = 21600


@dataclass(frozen=True)
class Coverage:
    start: date
    end: date
    fetched_at: float
    recent_fetched_at: float
    native_date: bool


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def json_cache_path(key: str) -> Path:
    return CACHE_ROOT / "http" / (hashlib.sha256(key.encode()).hexdigest() + ".json")


def _coverage(meta_path: Path) -> Coverage | None:
    try:
        data = json.loads(meta_path.read_text())
        start, end = date.fromisoformat(data["start"]), date.fromisoformat(data["end"])
        fetched_at = float(data["fetched_at"])
        if start <= end:
            return Coverage(start, end, fetched_at, float(data.get("recent_fetched_at", fetched_at)),
                            data.get("format") == 2)
    except (OSError, KeyError, TypeError, ValueError):
        pass
    return None


def _fetch_spans(coverage: Coverage | None, start: date, end: date) -> list[tuple[date, date]]:
    if coverage is None:
        return [(start, end)]
    spans = []
    one_day = timedelta(days=1)
    if start < coverage.start:
        spans.append((start, coverage.start - one_day))
    if end > coverage.end:
        spans.append((coverage.end + one_day, end))
    if end >= date.today() - RECENT_WINDOW and time.time() - coverage.recent_fetched_at > REFRESH_AFTER_SECONDS:
        spans.append((max(start, end - RECENT_WINDOW), end))
    merged = []
    for lo, hi in sorted(spans):
        if merged and lo <= merged[-1][1] + one_day:
            lo, previous_hi = merged.pop()
            hi = max(previous_hi, hi)
        merged.append((lo, hi))
    return merged


def _read_history(
    path: Path, coverage: Coverage, window: tuple[date, date] | None = None
) -> pd.DataFrame:
    # Legacy files stored ISO dates as strings. Newly written files use DATE so
    # DuckDB can skip sorted row groups outside the query window.
    day = "game_date" if coverage.native_date else "TRY_CAST(game_date AS DATE)"
    with duckdb.connect() as db:
        query = f"SELECT * REPLACE ({day} AS game_date) FROM read_parquet(?)"
        if window is None:
            return db.execute(query, [str(path)]).df()
        query += f" WHERE {day} BETWEEN ? AND ?"
        return db.execute(query, [str(path), *window]).df()


def _validated_history(part: pd.DataFrame, role: str, player_id: int, lo: date, hi: date) -> pd.DataFrame:
    if not isinstance(part, pd.DataFrame) or not {"game_date", "events"}.issubset(part.columns):
        raise RuntimeError("Statcast did not return pitch data")
    part = part.copy()
    days = pd.to_datetime(part["game_date"], errors="coerce", utc=True).dt.date
    if not part.empty and not days.between(lo, hi).all():
        raise RuntimeError("Statcast returned dates outside the requested range")
    if role in part and not pd.to_numeric(part[role], errors="coerce").eq(player_id).all():
        raise RuntimeError("Statcast returned a different player")
    part["game_date"] = days
    return part


def _write_history(path: Path, history: pd.DataFrame) -> None:
    temp = path.with_suffix(".tmp.parquet")
    try:
        with duckdb.connect() as db:
            db.register("history", history)
            db.execute(
                """COPY (
                    SELECT * REPLACE (CAST(game_date AS DATE) AS game_date)
                    FROM history ORDER BY game_date
                ) TO ? (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 8192)""",
                [str(temp)],
            )
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def cached_history(role: str, player_id: int, start: str, end: str, loader: Callable) -> pd.DataFrame:
    """Extend a player's downloaded coverage; refresh recent data, retain offline provenance."""
    if role not in {"batter", "pitcher"} or int(player_id) <= 0:
        raise ValueError("Invalid history identity")
    player_id = int(player_id)
    start_day, end_day = date.fromisoformat(start), date.fromisoformat(end)
    if start_day > end_day:
        return pd.DataFrame()
    folder = CACHE_ROOT / "statcast"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{role}-{player_id}.parquet"
    meta_path = path.with_suffix(".json")
    # Serialize writers for this history across server threads and CLI processes.
    # Downloads for another player or role, and the JSON cache, can proceed.
    with FileLock(path.with_suffix(".lock")):
        coverage = _coverage(meta_path)
        window = (start_day, end_day)
        spans = _fetch_spans(coverage, *window)
        frame = pd.DataFrame()
        if coverage is not None:
            try:
                frame = _read_history(path, coverage, None if spans else window)
            except (OSError, duckdb.Error):
                coverage = None
                spans = [window]

        stale = False
        if spans:
            try:
                for lo, hi in spans:
                    part = _validated_history(loader(lo.isoformat(), hi.isoformat(), player_id),
                                              role, player_id, lo, hi)
                    if not frame.empty:
                        days = pd.to_datetime(frame["game_date"], errors="coerce", utc=True).dt.date
                        replaced = days.between(lo, hi)
                        if part.empty and replaced.any():
                            raise RuntimeError("Statcast returned no rows for a previously populated window")
                        frame = frame.loc[~replaced]
                    frame = part if frame.empty else pd.concat([frame, part], ignore_index=True)
                keys = [c for c in ("game_pk", "at_bat_number", "pitch_number") if c in frame]
                if len(keys) == 3:
                    identified = frame[keys].notna().all(axis=1)
                    frame = frame.loc[~identified | ~frame.duplicated(keys, keep="last")]
                left = min(start_day, coverage.start) if coverage else start_day
                right = max(end_day, coverage.end) if coverage else end_day
                fetched_at = time.time()
                recent = (coverage.recent_fetched_at if coverage and all(hi < right for _, hi in spans)
                          else fetched_at)
                _write_history(path, frame)
                atomic_json(meta_path, {
                    "start": left.isoformat(), "end": right.isoformat(), "fetched_at": fetched_at,
                    "recent_fetched_at": recent, "format": 2,
                })
                coverage = Coverage(left, right, fetched_at, recent, native_date=True)
            except Exception:
                # Only an already complete window can fall back to saved data.
                if not coverage or coverage.start > start_day or coverage.end < end_day:
                    raise
                stale = True
            frame = _read_history(path, coverage, window)
        frame.attrs["provenance"] = {
            "source": "Baseball Savant / Statcast", "stale": stale,
            "fetched_at": coverage.fetched_at, "storage": "Parquet",
        }
        return frame
