"""擴大縣市支援：官方全台資料篩選、跨區調度費、區域與免費規則。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from config import (
    CITY_CONFIG,
    REGIONS,
    free_minutes_for,
    return_fee_ntd,
    supported_cities,
)
from core.graph_builder import build_station_graph
from core.route_optimizer import cross_fee_message
from data.fetcher import FetchError, fetch_city_stations
from data.preprocessor import filter_invalid_stations, normalize_stations


def _yb(station_no, area_code, lat=24.8, lng=120.97, status=1):
    """仿 apis.youbike.com.tw station-yb2.json 的單筆紀錄。"""
    return {
        "country_code": "00", "area_code": area_code, "type": 2, "status": status,
        "station_no": station_no, "name_tw": f"站{station_no}", "district_tw": "東區",
        "parking_spaces": 20, "available_spaces": 7,
        "available_spaces_detail": {"yb1": 0, "yb2": 5, "eyb": 2},
        "empty_spaces": 13, "forbidden_spaces": 0,
        "lat": str(lat), "lng": str(lng), "updated_at": "2026-11-13 18:09:32",
    }


NATIONAL = [
    _yb("500101001", "00", 25.02, 121.54),   # 台北
    _yb("500601001", "09"),                  # 新竹市
    _yb("500601002", "09"),
    _yb("500801001", "01", 24.14, 120.68),   # 台中
    _yb("501201001", "12", 22.63, 120.30),   # 高雄
]


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload
    def raise_for_status(self): pass
    def json(self): return self._payload


def test_every_city_in_a_region_has_config():
    for region, cities in REGIONS.items():
        for c in cities:
            assert c in CITY_CONFIG, f"{region} 的 {c} 缺少設定"


def test_supported_cities_by_region():
    assert supported_cities("北北桃") == ["台北市", "新北市", "桃園市"]
    assert "新竹市" in supported_cities("竹竹苗")
    assert supported_cities("台中") == ["台中市"]
    assert supported_cities("不存在") == []


def test_national_feed_filtered_by_area_code(tmp_path: Path):
    with patch("data.fetcher.requests.get", return_value=_FakeResp(NATIONAL)) as m:
        hc = fetch_city_stations("新竹市", cache_dir=tmp_path)
        tc = fetch_city_stations("台中市", cache_dir=tmp_path)
    assert [r["station_no"] for r in hc] == ["500601001", "500601002"]
    assert [r["station_no"] for r in tc] == ["500801001"]
    # 全台資料只下載一次，第二個縣市直接用快取
    assert m.call_count == 1


def test_national_feed_city_without_stations_raises(tmp_path: Path):
    with patch("data.fetcher.requests.get", return_value=_FakeResp(NATIONAL)):
        with pytest.raises(FetchError, match="查無站點"):
            fetch_city_stations("台東縣", cache_dir=tmp_path)


def test_normalize_national_fields():
    raw = [_yb("500601001", "09"), _yb("500601002", "09", status=2)]
    df = normalize_stations(raw, "新竹市")
    row = df.iloc[0]
    assert row["station_id"] == "500601001"
    assert row["name"] == "站500601001"
    assert row["lat"] == pytest.approx(24.8)
    assert row["total"] == 20
    # 7 = 總可借；若已套用電輔車拆分（只算一般車 yb2）則為 5
    assert row["available_bikes"] in (5, 7)
    assert row["available_docks"] == 13
    # status=2（暫停營運）應被過濾
    assert len(filter_invalid_stations(df)) == 1


def test_return_fee_matrix():
    assert return_fee_ntd("台北市", "新北市") == 0
    assert return_fee_ntd("台北市", "桃園市") == 0
    assert return_fee_ntd("桃園市", "新竹市") == 0
    assert return_fee_ntd("桃園市", "苗栗縣") == 600
    assert return_fee_ntd("台北市", "台中市") == 815
    assert return_fee_ntd("台南市", "高雄市") == 0
    assert return_fee_ntd("台中市", "高雄市") == 660
    assert return_fee_ntd("台北市", "火星市") > 0  # 未知縣市保守警告


def test_cross_fee_message():
    assert cross_fee_message([("台北市", "新北市")]) == ""
    msg = cross_fee_message([("台北市", "新北市"), ("桃園市", "苗栗縣")])
    assert "調度費" in msg and "600" in msg


def test_kaohsiung_tainan_free_only_with_tpass():
    for city in ("高雄市", "台南市"):
        assert free_minutes_for(city, has_tpass=False) == 0
        assert free_minutes_for(city, has_tpass=True) == 30


def _df(rows):
    df = pd.DataFrame(rows)
    df["available_bikes"] = df["available_bikes"].astype("Int64")
    df["available_docks"] = df["available_docks"].astype("Int64")
    return df


def test_paid_return_pair_blocked_free_pair_allowed():
    # 三站相距數百公尺：桃園、新竹市、苗栗（虛構座標，只測規則）
    st = _df([
        {"station_id": "TY", "name": "TY", "lat": 24.9, "lon": 121.0,
         "available_bikes": 5, "available_docks": 5, "city": "桃園市"},
        {"station_id": "HC", "name": "HC", "lat": 24.9, "lon": 121.003,
         "available_bikes": 5, "available_docks": 5, "city": "新竹市"},
        {"station_id": "ML", "name": "ML", "lat": 24.9, "lon": 121.006,
         "available_bikes": 5, "available_docks": 5, "city": "苗栗縣"},
    ])
    g = build_station_graph(st, 30, 3, 12.0, 1.3)
    assert g.has_edge("TY", "HC") and g.has_edge("HC", "TY")   # 桃園↔新竹 免調度費
    assert g.has_edge("HC", "ML") and g.has_edge("ML", "HC")   # 新竹↔苗栗 免調度費
    assert not g.has_edge("TY", "ML")                          # 桃園→苗栗 600 元
    assert not g.has_edge("ML", "TY")


def test_city_without_free_minutes_has_no_ride_out():
    st = _df([
        {"station_id": "K1", "name": "K1", "lat": 22.63, "lon": 120.30,
         "available_bikes": 5, "available_docks": 5, "city": "高雄市"},
        {"station_id": "K2", "name": "K2", "lat": 22.632, "lon": 120.30,
         "available_bikes": 5, "available_docks": 5, "city": "高雄市"},
    ])
    fm = {"高雄市": free_minutes_for("高雄市", has_tpass=False)}
    g = build_station_graph(st, 30, 3, 12.0, 1.3, free_minutes_by_city=fm)
    assert g.number_of_edges() == 0
    fm = {"高雄市": free_minutes_for("高雄市", has_tpass=True)}
    g = build_station_graph(st, 30, 3, 12.0, 1.3, free_minutes_by_city=fm)
    assert g.has_edge("K1", "K2")
