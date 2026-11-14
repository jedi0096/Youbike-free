"""測試路徑演算法（含邊界情況）。"""
from __future__ import annotations

import pandas as pd

import pytest

from core.graph_builder import build_station_graph
from core.route_optimizer import (
    find_nearby_rent_alternatives,
    find_nearest_station,
    plan_route,
)
from core.time_estimator import haversine_km


def _make_stations(rows):
    df = pd.DataFrame(rows)
    df["available_bikes"] = df["available_bikes"].astype("Int64")
    df["available_docks"] = df["available_docks"].astype("Int64")
    return df


# 一條東西向直線上的 4 站，間距各約 1km
# 1 度經度在緯度 25 約 100.7km；1km ≈ 0.00993 度
DEG_PER_KM = 1 / 100.7

STATIONS = _make_stations([
    {"station_id": "A", "name": "A", "lat": 25.0, "lon": 121.5,
     "available_bikes": 5, "available_docks": 5, "city": "台北市"},
    {"station_id": "B", "name": "B", "lat": 25.0, "lon": 121.5 + DEG_PER_KM,
     "available_bikes": 5, "available_docks": 5, "city": "台北市"},
    {"station_id": "C", "name": "C", "lat": 25.0, "lon": 121.5 + 2 * DEG_PER_KM,
     "available_bikes": 5, "available_docks": 5, "city": "台北市"},
    {"station_id": "D", "name": "D", "lat": 25.0, "lon": 121.5 + 3 * DEG_PER_KM,
     "available_bikes": 5, "available_docks": 5, "city": "台北市"},
])


def _build():
    return build_station_graph(
        STATIONS, free_minutes=30, safety_margin=3,
        speed_kmh=12.0, detour_factor=1.3,
    )


def test_find_nearest_station():
    sid, dist = find_nearest_station(25.0, 121.5 + DEG_PER_KM * 0.1, STATIONS)
    assert sid == "A"
    assert dist < 0.2


def test_find_nearest_station_matches_scalar_haversine():
    # 向量化結果應與逐站 scalar haversine 一致
    qlat, qlon = 25.0, 121.5 + DEG_PER_KM * 2.4  # 介於 C、D 之間，偏 C
    sid, dist = find_nearest_station(qlat, qlon, STATIONS)
    expected = min(
        ((r.station_id, haversine_km(qlat, qlon, r.lat, r.lon))
         for r in STATIONS.itertuples()),
        key=lambda t: t[1],
    )
    assert sid == expected[0]
    assert dist == pytest.approx(expected[1])


def test_direct_route_when_within_free_time():
    g = _build()
    plan = plan_route(g, STATIONS, (25.0, 121.5), (25.0, 121.5 + DEG_PER_KM))
    assert plan.feasible
    assert len(plan.segments) == 1
    assert plan.segments[0].from_station_id == "A"
    assert plan.segments[0].to_station_id == "B"
    assert plan.swap_count == 0


def test_route_to_far_station():
    g = _build()
    # A → D 約 3km × 1.3 / 12 * 60 ≈ 19.5 分 < 27 分上限 → 直達
    plan = plan_route(g, STATIONS, (25.0, 121.5), (25.0, 121.5 + 3 * DEG_PER_KM))
    assert plan.feasible
    assert plan.segments[-1].to_station_id == "D"


def test_fewest_swaps_strategy():
    g = _build()
    plan = plan_route(
        g, STATIONS, (25.0, 121.5), (25.0, 121.5 + 3 * DEG_PER_KM),
        strategy="fewest_swaps",
    )
    assert plan.feasible
    assert plan.strategy == "fewest_swaps"
    assert plan.swap_count == 0


def test_origin_too_far_from_any_station():
    g = _build()
    plan = plan_route(g, STATIONS, (25.02, 121.5), (25.0, 121.5))
    assert not plan.feasible
    assert "起點" in plan.message


def test_cross_circle_edges_blocked_by_default():
    # 一站在台北市（北北桃），一站在高雄市（嘉南高屏），距離雖近但不該連邊
    cross = _make_stations([
        {"station_id": "TP", "name": "TP", "lat": 25.0, "lon": 121.5,
         "available_bikes": 5, "available_docks": 5, "city": "台北市"},
        {"station_id": "KH", "name": "KH", "lat": 25.0, "lon": 121.5005,
         "available_bikes": 5, "available_docks": 5, "city": "高雄市"},
    ])
    g = build_station_graph(cross, 30, 3, 12.0, 1.3)
    assert not g.has_edge("TP", "KH")
    assert not g.has_edge("KH", "TP")


def test_cross_circle_edges_allowed_with_flag():
    cross = _make_stations([
        {"station_id": "TP", "name": "TP", "lat": 25.0, "lon": 121.5,
         "available_bikes": 5, "available_docks": 5, "city": "台北市"},
        {"station_id": "KH", "name": "KH", "lat": 25.0, "lon": 121.5005,
         "available_bikes": 5, "available_docks": 5, "city": "高雄市"},
    ])
    g = build_station_graph(cross, 30, 3, 12.0, 1.3, allow_cross_circle=True)
    assert g.has_edge("TP", "KH")
    plan = plan_route(g, cross, (25.0, 121.5), (25.0, 121.5005))
    assert plan.feasible
    assert "調度費" in plan.message


def test_find_nearby_rent_alternatives_filters_and_sorts():
    # 圍繞 HUB 的鄰站：N1 (100m, 有車)、N2 (200m, 有車)、
    # N3 (250m, 0 車→排除)、FAR (1km→超出 300m 半徑排除)
    near = _make_stations([
        {"station_id": "HUB", "name": "樞紐", "lat": 25.0, "lon": 121.5,
         "available_bikes": 0, "available_docks": 5, "city": "台北市"},
        {"station_id": "N1", "name": "近站一", "lat": 25.0, "lon": 121.5 + 0.1 * DEG_PER_KM,
         "available_bikes": 4, "available_docks": 5, "city": "台北市"},
        {"station_id": "N2", "name": "近站二", "lat": 25.0, "lon": 121.5 + 0.2 * DEG_PER_KM,
         "available_bikes": 2, "available_docks": 5, "city": "台北市"},
        {"station_id": "N3", "name": "近站三無車", "lat": 25.0, "lon": 121.5 + 0.25 * DEG_PER_KM,
         "available_bikes": 0, "available_docks": 5, "city": "台北市"},
        {"station_id": "FAR", "name": "遠站", "lat": 25.0, "lon": 121.5 + DEG_PER_KM,
         "available_bikes": 9, "available_docks": 5, "city": "台北市"},
    ])
    alts = find_nearby_rent_alternatives("HUB", near)
    names = [a[0] for a in alts]
    # 只含有車的近站，依距離排序；排除 0 車 與 超半徑
    assert names == ["近站一", "近站二"]
    # 距離遞增
    assert alts[0][2] < alts[1][2]
    # 回傳含可借車輛數
    assert alts[0][3] == 4


def test_swap_advice_matches_swap_count():
    # 三站直線間距 3km，免費半徑 ~4.15km：P0→P2 需經 P1 換車一次
    line = _make_stations([
        {"station_id": "P0", "name": "P0", "lat": 25.0, "lon": 121.5,
         "available_bikes": 5, "available_docks": 5, "city": "台北市"},
        {"station_id": "P1", "name": "P1", "lat": 25.0, "lon": 121.5 + 3 * DEG_PER_KM,
         "available_bikes": 5, "available_docks": 5, "city": "台北市"},
        {"station_id": "P2", "name": "P2", "lat": 25.0, "lon": 121.5 + 6 * DEG_PER_KM,
         "available_bikes": 5, "available_docks": 5, "city": "台北市"},
    ])
    g = build_station_graph(line, 30, 3, 12.0, 1.3)
    plan = plan_route(g, line, (25.0, 121.5), (25.0, 121.5 + 6 * DEG_PER_KM))
    assert plan.feasible
    assert plan.swap_count == 1
    # 每個換車點對應一筆冷卻建議
    assert len(plan.swap_advice) == plan.swap_count


def test_no_path_returns_friendly_message():
    isolated = _make_stations([
        {"station_id": "X", "name": "X", "lat": 25.0, "lon": 121.5,
         "available_bikes": 5, "available_docks": 5, "city": "台北市"},
        {"station_id": "Y", "name": "Y", "lat": 25.5, "lon": 122.0,
         "available_bikes": 5, "available_docks": 5, "city": "台北市"},
    ])
    g = build_station_graph(
        isolated, free_minutes=30, safety_margin=3,
        speed_kmh=12.0, detour_factor=1.3,
    )
    plan = plan_route(g, isolated, (25.0, 121.5), (25.5, 122.0))
    assert not plan.feasible


def test_start_skips_nearest_station_without_free_bikes():
    # A 最近但沒有一般車（例如只剩電輔車）；B 在步行範圍內且有車 → 應從 B 出發
    stations = STATIONS.copy()
    stations.loc[stations["station_id"] == "A", "available_bikes"] = 0
    g = build_station_graph(
        stations, free_minutes=30, safety_margin=3,
        speed_kmh=12.0, detour_factor=1.3, require_availability=True,
    )
    origin = (25.0, 121.5 + DEG_PER_KM * 0.2)  # 靠近 A，距 B 約 0.8km
    dest = (25.0, 121.5 + 3 * DEG_PER_KM)
    plan = plan_route(g, stations, origin, dest)
    assert plan.feasible
    assert plan.segments[0].from_station_id == "B"


def test_end_skips_nearest_station_without_docks():
    stations = STATIONS.copy()
    stations.loc[stations["station_id"] == "D", "available_docks"] = 0
    g = build_station_graph(
        stations, free_minutes=30, safety_margin=3,
        speed_kmh=12.0, detour_factor=1.3, require_availability=True,
    )
    dest = (25.0, 121.5 + 2.8 * DEG_PER_KM)  # 靠近 D，距 C 約 0.8km
    plan = plan_route(g, stations, (25.0, 121.5), dest)
    assert plan.feasible
    assert plan.segments[-1].to_station_id == "C"


def test_nearest_falls_back_when_no_preferred_station_in_range():
    sid, _ = find_nearest_station(25.0, 121.5, STATIONS, prefer_ids=set())
    assert sid == "A"
