"""測試逐步指引、座標輸入辨識、導航連結與地圖視野計算。"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from core.directions import (
    build_steps,
    estimate_arrival,
    google_maps_dir_url,
    parse_latlon,
    total_trip_minutes,
)
from core.route_optimizer import RoutePlan, RouteSegment, SwapAdvice
from visualization.map_renderer import stations_near_route, view_for_route


def test_parse_latlon_accepts_common_formats():
    assert parse_latlon("25.0478, 121.5170") == (25.0478, 121.517)
    assert parse_latlon("25.0478,121.5170") == (25.0478, 121.517)
    assert parse_latlon("(25.0478 121.5170)") == (25.0478, 121.517)
    assert parse_latlon("25.0478，121.5170") == (25.0478, 121.517)


def test_parse_latlon_swaps_reversed_and_rejects_others():
    assert parse_latlon("121.5170, 25.0478") == (25.0478, 121.517)
    assert parse_latlon("台北車站") is None
    assert parse_latlon("") is None
    assert parse_latlon("40.7, -74.0") is None  # 不在台灣


def test_google_maps_dir_url():
    url = google_maps_dir_url((25.0, 121.5), (25.1, 121.6), "walking")
    assert url.startswith("https://www.google.com/maps/dir/?api=1")
    assert "travelmode=walking" in url
    assert "origin=25.000000%2C121.500000" in url


COORDS = {"A": (25.0, 121.5), "B": (25.03, 121.5), "C": (25.06, 121.5)}


def _plan(segments, **kw):
    base = dict(
        segments=segments, total_minutes=sum(s.minutes for s in segments if s.mode == "ride"),
        swap_count=max(0, sum(s.mode == "ride" for s in segments) - 1),
        walk_to_start_min=2.0, walk_from_end_min=3.0,
        strategy="fewest_swaps", feasible=True,
    )
    base.update(kw)
    return RoutePlan(**base)


def test_build_steps_two_rides_with_swap_advice():
    plan = _plan(
        [RouteSegment("A", "B", "A站", "B站", 20.0, 3.3),
         RouteSegment("B", "C", "B站", "C站", 26.5, 3.3)],
        swap_advice=[SwapAdvice("B", "B站", [("B2站", 2.4, 0.15, 7)])],
    )
    steps = build_steps(
        plan, COORDS, (24.999, 121.5), (25.061, 121.5),
        free_limit_for={"A": 30, "B": 30},
        availability={"A": (5, 5), "B": (5, 1), "C": (5, 9)},
    )
    texts = [s.text for s in steps]
    assert texts[0] == "步行到「A站」借車"
    assert "B站" in texts[1] and "還車換車" in texts[1]
    assert "C站" in texts[2] and texts[2].startswith("騎到「C站」還車（")
    assert texts[-1] == "步行抵達終點"
    # 換車點附上最近的改借站
    assert "B2站" in steps[1].note
    # B 站只剩 1 個空位 → 提醒
    assert steps[1].warn
    # 第二段餘裕 3.5 分 < 4 → 提醒
    assert steps[2].warn and "還有 4 分餘裕" in steps[2].note
    assert all(s.nav_url for s in steps)


def test_build_steps_cooldown_modes_and_same_station():
    plan = _plan(
        [RouteSegment("A", "B", "A站", "B站", 20.0, 3.3),
         RouteSegment("B", "B", "B站", "B站", 12.0, 0.0, mode="wait"),
         RouteSegment("B", "C", "B站", "C站", 20.0, 3.3)],
        transfer_minutes=12.0,
    )
    steps = build_steps(plan, COORDS, (25.0, 121.5), (25.06, 121.5))
    assert any(s.icon == "⏱️" for s in steps)
    assert total_trip_minutes(plan) == 20 + 20 + 12 + 2 + 3

    same = _plan([], total_minutes=0.0)
    s2 = build_steps(same, COORDS, (25.0, 121.5), (25.001, 121.5))
    assert len(s2) == 1 and "直接走" in s2[0].text

    assert build_steps(_plan([], feasible=False), COORDS, (0, 0), (0, 0)) == []


def test_estimate_arrival():
    plan = _plan([RouteSegment("A", "B", "A站", "B站", 25.0, 3.3)])
    t = estimate_arrival(datetime(2026, 1, 1, 8, 0), plan)
    assert t == datetime(2026, 1, 1, 8, 30)


def _stations():
    return pd.DataFrame([
        {"station_id": k, "name": k, "lat": v[0], "lon": v[1],
         "available_bikes": 5, "available_docks": 5, "city": "台北市"}
        for k, v in {**COORDS, "FAR": (25.5, 121.9)}.items()
    ])


def test_stations_near_route_and_view():
    plan = _plan([RouteSegment("A", "C", "A站", "C站", 25.0, 6.6)])
    df = _stations()
    near = stations_near_route(df, plan, (25.0, 121.5), (25.06, 121.5))
    assert set(near["station_id"]) == {"A", "B", "C"}
    center, zoom = view_for_route(plan, df, (25.0, 121.5), (25.06, 121.5))
    assert abs(center[0] - 25.03) < 1e-9
    assert 10 <= zoom <= 17
    # 路線越長，縮放等級越小（看得越廣）
    long_plan = _plan([RouteSegment("A", "FAR", "A站", "FAR", 25.0, 60)])
    _, zoom_far = view_for_route(long_plan, df, (25.0, 121.5), (25.5, 121.9))
    assert zoom_far < zoom
