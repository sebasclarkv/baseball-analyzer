"""The diagnostic must never confuse HTTP success, cached data and usable data."""
from datetime import date

import httpx
import pandas as pd
import pytest
import requests

from baseball_analyzer_v2 import data
from scripts.check_connections import LiveClient, ProbeError, check_statcast, pybaseball_cache


def client_with_transport(handler):
    client = LiveClient()
    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def test_html_success_is_not_valid_json():
    client = client_with_transport(lambda request: httpx.Response(200, text="<html>Access page</html>"))
    try:
        with pytest.raises(ProbeError) as result:
            client.public_json("https://statsapi.mlb.com/example")
        assert result.value.category == "invalid_response"
        assert len(client.requests) == 1
    finally:
        client.close()


def test_proxy_denial_is_not_retried():
    def blocked(request):
        raise httpx.ProxyError("403 Forbidden")
    client = client_with_transport(blocked)
    try:
        with pytest.raises(ProbeError) as result:
            client.get("https://statsapi.mlb.com/example")
        assert result.value.category == "proxy_denied"
        assert len(client.requests) == 1
    finally:
        client.close()


def test_statcast_without_request_cannot_pass(monkeypatch, history_data):
    monkeypatch.setattr(data, "fetch_batter_statcast", lambda *args: history_data)
    client = LiveClient()
    try:
        with pytest.raises(ProbeError) as result:
            check_statcast(client, date(2025, 7, 1))
        assert result.value.category == "cached_response"
    finally:
        client.close()


def test_statcast_exercises_actual_sdk_and_parquet(monkeypatch, history_data):
    frame = history_data.loc[history_data.game_pk < 1007].copy()
    frame["batter"] = 592450
    frame["game_date"] = (pd.to_datetime(frame.game_date) + pd.Timedelta(days=91)).dt.strftime("%Y-%m-%d")
    requested = []
    previous_cache_state = pybaseball_cache.enabled
    def get(url, **kwargs):
        assert pybaseball_cache.enabled is False
        requested.append((url, kwargs))
        response = requests.Response()
        response.status_code = 200
        response.encoding = "utf-8"
        response.headers["content-type"] = "text/csv"
        response._content = frame.to_csv(index=False).encode()
        return response
    monkeypatch.setattr(requests, "get", get)
    client = LiveClient()
    try:
        report = check_statcast(client, date(2025, 7, 1))
        assert report["projection_pipeline"] == "passed"
        assert report["cache_used"] is False
        assert report["games"] == 7
        assert len(requested) == len(client.requests) == 1
        assert "batters_lookup%5B%5D=592450" in requested[0][0]
        assert requested[0][1]["timeout"] == client.timeout
        assert pybaseball_cache.enabled == previous_cache_state
    finally:
        client.close()
