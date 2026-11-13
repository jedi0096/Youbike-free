"""測試 API 擷取與快取機制。"""
from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from data.fetcher import (
    FetchError,
    fetch_city_stations,
    _save_cache,
    _load_cache,
)


SAMPLE_STATIONS = [
    {"sno": "500101001", "sna": "捷運市政府站", "lat": 25.04, "lng": 121.56,
     "tot": 60, "sbi": 30, "bemp": 30},
    {"sno": "500101002", "sna": "市政府轉運站", "lat": 25.04, "lng": 121.57,
     "tot": 40, "sbi": 20, "bemp": 20},
]


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload
    def raise_for_status(self): pass
    def json(self): return self._payload


@pytest.fixture
def tmp_cache(tmp_path: Path) -> Path:
    return tmp_path / "cache"


def test_unknown_city_raises(tmp_cache: Path):
    with pytest.raises(ValueError):
        fetch_city_stations("火星市", cache_dir=tmp_cache)


def test_city_without_api_url_raises(tmp_cache: Path):
    # api_url 仍為 TODO（未設定）的縣市應擲 FetchError
    fake = {"測試縣": {"api_url": "TODO_NEED_TO_FIND", "free_minutes": 30}}
    with patch.dict("data.fetcher.CITY_CONFIG", fake):
        with pytest.raises(FetchError):
            fetch_city_stations("測試縣", use_cache=False, cache_dir=tmp_cache)


def test_fetch_writes_cache(tmp_cache: Path):
    with patch("data.fetcher.requests.get", return_value=_FakeResp(SAMPLE_STATIONS)):
        data = fetch_city_stations("台北市", use_cache=False, cache_dir=tmp_cache)
    assert data == SAMPLE_STATIONS
    assert (tmp_cache / "台北市.json").exists()


def test_cache_hit_within_ttl(tmp_cache: Path):
    _save_cache("台北市", SAMPLE_STATIONS, tmp_cache)
    with patch("data.fetcher.requests.get") as mock_get:
        data = fetch_city_stations("台北市", cache_ttl=300, cache_dir=tmp_cache)
        mock_get.assert_not_called()
    assert data == SAMPLE_STATIONS


def test_cache_expired_refetches(tmp_cache: Path):
    _save_cache("台北市", SAMPLE_STATIONS, tmp_cache)
    cache_file = tmp_cache / "台北市.json"
    old = time.time() - 3600
    os.utime(cache_file, (old, old))

    fresh = [{"sno": "X"}]
    with patch("data.fetcher.requests.get", return_value=_FakeResp(fresh)) as mock_get:
        data = fetch_city_stations("台北市", cache_ttl=300, cache_dir=tmp_cache)
        mock_get.assert_called_once()
    assert data == fresh


def test_corrupt_cache_falls_through(tmp_cache: Path):
    tmp_cache.mkdir(parents=True, exist_ok=True)
    (tmp_cache / "台北市.json").write_text("not json", encoding="utf-8")
    assert _load_cache("台北市", 300, tmp_cache) is None


def test_non_list_response_raises(tmp_cache: Path):
    with patch("data.fetcher.requests.get", return_value=_FakeResp({"oops": True})):
        with pytest.raises(FetchError):
            fetch_city_stations("台北市", use_cache=False, cache_dir=tmp_cache)


def test_connect_timeout_retries_and_friendly_error(tmp_cache: Path):
    import requests
    from data.fetcher import MAX_ATTEMPTS

    with patch("data.fetcher.requests.get",
               side_effect=requests.ConnectTimeout("timed out")) as mock_get:
        with pytest.raises(FetchError, match="連線逾時，已略過該市"):
            fetch_city_stations("台北市", use_cache=False, cache_dir=tmp_cache)
    # 暫時性逾時應重試到 MAX_ATTEMPTS 次
    assert mock_get.call_count == MAX_ATTEMPTS


def test_http_error_not_retried(tmp_cache: Path):
    import requests

    resp = _FakeResp({"x": 1})
    def _raise():
        raise requests.HTTPError("500 Server Error")
    resp.raise_for_status = _raise
    with patch("data.fetcher.requests.get", return_value=resp) as mock_get:
        with pytest.raises(FetchError, match="擷取失敗"):
            fetch_city_stations("台北市", use_cache=False, cache_dir=tmp_cache)
    # 非暫時性錯誤（HTTP 4xx/5xx）不重試
    assert mock_get.call_count == 1


def test_data_path_extracts_nested_list(tmp_cache: Path):
    # 模擬 CKAN 風格巢狀回傳 {"result": {"records": [...]}}
    nested = {"result": {"records": SAMPLE_STATIONS}}
    cfg = {
        "api_url": "https://example.com/api",
        "data_path": ["result", "records"],
    }
    with patch.dict("data.fetcher.CITY_CONFIG", {"測試市": cfg}), \
            patch("data.fetcher.requests.get", return_value=_FakeResp(nested)):
        data = fetch_city_stations("測試市", use_cache=False, cache_dir=tmp_cache)
    assert data == SAMPLE_STATIONS


def test_data_path_missing_key_raises(tmp_cache: Path):
    cfg = {
        "api_url": "https://example.com/api",
        "data_path": ["result", "records"],
    }
    with patch.dict("data.fetcher.CITY_CONFIG", {"測試市": cfg}), \
            patch("data.fetcher.requests.get", return_value=_FakeResp({"result": {}})):
        with pytest.raises(FetchError, match="缺少預期欄位"):
            fetch_city_stations("測試市", use_cache=False, cache_dir=tmp_cache)
