"""測試欄位正規化、異常過濾、多縣市合併。"""
from __future__ import annotations

import pandas as pd

from data.preprocessor import (
    STANDARD_COLUMNS,
    normalize_stations,
    filter_invalid_stations,
    merge_cities,
)


TAIPEI_SAMPLE = [
    {
        "sno": "500101001",
        "sna": "YouBike2.0_捷運科技大樓站",
        "latitude": 25.02605,
        "longitude": 121.5436,
        "Quantity": 28,
        "available_rent_bikes": 24,
        "available_return_bikes": 4,
        "srcUpdateTime": "2026-06-07 14:32:52",
        "act": "1",
    },
    {
        "sno": "500101002",
        "sna": "停用站",
        "latitude": 25.04,
        "longitude": 121.55,
        "Quantity": 20,
        "available_rent_bikes": 0,
        "available_return_bikes": 20,
        "srcUpdateTime": "2026-06-07 14:32:52",
        "act": "0",
    },
    {
        "sno": "500101003",
        "sna": "壞座標站",
        "latitude": 0,
        "longitude": 0,
        "Quantity": 10,
        "available_rent_bikes": 5,
        "available_return_bikes": 5,
        "srcUpdateTime": "2026-06-07 14:32:52",
        "act": "1",
    },
]


# 假設新北市使用舊 schema（不同欄位名）
NTPC_SAMPLE = [
    {
        "sno": "1001",
        "sna": "板橋車站",
        "lat": 25.0136,
        "lng": 121.4637,
        "tot": 50,
        "sbi": 30,
        "bemp": 20,
        "mday": "2026-06-07 14:30:00",
    },
]


def test_normalize_taipei_schema():
    df = normalize_stations(TAIPEI_SAMPLE, "台北市")
    assert list(df.columns) == STANDARD_COLUMNS
    assert len(df) == 3
    first = df.iloc[0]
    assert first["station_id"] == "500101001"
    assert first["name"] == "YouBike2.0_捷運科技大樓站"
    assert first["lat"] == 25.02605
    assert first["lon"] == 121.5436
    assert first["total"] == 28
    assert first["available_bikes"] == 24
    assert first["available_docks"] == 4
    assert first["city"] == "台北市"
    assert first["active"] is True or first["active"] == True  # noqa


def test_normalize_ntpc_legacy_schema():
    df = normalize_stations(NTPC_SAMPLE, "新北市")
    row = df.iloc[0]
    assert row["station_id"] == "1001"
    assert row["lat"] == 25.0136
    assert row["lon"] == 121.4637
    assert row["total"] == 50
    assert row["available_bikes"] == 30
    assert row["available_docks"] == 20


def test_filter_drops_inactive_and_bad_coords():
    df = normalize_stations(TAIPEI_SAMPLE, "台北市")
    clean = filter_invalid_stations(df)
    assert len(clean) == 1
    assert clean.iloc[0]["station_id"] == "500101001"


def test_merge_cities_prefixes_station_id():
    a = normalize_stations(TAIPEI_SAMPLE[:1], "台北市")
    b = normalize_stations([{**TAIPEI_SAMPLE[0], "sno": "500101001"}], "新北市")
    merged = merge_cities([a, b])
    assert len(merged) == 2
    ids = set(merged["station_id"])
    assert "台北市_500101001" in ids
    assert "新北市_500101001" in ids


def test_merge_empty_returns_empty_df():
    df = merge_cities([])
    assert list(df.columns) == STANDARD_COLUMNS
    assert len(df) == 0


# 新北 API 目前格式：sbi_quantity = 一般車 yb2_quantity + 電輔車 eyb_quantity（皆為字串）
NTPC_EBIKE_SAMPLE = [
    {
        "sno": "500201002", "sna": "YouBike2.0_八里行政中心",
        "tot_quantity": "20", "sbi_quantity": "16", "bemp": "4",
        "lat": "25.15397", "lng": "121.40721", "act": "1",
        "mday": "20261113T180402",
        "yb2_quantity": "15", "eyb_quantity": "1",
    },
    {
        "sno": "500201003", "sna": "YouBike2.0_只剩電輔車",
        "tot_quantity": "20", "sbi_quantity": "2", "bemp": "18",
        "lat": "25.15", "lng": "121.40", "act": "1",
        "yb2_quantity": "0", "eyb_quantity": "2",
    },
]


def test_ntpc_ebikes_not_counted_as_free_bikes():
    df = normalize_stations(NTPC_EBIKE_SAMPLE, "新北市")
    assert df.iloc[0]["available_bikes"] == 15
    assert df.iloc[0]["available_ebikes"] == 1
    # 只剩電輔車的站：一般車 0 台，不能當作有免費車可借
    assert df.iloc[1]["available_bikes"] == 0
    assert df.iloc[1]["available_ebikes"] == 2


def test_official_detail_format_splits_ebikes():
    raw = [{**TAIPEI_SAMPLE[0], "available_rent_bikes": 10,
            "available_spaces_detail": {"yb2": 7, "eyb": 3}}]
    df = normalize_stations(raw, "台北市")
    assert df.iloc[0]["available_bikes"] == 7
    assert df.iloc[0]["available_ebikes"] == 3


def test_only_ebike_count_is_subtracted_from_total():
    raw = [{**TAIPEI_SAMPLE[0], "available_rent_bikes": 10, "eyb_quantity": 4}]
    df = normalize_stations(raw, "台北市")
    assert df.iloc[0]["available_bikes"] == 6


def test_taipei_without_split_keeps_total_and_unknown_ebikes():
    df = normalize_stations(TAIPEI_SAMPLE, "台北市")
    assert df.iloc[0]["available_bikes"] == 24
    assert pd.isna(df.iloc[0]["available_ebikes"])
