from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import json
from pathlib import Path
from threading import Barrier, Event

import duckdb
import pandas as pd
import pytest
from baseball_analyzer_v2 import data, storage


def test_name_order_accents_and_typo(monkeypatch):
    table = pd.DataFrame([{"name_first": "Aaron", "name_last": "Judge", "key_mlbam": 1, "mlb_played_first": 2016, "mlb_played_last": 2025},
                          {"name_first": "José", "name_last": "Ramírez", "key_mlbam": 2, "mlb_played_first": 2013, "mlb_played_last": 2025}])
    monkeypatch.setattr(data, "_register", lambda: table)
    data._prepared_register.cache_clear()
    data._search_players_normalized.cache_clear()
    for query, target in [("Judge Aaron", 1), ("aaron judeg", 1), ("Ramirez Jose", 2), ("Judge", 1), ("JUDGE", 1)]:
        assert data.search_players(query)[0]["id"] == target
    assert data.search_players("x") == []
    data._prepared_register.cache_clear()
    data._search_players_normalized.cache_clear()


def test_parquet_cache_extends_only_missing_span(tmp_path, monkeypatch, history_data):
    monkeypatch.setattr(storage, "CACHE_ROOT", tmp_path)
    calls = []
    def fetch(start, end, player):
        calls.append((start, end, player))
        return history_data.loc[history_data.game_date.between(start, end)].copy()
    first = storage.cached_history("batter", 99, "2025-04-01", "2025-04-15", fetch)
    repeated = storage.cached_history("batter", 99, "2025-04-01", "2025-04-15", fetch)
    assert len(calls) == 1 and len(first) == len(repeated)
    expanded = storage.cached_history("batter", 99, "2025-04-01", "2025-05-01", fetch)
    assert calls[-1] == ("2025-04-16", "2025-05-01", 99)
    assert len(expanded) > len(first)
    assert not expanded.duplicated(["game_pk", "at_bat_number", "pitch_number"]).any()


def test_incomplete_cache_is_not_reported_as_complete(tmp_path, monkeypatch, history_data):
    monkeypatch.setattr(storage, "CACHE_ROOT", tmp_path)
    storage.cached_history("batter", 99, "2025-04-01", "2025-04-15", lambda *a: history_data.head(1))
    def fail(*args):
        raise RuntimeError("offline")
    with pytest.raises(RuntimeError):
        storage.cached_history("batter", 99, "2025-04-01", "2025-05-01", fail)


def test_previous_parquet_format_can_be_read_and_extended(tmp_path, monkeypatch, history_data):
    monkeypatch.setattr(storage, "CACHE_ROOT", tmp_path)
    path = tmp_path / "statcast" / "batter-99.parquet"
    path.parent.mkdir()
    original = history_data.loc[history_data.game_date <= "2025-04-10"]
    with duckdb.connect() as db:
        db.register("original", original)
        db.execute("COPY original TO ? (FORMAT PARQUET)", [str(path)])
    storage.atomic_json(path.with_suffix(".json"), {
        "start": "2025-04-01", "end": "2025-04-10", "fetched_at": 1.0,
    })

    cached = storage.cached_history(
        "batter", 99, "2025-04-03", "2025-04-04",
        lambda *_: pytest.fail("a covered legacy window must not download again"),
    )
    assert set(cached.game_date.dt.strftime("%Y-%m-%d")) == {"2025-04-03", "2025-04-04"}

    def fetch(start, end, player):
        assert (start, end, player) == ("2025-04-11", "2025-04-12", 99)
        return history_data.loc[history_data.game_date.between(start, end)]

    expanded = storage.cached_history("batter", 99, "2025-04-01", "2025-04-12", fetch)
    assert expanded.game_date.nunique() == 12
    with duckdb.connect() as db:
        source_type = db.execute("SELECT typeof(game_date) FROM read_parquet(?) LIMIT 1", [str(path)]).fetchone()[0]
    assert source_type == "DATE"


def test_slow_download_does_not_block_other_player_or_duplicate_same_player(tmp_path, monkeypatch, history_data):
    monkeypatch.setattr(storage, "CACHE_ROOT", tmp_path)
    downloading = Event()
    unblock = Event()
    calls = []

    def fetch(start, end, player):
        calls.append((start, end, player))
        if (start, player) == ("2025-04-01", 99):
            downloading.set()
            assert unblock.wait(12)
        return history_data.loc[history_data.game_date.between(start, end)].assign(batter=player)

    with ThreadPoolExecutor(max_workers=3) as workers:
        first = workers.submit(storage.cached_history, "batter", 99, "2025-04-01", "2025-04-05", fetch)
        try:
            assert downloading.wait(5)
            overlapping = workers.submit(storage.cached_history, "batter", 99, "2025-04-03", "2025-04-08", fetch)
            other = workers.submit(storage.cached_history, "batter", 100, "2025-04-01", "2025-04-05", fetch)
            assert set(other.result(timeout=5).batter) == {100}
            assert not overlapping.done()
        finally:
            unblock.set()
        assert first.result(timeout=5).game_date.nunique() == 5
        assert overlapping.result(timeout=5).game_date.nunique() == 6
    assert [call for call in calls if call[2] == 99] == [
        ("2025-04-01", "2025-04-05", 99), ("2025-04-06", "2025-04-08", 99),
    ]


@pytest.mark.parametrize("columns", [["<html>Request denied</html>"], ["game_date", "events"]])
def test_bad_refresh_preserves_complete_cached_window(tmp_path, monkeypatch, history_data, columns):
    monkeypatch.setattr(storage, "CACHE_ROOT", tmp_path)
    day = date.today() - timedelta(days=1)
    start = (day - timedelta(days=3)).isoformat()
    end = day.isoformat()
    genuine = history_data.head(3).assign(game_date=end)
    storage.cached_history("batter", 99, start, end, lambda *_: genuine)
    path = tmp_path / "statcast" / "batter-99.json"
    metadata = json.loads(path.read_text())
    metadata["recent_fetched_at"] = 1.0
    storage.atomic_json(path, metadata)

    result = storage.cached_history("batter", 99, start, end, lambda *_: pd.DataFrame(columns=columns))
    assert list(result.events) == list(genuine.events)
    assert result.attrs["provenance"]["stale"] is True
    assert json.loads(path.read_text()) == metadata


def test_cache_keeps_pitches_without_full_identity(tmp_path, monkeypatch, history_data):
    monkeypatch.setattr(storage, "CACHE_ROOT", tmp_path)
    row = history_data.head(1)
    data = pd.concat([
        row, row.assign(events="single"), row.assign(game_pk=None),
        row.assign(game_pk=None, events="double"),
    ], ignore_index=True)
    saved = storage.cached_history("batter", 99, "2025-04-01", "2025-04-01", lambda *_: data)

    assert list(saved.loc[saved.game_pk.notna(), "events"]) == ["single"]
    assert len(saved.loc[saved.game_pk.isna()]) == 2


def test_atomic_json_can_be_written_concurrently(tmp_path, monkeypatch):
    original = Path.write_text
    barrier = Barrier(2)

    def wait_for_other_writer(path, *args, **kwargs):
        count = original(path, *args, **kwargs)
        barrier.wait(timeout=5)
        return count

    monkeypatch.setattr(Path, "write_text", wait_for_other_writer)
    path = tmp_path / "report.json"
    with ThreadPoolExecutor(max_workers=2) as workers:
        values = [{"profile": "José", "rows": [1]}, {"profile": "Ramírez", "rows": [2, 3]}]
        futures = [workers.submit(storage.atomic_json, path, value) for value in values]
        for future in futures:
            future.result(timeout=7)
    assert json.loads(path.read_text(encoding="utf-8")) in values
    assert list(tmp_path.iterdir()) == [path]
