"""測試兩 case 免費騎乘時長（依縣市 + 是否持 TPASS）與逐段建圖上限。"""
from __future__ import annotations

import pandas as pd

from config import active_cities, free_minutes_by_city, free_minutes_for
from core.graph_builder import build_station_graph


def test_free_minutes_for_taipei_constant():
    assert free_minutes_for("台北市", has_tpass=False) == 30
    assert free_minutes_for("台北市", has_tpass=True) == 30


def test_free_minutes_for_taoyuan_is_60_for_all_members():
    # 桃園 2023/4/1 起會員前 60 分鐘免費，不需 TPASS
    assert free_minutes_for("桃園市", has_tpass=False) == 60
    assert free_minutes_for("桃園市", has_tpass=True) == 60


def test_free_minutes_by_city_map():
    fmbc = free_minutes_by_city(["台北市", "桃園市"], has_tpass=True)
    assert fmbc == {"台北市": 30, "桃園市": 60}


def test_active_cities_are_north_circle_only():
    cities = active_cities()
    assert set(cities) <= {"台北市", "新北市", "桃園市"}
    assert "高雄市" not in cities


def _make_df(rows):
    df = pd.DataFrame(rows)
    df["available_bikes"] = df["available_bikes"].astype("Int64")
    df["available_docks"] = df["available_docks"].astype("Int64")
    return df


def test_per_city_free_minutes_edge_limit():
    # 兩對相距 ~5km 的站：桃園對（上限 60 分→可連）、台北對（上限 30 分→不可連）。
    # 5km × 1.3 / 12 * 60 ≈ 32.5 分鐘 → 超過台北 27、未超過桃園 57。
    deg = 5 / 111.0  # ~5km 緯度差
    stations = _make_df([
        {"station_id": "TY1", "name": "桃A", "lat": 25.0, "lon": 121.30,
         "available_bikes": 5, "available_docks": 5, "city": "桃園市"},
        {"station_id": "TY2", "name": "桃B", "lat": 25.0 + deg, "lon": 121.30,
         "available_bikes": 5, "available_docks": 5, "city": "桃園市"},
        {"station_id": "TP1", "name": "北A", "lat": 25.0, "lon": 121.50,
         "available_bikes": 5, "available_docks": 5, "city": "台北市"},
        {"station_id": "TP2", "name": "北B", "lat": 25.0 + deg, "lon": 121.50,
         "available_bikes": 5, "available_docks": 5, "city": "台北市"},
    ])
    fmbc = free_minutes_by_city(["桃園市", "台北市"], has_tpass=True)
    g = build_station_graph(
        stations, free_minutes=60, safety_margin=3,
        speed_kmh=12.0, detour_factor=1.3,
        free_minutes_by_city=fmbc,
    )
    # 桃園 60 分上限 → 連邊
    assert g.has_edge("TY1", "TY2")
    # 台北 30 分上限（27 分有效）→ 不連
    assert not g.has_edge("TP1", "TP2")
