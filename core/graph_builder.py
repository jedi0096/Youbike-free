"""以 YouBike 站點為節點、騎乘時間為邊權重的圖建構模組。

只在「可用騎乘時間」半徑內連邊，避免完整圖 O(n²) 的儲存與計算成本。
"""
from __future__ import annotations

import numpy as np
import networkx as nx
import pandas as pd

from config import city_to_circle, return_fee_ntd
from core.time_estimator import estimate_riding_time, haversine_km


def build_station_graph(
    stations: pd.DataFrame,
    free_minutes: int,
    safety_margin: int,
    speed_kmh: float,
    detour_factor: float,
    allow_cross_circle: bool = False,
    require_availability: bool = False,
    min_bikes: int = 1,
    min_docks: int = 1,
    free_minutes_by_city: dict[str, int] | None = None,
) -> nx.DiGraph:
    """以站點 DataFrame 建立有向圖。

    Args:
        stations: 經過標準化與過濾的站點 DataFrame。
        free_minutes: 全域免費騎乘上限（分鐘）。當 free_minutes_by_city 為 None
            時對所有邊套用此值。
        safety_margin: 安全餘裕（分鐘）。實際上限 = free_minutes - safety_margin。
        speed_kmh: 平均騎乘時速。
        detour_factor: 路徑修正係數。
        allow_cross_circle: 預設 False，禁止連接「還車需付跨區調度費」的站點對
            （依 config.return_fee_ntd，600~1135 元）。設 True 解鎖。
        require_availability: 預設 False。設 True 時依即時車輛數過濾邊：
            借不到車（available_bikes < min_bikes）的站不會有出邊；
            還不了車（available_docks < min_docks）的站不會有入邊。
        min_bikes: 視為「可借車」的最低可借車輛數。
        min_docks: 視為「可還車」的最低可還空位數。
        free_minutes_by_city: {縣市: 免費上限} 對照。提供時，每條邊的免費時限
            依「起點（借車）站所在縣市」決定——因為免費規則以借車地為準。
            未列於對照中的縣市退回 free_minutes。

    Returns:
        NetworkX 有向圖。
        節點屬性：name, lat, lon, available_bikes, available_docks, city, circle。
        邊屬性：weight（騎乘時間，分鐘）、distance_km。
    """
    # 候選鄰居的粗篩窗以「最寬鬆的免費上限」計算，逐邊再依起點縣市做精確判斷。
    global_free = free_minutes
    if free_minutes_by_city:
        global_free = max(max(free_minutes_by_city.values()), free_minutes)
    global_max_minutes = global_free - safety_margin
    global_max_km = global_max_minutes * speed_kmh / 60 / detour_factor

    g = nx.DiGraph()
    circles: list[str | None] = []
    bikes: list[int] = []
    docks: list[int] = []
    cities: list[str] = []
    for _, row in stations.iterrows():
        circle = city_to_circle(row["city"])
        circles.append(circle)
        cities.append(row["city"])
        n_bikes = int(row["available_bikes"] or 0)
        n_docks = int(row["available_docks"] or 0)
        bikes.append(n_bikes)
        docks.append(n_docks)
        g.add_node(
            row["station_id"],
            name=row["name"],
            lat=float(row["lat"]),
            lon=float(row["lon"]),
            available_bikes=n_bikes,
            available_docks=n_docks,
            city=row["city"],
            circle=circle,
        )

    ids = stations["station_id"].to_numpy()
    lats = stations["lat"].to_numpy(dtype=float)
    lons = stations["lon"].to_numpy(dtype=float)
    n = len(ids)

    def _max_minutes_for(idx: int) -> float:
        if free_minutes_by_city is None:
            return global_max_minutes
        return free_minutes_by_city.get(cities[idx], free_minutes) - safety_margin

    # 用經緯度粗略過濾再算 haversine：1 度緯度 ≈ 111km
    lat_window = global_max_km / 111.0

    for i in range(n):
        # 借不到車的站無法作為某段騎乘的起點 → 直接跳過其所有出邊
        if require_availability and bikes[i] < min_bikes:
            continue
        lat_i, lon_i = lats[i], lons[i]
        max_minutes_i = _max_minutes_for(i)  # 免費規則以借車地（起點 i）為準
        max_km_i = max_minutes_i * speed_kmh / 60 / detour_factor
        for j in _candidate_indices(i, lats, lons, lat_window):
            if not allow_cross_circle and return_fee_ntd(cities[i], cities[j]) > 0:
                continue
            # 還不了車的站無法作為某段騎乘的終點 → 不連入邊
            if require_availability and docks[j] < min_docks:
                continue
            dist = haversine_km(lat_i, lon_i, lats[j], lons[j])
            if dist > max_km_i:
                continue
            minutes = estimate_riding_time(
                lat_i, lon_i, lats[j], lons[j], speed_kmh, detour_factor
            )
            if minutes <= max_minutes_i:
                g.add_edge(ids[i], ids[j], weight=minutes, distance_km=dist)
    return g


def _candidate_indices(
    i: int, lats: np.ndarray, lons: np.ndarray, lat_window: float
) -> np.ndarray:
    """粗略地用緯度視窗過濾候選鄰居，回傳 index 陣列（排除自己）。"""
    mask = np.abs(lats - lats[i]) <= lat_window
    mask[i] = False
    return np.where(mask)[0]
