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
