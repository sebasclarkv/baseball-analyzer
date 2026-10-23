"""Public baseball data access through pybaseball and Baseball Savant."""

from __future__ import annotations

import os
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

APP_ROOT = Path(__file__).resolve().parent.parent
CACHE_ROOT = APP_ROOT / ".pybaseball" / "cache"
LOCAL_CACHE = APP_ROOT / ".cache"

os.environ.setdefault("PYBASEBALL_CACHE", str(CACHE_ROOT))
os.environ.setdefault("XDG_CACHE_HOME", str(LOCAL_CACHE))
os.environ.setdefault("MPLCONFIGDIR", str(LOCAL_CACHE / "matplotlib"))
CACHE_ROOT.mkdir(parents=True, exist_ok=True)
(LOCAL_CACHE / "matplotlib").mkdir(parents=True, exist_ok=True)

import pybaseball as pyb
from pybaseball.playerid_lookup import chadwick_register

pyb.cache.enable()


def _normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(character for character in text if not unicodedata.combining(character))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.casefold()).split())


@lru_cache(maxsize=1)
def _register():
    return chadwick_register(save=True)


@lru_cache(maxsize=64)
def _search_players_normalized(query: str, limit: int) -> tuple[dict[str, Any], ...]:
    if not query:
        return ()

    table = _register()
    records = table.copy()
    for column in ("name_first", "name_last"):
        if column not in records:
            records[column] = ""
        records[column] = records[column].fillna("").astype(str)

    records["_display_name"] = (
        records["name_first"].str.strip() + " " + records["name_last"].str.strip()
    ).str.strip()
    records["_normalized_name"] = records["_display_name"].map(_normalize)
    records["_normalized_first"] = records["name_first"].map(_normalize)
    records["_normalized_last"] = records["name_last"].map(_normalize)
    records["_mlbam"] = records["key_mlbam"].fillna(-1).astype(int)
    records = records[records["_mlbam"] > 0]

    tokens = query.split()
    if len(tokens) == 1:
        mask = (
            records["_normalized_last"].str.contains(query, regex=False)
            | records["_normalized_first"].str.contains(query, regex=False)
        )
    else:
        mask = records["_normalized_name"].str.contains(query, regex=False)
    matches = records.loc[mask].copy()
    matches["_rank"] = matches["_normalized_name"].map(
        lambda name: 0 if name == query else 1 if name.startswith(query) else 2
    )
    if "mlb_played_last" in matches:
        matches["_last_season"] = (
            matches["mlb_played_last"].fillna(0).astype(int)
        )
        matches = matches.sort_values(
            ["_rank", "_last_season", "_display_name"],
            ascending=[True, False, True],
        )
    else:
        matches = matches.sort_values(["_rank", "_display_name"])

    results = []
    for _, row in matches.head(limit).iterrows():
        first_season = row.get("mlb_played_first")
        last_season = row.get("mlb_played_last")
        years = None
        if pd.notna(first_season) and pd.notna(last_season):
            years = f"{int(first_season)}–{int(last_season)}"
        results.append(
            {
                "id": int(row["_mlbam"]),
                "name": str(row["_display_name"]),
                "years": years,
            }
        )
    return tuple(results)


def search_players(query: str, limit: int = 8) -> list[dict[str, Any]]:
    normalized = _normalize(query)
    if len(normalized) < 2:
        return []
    return list(_search_players_normalized(normalized, max(1, min(limit, 12))))


def fetch_batter_statcast(
    player_id: int, start_date: str, end_date: str
):
    """Fetch pitch-level Statcast data for one batter and date range."""
    return pyb.statcast_batter(start_date, end_date, int(player_id))


def fetch_pitcher_statcast(
    player_id: int, start_date: str, end_date: str
):
    """Fetch pitch-level Statcast data for one opposing pitcher and date range."""
    return pyb.statcast_pitcher(start_date, end_date, int(player_id))
