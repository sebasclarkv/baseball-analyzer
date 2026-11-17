"""Local Parquet history and an expiring JSON cache; no external database."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

import duckdb
import pandas as pd

CACHE_ROOT = Path(os.environ.get("DIAMOND_DATA_DIR", Path(__file__).resolve().parent.parent / ".cache" / "diamond"))
LOCK = threading.RLock()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temp.replace(path)


def json_cache_path(key: str) -> Path:
    return CACHE_ROOT / "http" / (hashlib.sha256(key.encode()).hexdigest() + ".json")


def cached_history(role: str, player_id: int, start: str, end: str, loader: Callable) -> pd.DataFrame:
    """Extend a player's downloaded coverage; refresh recent data, retain offline provenance."""
    if role not in {"batter", "pitcher"} or int(player_id) <= 0:
        raise ValueError("Invalid history identity")
    start_day, end_day = date.fromisoformat(start), date.fromisoformat(end)
    if start_day > end_day:
        return pd.DataFrame()
    folder = CACHE_ROOT / "statcast"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{role}-{int(player_id)}.parquet"
    meta_path = path.with_suffix(".json")
    # Requests for the same player must not overwrite one another's coverage.
    with LOCK:
        meta = {}
        frame = pd.DataFrame()
        try:
            meta = json.loads(meta_path.read_text())
            with duckdb.connect() as db:
                frame = db.execute("SELECT * FROM read_parquet(?)", [str(path)]).df()
        except (OSError, ValueError, duckdb.Error):
            meta = {}
        spans = []
        if not meta:
            spans = [(start_day, end_day)]
        else:
            left, right = date.fromisoformat(meta["start"]), date.fromisoformat(meta["end"])
            if start_day < left:
                spans.append((start_day, left - timedelta(days=1)))
            if end_day > right:
                spans.append((right + timedelta(days=1), end_day))
            if end_day >= date.today() - timedelta(days=7) and time.time() - meta["fetched_at"] > 21600:
                spans.append((max(start_day, end_day - timedelta(days=7)), end_day))
        stale = False
        try:
            # Fetch everything before publishing a new coverage record.
            updates = [(lo, hi, loader(lo.isoformat(), hi.isoformat(), int(player_id))) for lo, hi in spans]
            for lo, hi, part in updates:
                if not isinstance(part, pd.DataFrame):
                    raise ValueError("Invalid Statcast response")
                if not frame.empty:
                    days = pd.to_datetime(frame["game_date"], errors="coerce").dt.date
                    frame = frame.loc[~days.between(lo, hi)]
                frame = pd.concat([frame, part], ignore_index=True)
            if spans:
                if "game_date" not in frame:
                    frame = pd.DataFrame(columns=["game_date", "events"])
                keys = [c for c in ("game_pk", "at_bat_number", "pitch_number") if c in frame]
                if len(keys) == 3:
                    frame = frame.drop_duplicates(keys, keep="last")
                temp = path.with_suffix(".tmp.parquet")
                with duckdb.connect() as db:
                    db.register("history", frame)
                    db.execute("COPY history TO ? (FORMAT PARQUET, COMPRESSION ZSTD)", [str(temp)])
                temp.replace(path)
                meta = {"start": min(start_day, date.fromisoformat(meta.get("start", start))).isoformat(),
                        "end": max(end_day, date.fromisoformat(meta.get("end", end))).isoformat(),
                        "fetched_at": time.time()}
                atomic_json(meta_path, meta)
        except Exception:
            # A partial cache cannot be silently presented as the requested history.
            if not meta or meta["start"] > start or meta["end"] < end:
                raise
            with duckdb.connect() as db:
                frame = db.execute("SELECT * FROM read_parquet(?)", [str(path)]).df()
            stale = True
        if not frame.empty:
            days = pd.to_datetime(frame["game_date"], errors="coerce").dt.date
            frame = frame.loc[days.between(start_day, end_day)].copy()
        frame.attrs["provenance"] = {"source": "Baseball Savant / Statcast", "stale": stale,
                                      "fetched_at": meta.get("fetched_at"), "storage": "Parquet"}
        return frame
