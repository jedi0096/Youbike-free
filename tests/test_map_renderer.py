"""測試地圖渲染：顏色判斷、底圖建立、路線繪製不出錯。"""
from __future__ import annotations

import pandas as pd

from core.route_optimizer import RoutePlan, RouteSegment
from visualization.map_renderer import _station_color, draw_route, render_base_map


def test_station_color_thresholds():
    assert _station_color(0, 5) == "red"
    assert _station_color(5, 0) == "red"
    assert _station_color(2, 5) == "orange"
    assert _station_color(5, 2) == "orange"
    assert _station_color(5, 5) == "green"
    assert _station_color(100, 100) == "green"


def _sample_stations():
    df = pd.DataFrame([
        {"station_id": "A", "name": "A", "lat": 25.0, "lon": 121.5,
         "available_bikes": 5, "available_docks": 5, "city": "台北市"},
        {"station_id": "B", "name": "B", "lat": 25.01, "lon": 121.51,
         "available_bikes": 0, "available_docks": 10, "city": "台北市"},
    ])
    df["available_bikes"] = df["available_bikes"].astype("Int64")
    df["available_docks"] = df["available_docks"].astype("Int64")
    return df


def test_render_base_map_returns_folium_map():
    import folium
    m = render_base_map(_sample_stations())
    assert isinstance(m, folium.Map)
    # 渲染為 HTML 應成功且包含站名
    html = m._repr_html_()
    assert "A" in html


def test_draw_route_with_feasible_plan():
    import folium
    stations = _sample_stations()
    plan = RoutePlan(
        segments=[
            RouteSegment("A", "B", "A", "B", minutes=10.0, distance_km=1.5),
        ],
        total_minutes=10.0,
        swap_count=0,
        walk_to_start_min=2.0,
        walk_from_end_min=2.0,
        strategy="fewest_swaps",
        feasible=True,
    )
    m = render_base_map(stations)
    out = draw_route(m, plan, stations, (25.0, 121.5), (25.01, 121.51))
    assert isinstance(out, folium.Map)
    html = out._repr_html_()
    assert "起點" in html and "終點" in html


def test_draw_route_with_cooldown_modes_renders():
    import folium
    df = pd.DataFrame([
        {"station_id": "A", "name": "A", "lat": 25.0, "lon": 121.5,
         "available_bikes": 5, "available_docks": 5, "city": "台北市"},
        {"station_id": "B", "name": "B", "lat": 25.01, "lon": 121.51,
         "available_bikes": 5, "available_docks": 5, "city": "台北市"},
        {"station_id": "Bn", "name": "B鄰", "lat": 25.0105, "lon": 121.5105,
         "available_bikes": 5, "available_docks": 5, "city": "台北市"},
        {"station_id": "C", "name": "C", "lat": 25.02, "lon": 121.52,
         "available_bikes": 5, "available_docks": 5, "city": "台北市"},
    ])
    df["available_bikes"] = df["available_bikes"].astype("Int64")
    df["available_docks"] = df["available_docks"].astype("Int64")
    plan = RoutePlan(
        segments=[
            RouteSegment("A", "B", "A", "B", 10.0, 1.5, mode="ride"),
            RouteSegment("B", "Bn", "B", "B鄰", 1.0, 0.1, mode="walk"),
            RouteSegment("Bn", "C", "B鄰", "C", 9.0, 1.4, mode="ride"),
        ],
        total_minutes=19.0, swap_count=1,
        walk_to_start_min=1.0, walk_from_end_min=1.0,
        strategy="shortest_time", feasible=True, transfer_minutes=1.0,
    )
    m = render_base_map(df)
    out = draw_route(m, plan, df, (25.0, 121.5), (25.02, 121.52))
    assert isinstance(out, folium.Map)
    # 以 ASCII 結構標記驗證（folium 會把中文 DivIcon 文字轉成 \uXXXX，故不直接比中文）
    html = out.get_root().render()
    assert "dashArray" in html       # 步行換車段以虛線繪製
    assert "clock-o" not in html     # 此路線以步行換車，無等冷卻段


def test_draw_route_wait_segment_adds_cooldown_marker():
    import folium
    df = pd.DataFrame([
        {"station_id": "A", "name": "A", "lat": 25.0, "lon": 121.5,
         "available_bikes": 5, "available_docks": 5, "city": "台北市"},
        {"station_id": "B", "name": "B", "lat": 25.01, "lon": 121.51,
         "available_bikes": 5, "available_docks": 5, "city": "台北市"},
        {"station_id": "C", "name": "C", "lat": 25.02, "lon": 121.52,
         "available_bikes": 5, "available_docks": 5, "city": "台北市"},
    ])
    df["available_bikes"] = df["available_bikes"].astype("Int64")
    df["available_docks"] = df["available_docks"].astype("Int64")
    plan = RoutePlan(
        segments=[
            RouteSegment("A", "B", "A", "B", 10.0, 1.5, mode="ride"),
            RouteSegment("B", "B", "B", "B", 12.0, 0.0, mode="wait"),
            RouteSegment("B", "C", "B", "C", 9.0, 1.4, mode="ride"),
        ],
        total_minutes=19.0, swap_count=1,
        walk_to_start_min=1.0, walk_from_end_min=1.0,
        strategy="shortest_time", feasible=True, transfer_minutes=12.0,
    )
    out = draw_route(render_base_map(df), plan, df, (25.0, 121.5), (25.02, 121.52))
    html = out.get_root().render()
    assert "clock-o" in html          # 等冷卻 marker
    assert isinstance(out, folium.Map)


def test_draw_route_with_infeasible_plan_still_marks_endpoints():
    stations = _sample_stations()
    plan = RoutePlan(
        segments=[], total_minutes=0, swap_count=0,
        walk_to_start_min=0, walk_from_end_min=0,
        strategy="fewest_swaps", feasible=False, message="無路",
    )
    m = render_base_map(stations)
    out = draw_route(m, plan, stations, (25.0, 121.5), (25.01, 121.51))
    html = out._repr_html_()
    assert "起點" in html and "終點" in html


def test_route_points_include_endpoints_and_route_stations():
    from visualization.map_renderer import route_points

    df = _sample_stations()
    ids = df["station_id"].tolist()
    plan = RoutePlan(
        segments=[RouteSegment(ids[0], ids[1], "a", "b", 10.0, 1.5)],
        total_minutes=10.0, swap_count=0, walk_to_start_min=1.0,
        walk_from_end_min=1.0, strategy="fewest_swaps", feasible=True,
    )
    pts = route_points(plan, df, (24.9, 121.4), (25.2, 121.7))
    assert pts[:2] == [(24.9, 121.4), (25.2, 121.7)]
    assert len(pts) == 4
    # 找不到路線時只剩起終點
    plan.feasible = False
    assert route_points(plan, df, (24.9, 121.4), (25.2, 121.7)) == [
        (24.9, 121.4), (25.2, 121.7),
    ]


def test_fit_view_to_overlays_emits_padding_script():
    from visualization.map_renderer import ViewPadding, fit_view_to_overlays

    m = render_base_map(_sample_stations(), zoom_control=False)
    pad = ViewPadding(left=416, mobile_top=200, mobile_bottom_ratio=0.44)
    fit_view_to_overlays(m, pad, [(25.0, 121.5), (25.1, 121.4)])
    html = m.get_root().render()
    assert "fitBounds" in html
    assert '"left": 416' in html
    # bounds = [[南, 西], [北, 東]]
    assert "[[25.0, 121.4], [25.1, 121.5]]" in html
    # 內建的縮放按鈕關掉，改由腳本放到右上角
    assert "zoomControl\": false" in html or "zoomControl: false" in html
    assert 'position: "topright"' in html


def test_fit_view_to_overlays_without_points_keeps_view():
    from visualization.map_renderer import ViewPadding, fit_view_to_overlays

    m = fit_view_to_overlays(render_base_map(_sample_stations()), ViewPadding())
    html = m.get_root().render()
    assert "var bounds = null;" in html
