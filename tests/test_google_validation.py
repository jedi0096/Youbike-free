"""測試 Google 實測驗證會自動避開超時路段並重新規劃。"""
from __future__ import annotations

import pandas as pd

from ui import planning
from core.graph_builder import build_station_graph
from core.time_estimator import haversine_km

DEG_PER_KM = 1 / 100.7


def _stations():
    df = pd.DataFrame([
        {"station_id": "A", "name": "A", "lat": 25.0, "lon": 121.5,
         "available_bikes": 5, "available_docks": 5, "city": "台北市"},
        {"station_id": "B", "name": "B", "lat": 25.0, "lon": 121.5 + 1.5 * DEG_PER_KM,
         "available_bikes": 5, "available_docks": 5, "city": "台北市"},
        {"station_id": "D", "name": "D", "lat": 25.0, "lon": 121.5 + 3 * DEG_PER_KM,
         "available_bikes": 5, "available_docks": 5, "city": "台北市"},
    ])
    df["available_bikes"] = df["available_bikes"].astype("Int64")
    df["available_docks"] = df["available_docks"].astype("Int64")
    return df


def _fake_gmaps(o_lat, o_lon, d_lat, d_lon):
    # 長段（A→D ~3km）Google 說 40 分（超過 30），短段 ~12 分
    d = haversine_km(o_lat, o_lon, d_lat, d_lon)
    return (40.0 if d > 2.5 else 12.0), d, "driving_distance"


def test_validation_reroutes_around_over_limit_segment(monkeypatch):
    stations = _stations()
    graph = build_station_graph(stations, 30, 3, 12.0, 1.3)
    # 直達 A→D 估算約 19.5 分 → 圖中存在；但 Google 實測 40 分需避開
    assert graph.has_edge("A", "D")

    monkeypatch.setattr(planning, "gmaps_travel_cached", _fake_gmaps)

    plan, rows, any_over, gtimes, n_removed = planning.plan_with_google_validation(
        graph, stations, (25.0, 121.5), (25.0, 121.5 + 3 * DEG_PER_KM),
        strategy="fewest_swaps", fmbc={"台北市": 30}, use_cooldown=False,
    )
    assert plan.feasible
    # 應改走 A→B→D 兩段，避開 Google 實測超時的 A→D
    assert [s.to_station_id for s in plan.segments] == ["B", "D"]
    assert n_removed == 1
    assert not any_over  # 重規劃後各段都在免費上限內


def test_validation_passes_when_all_within_limit(monkeypatch):
    stations = _stations()
    graph = build_station_graph(stations, 30, 3, 12.0, 1.3)
    # 所有段 Google 都說 10 分 → 不需重規劃，保留最少換車的直達
    monkeypatch.setattr(planning, "gmaps_travel_cached", lambda *a: (10.0, 1.0, "bicycling"))

    plan, rows, any_over, gtimes, n_removed = planning.plan_with_google_validation(
        graph, stations, (25.0, 121.5), (25.0, 121.5 + 3 * DEG_PER_KM),
        strategy="fewest_swaps", fmbc={"台北市": 30}, use_cooldown=False,
    )
    assert plan.feasible
    assert n_removed == 0
    assert [s.to_station_id for s in plan.segments] == ["D"]  # 直達
