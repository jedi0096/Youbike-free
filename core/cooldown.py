"""同站續借冷卻的 node-split 路徑模型（opt-in 進階模式）。

一般模式（route_optimizer）裡，路徑每經過一個中間站就是「在同站還車後立刻再借」，
但實測同站續借需等 10~15 分鐘冷卻。本模組把每個實體站點拆成兩個邏輯節點，讓
「步行到鄰站借車」與「原站等冷卻」都成為可被最佳化挑選的選項：

    (sid, "out")  借車節點：在此租車出發
    (sid, "in")   還車節點：在此還車抵達

邊：
    ride : (S,"out") -> (T,"in")   騎乘（免費），weight = 騎乘分鐘
    wait : (S,"in")  -> (S,"out")  同站續借，需等冷卻，weight = cooldown 分鐘
    walk : (S,"in")  -> (U,"out")  步行到鄰站借車（免冷卻），weight = 步行分鐘

策略：
    - "fewest_swaps"：以邊屬性 ride_count（騎乘 1、轉乘 0）最小化騎乘段數。
    - "shortest_time"：以實際分鐘數（含步行 / 等待）最小化總時間。
"""
from __future__ import annotations

import networkx as nx
import numpy as np
import pandas as pd

from config import CROSS_CIRCLE_FEE_NTD, city_to_circle
from core.route_optimizer import (
    MAX_WALK_KM,
    RoutePlan,
    RouteSegment,
    Strategy,
    find_nearest_station,
)
from core.time_estimator import (
    estimate_riding_time,
    estimate_walking_time,
    haversine_km,
)

DEFAULT_COOLDOWN_MIN = 12.0   # 同站續借冷卻（10~15 分取中位）
DEFAULT_WALK_RADIUS_KM = 0.3  # 步行換車可接受半徑（約 300m）


def build_cooldown_graph(
    stations: pd.DataFrame,
    free_minutes: int,
    safety_margin: int,
    speed_kmh: float,
    detour_factor: float,
    *,
    free_minutes_by_city: dict[str, int] | None = None,
    allow_cross_circle: bool = False,
    require_availability: bool = False,
    min_bikes: int = 1,
    min_docks: int = 1,
    cooldown_min: float = DEFAULT_COOLDOWN_MIN,
    walk_radius_km: float = DEFAULT_WALK_RADIUS_KM,
) -> nx.DiGraph:
    """建立 node-split 冷卻圖。參數語意與 build_station_graph 一致，另加冷卻 / 步行設定。"""
    global_free = free_minutes
    if free_minutes_by_city:
        global_free = max(max(free_minutes_by_city.values()), free_minutes)
    global_max_minutes = global_free - safety_margin
    global_max_km = global_max_minutes * speed_kmh / 60 / detour_factor

    ids = stations["station_id"].to_numpy()
    names = stations["name"].to_numpy()
    lats = stations["lat"].to_numpy(dtype=float)
    lons = stations["lon"].to_numpy(dtype=float)
    cities = stations["city"].to_numpy()
    bikes = stations["available_bikes"].fillna(0).astype(int).to_numpy()
    docks = stations["available_docks"].fillna(0).astype(int).to_numpy()
    circles = [city_to_circle(c) for c in cities]
    n = len(ids)

    g = nx.DiGraph()
    for i in range(n):
        attrs = dict(
            name=names[i], lat=float(lats[i]), lon=float(lons[i]),
            available_bikes=int(bikes[i]), available_docks=int(docks[i]),
            city=cities[i], circle=circles[i], station_id=ids[i],
        )
        g.add_node((ids[i], "out"), **attrs)
        g.add_node((ids[i], "in"), **attrs)

    def _max_minutes_for(idx: int) -> float:
        if free_minutes_by_city is None:
            return global_max_minutes
        return free_minutes_by_city.get(cities[idx], free_minutes) - safety_margin

    ride_window = global_max_km / 111.0
    walk_window = walk_radius_km / 111.0

    for i in range(n):
        can_rent_i = not (require_availability and bikes[i] < min_bikes)

        # wait：同站還車後等冷卻再借（需該站可借車）
        if can_rent_i:
            g.add_edge((ids[i], "in"), (ids[i], "out"),
                       weight=cooldown_min, distance_km=0.0, mode="wait", ride_count=0)

        # ride：(i,out) -> (j,in)，免費騎乘（起點 i 需可借車）
        if can_rent_i:
            lat_i, lon_i = lats[i], lons[i]
            max_minutes_i = _max_minutes_for(i)
            max_km_i = max_minutes_i * speed_kmh / 60 / detour_factor
            for j in _candidates(i, lats, ride_window, lons):
                if not allow_cross_circle and circles[i] != circles[j]:
                    continue
                if require_availability and docks[j] < min_docks:
                    continue  # j 還不了車 → 不可當終點
                dist = haversine_km(lat_i, lon_i, lats[j], lons[j])
                if dist > max_km_i:
                    continue
                minutes = estimate_riding_time(
                    lat_i, lon_i, lats[j], lons[j], speed_kmh, detour_factor
                )
                if minutes <= max_minutes_i:
                    g.add_edge((ids[i], "out"), (ids[j], "in"),
                               weight=minutes, distance_km=dist, mode="ride", ride_count=1)

        # walk：(i,in) -> (j,out)，還車後步行到鄰站借車（鄰站 j 需可借車）
        for j in _candidates(i, lats, walk_window, lons):
            if i == j:
                continue
            if not allow_cross_circle and circles[i] != circles[j]:
                continue
            if require_availability and bikes[j] < min_bikes:
                continue
            dist = haversine_km(lats[i], lons[i], lats[j], lons[j])
            if dist > walk_radius_km:
                continue
            walk_min = estimate_walking_time(lats[i], lons[i], lats[j], lons[j])
            g.add_edge((ids[i], "in"), (ids[j], "out"),
                       weight=walk_min, distance_km=dist, mode="walk", ride_count=0)

    return g


def _candidates(
    i: int, lats: np.ndarray, window: float, lons: np.ndarray | None = None
) -> np.ndarray:
    """以緯度（及經度）視窗粗篩候選鄰居；window 為緯度度數。"""
    mask = np.abs(lats - lats[i]) <= window
    if lons is not None:
        mask &= np.abs(lons - lons[i]) <= _lon_window(window, lats[i])
    mask[i] = False
    return np.where(mask)[0]


def _lon_window(lat_window: float, lat: float) -> float:
    """把緯度視窗換成經度視窗：經度 1 度的長度隨緯度乘上 cos(緯度) 縮短。"""
    return lat_window / max(np.cos(np.radians(lat)), 0.1)


def plan_cooldown_route(
    graph: nx.DiGraph,
    stations: pd.DataFrame,
    origin: tuple[float, float],
    destination: tuple[float, float],
    strategy: Strategy = "fewest_swaps",
    max_walk_km: float = MAX_WALK_KM,
) -> RoutePlan:
    """在 node-split 冷卻圖上規劃路線。回傳含 ride/walk/wait 各段的 RoutePlan。"""
    o_lat, o_lon = origin
    d_lat, d_lon = destination

    # 優先選「借得到車」的起站與「還得了車」的終站
    can_rent = {str(n[0]) for n in graph.nodes
                if n[1] == "out" and graph.out_degree(n) > 0}
    can_return = {str(n[0]) for n in graph.nodes
                  if n[1] == "in" and graph.in_degree(n) > 0}
    start_id, start_dist = find_nearest_station(o_lat, o_lon, stations, can_rent, max_walk_km)
    end_id, end_dist = find_nearest_station(d_lat, d_lon, stations, can_return, max_walk_km)

    start_node = (start_id, "out")
    end_node = (end_id, "in")
    walk_to_start = estimate_walking_time(
        o_lat, o_lon, graph.nodes[start_node]["lat"], graph.nodes[start_node]["lon"]
    )
    walk_from_end = estimate_walking_time(
        graph.nodes[end_node]["lat"], graph.nodes[end_node]["lon"], d_lat, d_lon
    )

    def _fail(msg: str) -> RoutePlan:
        return RoutePlan(
            segments=[], total_minutes=0.0, swap_count=0,
            walk_to_start_min=walk_to_start, walk_from_end_min=walk_from_end,
            strategy=strategy, feasible=False, message=msg,
        )

    if start_dist > max_walk_km:
        return _fail(f"起點附近 {max_walk_km:g}km 內無 YouBike 站（最近 {start_dist:.2f}km）")
    if end_dist > max_walk_km:
        return _fail(f"終點附近 {max_walk_km:g}km 內無 YouBike 站（最近 {end_dist:.2f}km）")
    if start_id == end_id:
        return RoutePlan(
            segments=[], total_minutes=0.0, swap_count=0,
            walk_to_start_min=walk_to_start, walk_from_end_min=walk_from_end,
            strategy=strategy, feasible=True,
            message="起終點最近站為同一站，直接步行即可",
        )

    weight = "ride_count" if strategy == "fewest_swaps" else "weight"
    try:
        node_path = nx.shortest_path(graph, start_node, end_node, weight=weight)
    except nx.NetworkXNoPath:
        return _fail("找不到可行的免費路線（站點之間距離過遠），建議直接付費騎乘")
    except nx.NodeNotFound as e:
        return _fail(f"圖中找不到節點: {e}")

    segments, ride_min, transfer_min, ride_count = _build_segments(graph, node_path)

    cross = [
        s for s in segments
        if s.mode == "ride"
        and graph.nodes[(s.from_station_id, "out")].get("circle")
        != graph.nodes[(s.to_station_id, "in")].get("circle")
    ]
    msg = ""
    if cross:
        lo, hi = CROSS_CIRCLE_FEE_NTD
        msg = (
            f"⚠️ 路線包含 {len(cross)} 段跨生活圈騎乘，"
            f"還車時可能被收 {lo}~{hi} 元調度費（即非全程免費）"
        )

    return RoutePlan(
        segments=segments,
        total_minutes=ride_min,
        swap_count=max(0, ride_count - 1),
        walk_to_start_min=walk_to_start,
        walk_from_end_min=walk_from_end,
        strategy=strategy,
        feasible=True,
        message=msg,
        transfer_minutes=transfer_min,
    )


def _build_segments(
    graph: nx.DiGraph, node_path: list[tuple[str, str]]
) -> tuple[list[RouteSegment], float, float, int]:
    """從 split 節點路徑建構各段；回傳 (segments, 騎乘總分, 轉乘總分, 騎乘段數)。"""
    segments: list[RouteSegment] = []
    ride_min = 0.0
    transfer_min = 0.0
    ride_count = 0
    for u, v in zip(node_path[:-1], node_path[1:]):
        data = graph.edges[u, v]
        mode = data["mode"]
        seg = RouteSegment(
            from_station_id=u[0],
            to_station_id=v[0],
            from_name=graph.nodes[u]["name"],
            to_name=graph.nodes[v]["name"],
            minutes=data["weight"],
            distance_km=data["distance_km"],
            mode=mode,
        )
        segments.append(seg)
        if mode == "ride":
            ride_min += data["weight"]
            ride_count += 1
        else:
            transfer_min += data["weight"]
    return segments, ride_min, transfer_min, ride_count
