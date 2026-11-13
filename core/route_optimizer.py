"""帶免費時間約束的最短路徑演算法。

策略：
    - "fewest_swaps": BFS，邊權為 1，最小化換車次數。
    - "shortest_time": Dijkstra，邊權為騎乘時間，最小化總時間。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import networkx as nx
import numpy as np
import pandas as pd

from config import CROSS_CIRCLE_FEE_NTD
from core.time_estimator import estimate_walking_time

_EARTH_RADIUS_KM = 6371.0088


Strategy = Literal["fewest_swaps", "shortest_time"]

MAX_WALK_KM = 1.0  # 起終點到最近站可接受的最大步行距離

# 同站續借冷卻：實測還車後約需等 10~15 分鐘才能在同一站再借。
SAME_STATION_COOLDOWN_MIN = (10, 15)
# 換車點建議改借的鄰近站搜尋半徑（公里）與筆數。
NEARBY_RENT_RADIUS_KM = 0.3
NEARBY_RENT_COUNT = 3


@dataclass
class RouteSegment:
    """單一路段。mode 區分騎乘 / 步行換車 / 同站等待冷卻。"""
    from_station_id: str
    to_station_id: str
    from_name: str
    to_name: str
    minutes: float
    distance_km: float
    mode: str = "ride"  # "ride" | "walk" | "wait"


@dataclass
class SwapAdvice:
    """換車點的同站續借冷卻提醒與鄰近改借建議。"""
    station_id: str
    station_name: str
    # 鄰近可改借的替代站：(站名, 步行分鐘, 距離公里, 可借車輛數)
    alternatives: list[tuple[str, float, float, int]] = field(default_factory=list)


@dataclass
class RoutePlan:
    """完整路線規劃結果。"""
    segments: list[RouteSegment]
    total_minutes: float
    swap_count: int
    walk_to_start_min: float
    walk_from_end_min: float
    strategy: Strategy
    feasible: bool
    message: str = ""
    swap_advice: list[SwapAdvice] = field(default_factory=list)
    # node-split 冷卻模型才會用到：換車的步行 + 等待冷卻總時間（分鐘）
    transfer_minutes: float = 0.0


def _haversine_km_vec(
    lat: float, lon: float, lats: np.ndarray, lons: np.ndarray
) -> np.ndarray:
    """向量化 haversine：單點對多點，回傳距離（公里）陣列。"""
    rlat1 = np.radians(lat)
    rlats = np.radians(lats)
    dlat = np.radians(lats - lat)
    dlon = np.radians(lons - lon)
    a = (
        np.sin(dlat / 2) ** 2
        + np.cos(rlat1) * np.cos(rlats) * np.sin(dlon / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))


def find_nearest_station(
    lat: float,
    lon: float,
    stations: pd.DataFrame,
    prefer_ids: set[str] | None = None,
    max_walk_km: float = MAX_WALK_KM,
) -> tuple[str, float]:
    """找出距離指定座標最近的站點，回傳 (station_id, 距離公里)。

    以向量化 haversine 一次算完所有站點距離，較逐站 Python 迴圈快很多。

    prefer_ids：若提供，優先挑「步行範圍內、且在此集合中」的最近站
    （例如「有一般車可借」的站）；範圍內都沒有才退回不分條件的最近站。
    這避免最近的站剛好沒車（或只剩電輔車）時，整條路線被判定為規劃失敗。
    """
    if stations.empty:
        raise ValueError("stations DataFrame 為空")
    lats = stations["lat"].to_numpy(dtype=float)
    lons = stations["lon"].to_numpy(dtype=float)
    dists = _haversine_km_vec(lat, lon, lats, lons)
    if prefer_ids is not None:
        in_set = stations["station_id"].astype(str).isin(prefer_ids).to_numpy()
        ok = in_set & (dists <= max_walk_km)
        if ok.any():
            idx = int(np.flatnonzero(ok)[np.argmin(dists[ok])])
            return str(stations.iloc[idx]["station_id"]), float(dists[idx])
    idx = int(np.argmin(dists))
    return str(stations.iloc[idx]["station_id"]), float(dists[idx])


def find_nearby_rent_alternatives(
    station_id: str,
    stations: pd.DataFrame,
    max_walk_km: float = NEARBY_RENT_RADIUS_KM,
    count: int = NEARBY_RENT_COUNT,
) -> list[tuple[str, float, float, int]]:
    """找出 station_id 鄰近、可改借車的替代站（避免同站續借冷卻）。

    只回傳「有車可借」（available_bikes > 0）且非自身、位於步行半徑內的站，
    依距離由近到遠排序。回傳 (站名, 步行分鐘, 距離公里, 可借車輛數)。
    """
    rows = stations[stations["station_id"] == station_id]
    if rows.empty:
        return []
    origin = rows.iloc[0]
    o_lat, o_lon = float(origin["lat"]), float(origin["lon"])

    others = stations[stations["station_id"] != station_id]
    if others.empty:
        return []
    lats = others["lat"].to_numpy(dtype=float)
    lons = others["lon"].to_numpy(dtype=float)
    dists = _haversine_km_vec(o_lat, o_lon, lats, lons)

    results: list[tuple[str, float, float, int]] = []
    for row, dist in zip(others.itertuples(), dists):
        if dist > max_walk_km:
            continue
        bikes = int(row.available_bikes or 0)
        if bikes <= 0:
            continue
        walk_min = estimate_walking_time(o_lat, o_lon, float(row.lat), float(row.lon))
        results.append((str(row.name), walk_min, float(dist), bikes))

    results.sort(key=lambda t: t[2])
    return results[:count]


def _build_swap_advice(
    segments: list[RouteSegment], stations: pd.DataFrame
) -> list[SwapAdvice]:
    """為每個換車點（中間站）建立同站續借冷卻提醒與鄰近改借建議。"""
    advice: list[SwapAdvice] = []
    # 換車點 = 每段的終站，最後一段除外（最後一段終站是目的地，不再續借）
    for seg in segments[:-1]:
        alts = find_nearby_rent_alternatives(seg.to_station_id, stations)
        advice.append(
            SwapAdvice(
                station_id=seg.to_station_id,
                station_name=seg.to_name,
                alternatives=alts,
            )
        )
    return advice


def _build_segments(
    graph: nx.DiGraph, node_path: list[str]
) -> tuple[list[RouteSegment], float]:
    """從節點路徑建構 RouteSegment 列表並計算總時間。"""
    segments: list[RouteSegment] = []
    total = 0.0
    for u, v in zip(node_path[:-1], node_path[1:]):
        data = graph.edges[u, v]
        segments.append(
            RouteSegment(
                from_station_id=u,
                to_station_id=v,
                from_name=graph.nodes[u]["name"],
                to_name=graph.nodes[v]["name"],
                minutes=data["weight"],
                distance_km=data["distance_km"],
            )
        )
        total += data["weight"]
    return segments, total


def plan_route(
    graph: nx.DiGraph,
    stations: pd.DataFrame,
    origin: tuple[float, float],
    destination: tuple[float, float],
    strategy: Strategy = "fewest_swaps",
) -> RoutePlan:
    """規劃從 origin 到 destination 的免費騎乘路線。"""
    o_lat, o_lon = origin
    d_lat, d_lon = destination

    # 優先選「借得到車」的起站與「還得了車」的終站（圖已依即時車況過濾邊）
    can_rent = {str(n) for n in graph.nodes if graph.out_degree(n) > 0}
    can_return = {str(n) for n in graph.nodes if graph.in_degree(n) > 0}
    start_id, start_dist = find_nearest_station(o_lat, o_lon, stations, can_rent)
    end_id, end_dist = find_nearest_station(d_lat, d_lon, stations, can_return)
    walk_to_start = estimate_walking_time(
        o_lat, o_lon,
        graph.nodes[start_id]["lat"], graph.nodes[start_id]["lon"],
    )
    walk_from_end = estimate_walking_time(
        graph.nodes[end_id]["lat"], graph.nodes[end_id]["lon"],
        d_lat, d_lon,
    )

    def _fail(msg: str) -> RoutePlan:
        return RoutePlan(
            segments=[], total_minutes=0.0, swap_count=0,
            walk_to_start_min=walk_to_start, walk_from_end_min=walk_from_end,
            strategy=strategy, feasible=False, message=msg,
        )

    if start_dist > MAX_WALK_KM:
        return _fail(f"起點附近 {MAX_WALK_KM}km 內無 YouBike 站（最近 {start_dist:.2f}km）")
    if end_dist > MAX_WALK_KM:
        return _fail(f"終點附近 {MAX_WALK_KM}km 內無 YouBike 站（最近 {end_dist:.2f}km）")

    if start_id == end_id:
        return RoutePlan(
            segments=[], total_minutes=0.0, swap_count=0,
            walk_to_start_min=walk_to_start, walk_from_end_min=walk_from_end,
            strategy=strategy, feasible=True,
            message="起終點最近站為同一站，直接步行即可",
        )

    try:
        if strategy == "fewest_swaps":
            node_path = nx.shortest_path(graph, start_id, end_id)
        elif strategy == "shortest_time":
            node_path = nx.shortest_path(graph, start_id, end_id, weight="weight")
        else:
            raise ValueError(f"未知策略: {strategy}")
    except nx.NetworkXNoPath:
        return _fail("找不到可行的免費路線（站點之間距離過遠），建議直接付費騎乘")
    except nx.NodeNotFound as e:
        return _fail(f"圖中找不到節點: {e}")

    segments, total = _build_segments(graph, node_path)

    cross_circle_segments = [
        s for s in segments
        if graph.nodes[s.from_station_id].get("circle")
        != graph.nodes[s.to_station_id].get("circle")
    ]
    msg = ""
    if cross_circle_segments:
        lo, hi = CROSS_CIRCLE_FEE_NTD
        msg = (
            f"⚠️ 路線包含 {len(cross_circle_segments)} 段跨生活圈騎乘，"
            f"還車時可能被收 {lo}~{hi} 元調度費（即非全程免費）"
        )

    return RoutePlan(
        segments=segments,
        total_minutes=total,
        swap_count=max(0, len(segments) - 1),
        walk_to_start_min=walk_to_start,
        walk_from_end_min=walk_from_end,
        strategy=strategy,
        feasible=True,
        message=msg,
        swap_advice=_build_swap_advice(segments, stations),
    )
