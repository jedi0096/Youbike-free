"""Folium 地圖渲染：站點標記、路線繪製、換車點標示。"""
from __future__ import annotations

import math

import folium
import pandas as pd

from core.route_optimizer import RoutePlan

LOW_THRESHOLD = 3


def _station_color(available_bikes: int, available_docks: int) -> str:
    """依車輛 / 空位數回傳 marker 顏色。"""
    if available_bikes == 0 or available_docks == 0:
        return "red"
    if available_bikes < LOW_THRESHOLD or available_docks < LOW_THRESHOLD:
        return "orange"
    return "green"


# 底圖樣式：介面顯示名稱 → folium tiles 名稱
MAP_STYLES: dict[str, str] = {
    "標準": "OpenStreetMap",
    "淺色（看路線較清楚）": "CartoDB positron",
    "深色": "CartoDB dark_matter",
}

_LEGEND_HTML = """
<div style="position: fixed; bottom: 18px; left: 18px; z-index: 9999;
     background: rgba(255,255,255,0.92); padding: 6px 10px; border-radius: 6px;
     font-size: 12px; line-height: 1.6; box-shadow: 0 1px 4px rgba(0,0,0,0.25);
     color: #222;">
  <span style="color:#2e8b57;">●</span> 車位充足
  &nbsp;<span style="color:orange;">●</span> 少於 3
  &nbsp;<span style="color:red;">●</span> 沒車或沒位
</div>
"""


def render_base_map(
    stations: pd.DataFrame,
    center: tuple[float, float] | None = None,
    zoom_start: int = 13,
    tiles: str = "OpenStreetMap",
    show_legend: bool = True,
) -> folium.Map:
    """建立含站點 CircleMarker 的底圖。

    stations 可傳入全部站點，或只傳路線附近的站（減少手機上的繪製負擔）；
    傳入空 DataFrame 時只畫底圖。
    """
    if center is None:
        if stations.empty:
            center = (25.0478, 121.5170)
        else:
            center = (float(stations["lat"].mean()), float(stations["lon"].mean()))
    m = folium.Map(location=center, zoom_start=zoom_start, tiles=tiles)
    if stations.empty:
        return m
    fg = folium.FeatureGroup(name="站點")
    # itertuples 比 iterrows 快很多（站點上千個時差異明顯）
    for row in stations.itertuples(index=False):
        bikes = int(row.available_bikes or 0)
        docks = int(row.available_docks or 0)
        color = _station_color(bikes, docks)
        folium.CircleMarker(
            location=(row.lat, row.lon),
            radius=4,
            color=color,
            fill=True,
            fill_opacity=0.7,
            popup=folium.Popup(
                f"<b>{row.name}</b><br>"
                f"可借: {bikes} / 可還: {docks}<br>"
                f"{row.city}",
                max_width=250,
            ),
        ).add_to(fg)
    fg.add_to(m)
    if show_legend:
        m.get_root().html.add_child(folium.Element(_LEGEND_HTML))
    return m


def stations_near_route(
    stations: pd.DataFrame,
    plan: RoutePlan,
    origin: tuple[float, float],
    destination: tuple[float, float],
    padding_km: float = 0.5,
) -> pd.DataFrame:
    """只留下路線（含起終點）外框附近的站，讓地圖更清爽、手機更順。"""
    if stations.empty:
        return stations
    lats = [origin[0], destination[0]]
    lons = [origin[1], destination[1]]
    if plan.feasible and plan.segments:
        coords = stations.set_index("station_id")[["lat", "lon"]]
        ids = {s.from_station_id for s in plan.segments} | {
            s.to_station_id for s in plan.segments
        }
        sub = coords.loc[coords.index.intersection(list(ids))]
        lats += sub["lat"].tolist()
        lons += sub["lon"].tolist()
    pad = padding_km / 111.0
    lat_lo, lat_hi = min(lats) - pad, max(lats) + pad
    lon_lo, lon_hi = min(lons) - pad * 1.1, max(lons) + pad * 1.1
    mask = stations["lat"].between(lat_lo, lat_hi) & stations["lon"].between(lon_lo, lon_hi)
    return stations[mask]


def view_for_route(
    plan: RoutePlan,
    stations: pd.DataFrame,
    origin: tuple[float, float],
    destination: tuple[float, float],
    map_px: tuple[int, int] = (480, 400),
) -> tuple[tuple[float, float], int]:
    """算出剛好容納整條路線（含起終點）的地圖中心與縮放等級。

    streamlit-folium 會忽略 folium 的 fit_bounds，所以改為直接算 zoom。
    map_px 是扣掉邊距後地圖可視範圍的保守估計（寬, 高 像素），手機上會比桌機窄。
    """
    pts = [origin, destination]
    if plan.feasible and plan.segments:
        coords = stations.set_index("station_id")[["lat", "lon"]].to_dict("index")
        for s in plan.segments:
            for sid in (s.from_station_id, s.to_station_id):
                c = coords.get(sid)
                if c:
                    pts.append((c["lat"], c["lon"]))
    lats = [p[0] for p in pts]
    lons = [p[1] for p in pts]
    center = ((min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2)
    # Web Mercator：zoom z 時全世界寬 256·2^z 像素（360 度經度）
    lon_span = max(max(lons) - min(lons), 1e-4)
    lat_span = max(max(lats) - min(lats), 1e-4) / math.cos(math.radians(center[0]))
    z_lon = math.log2(map_px[0] * 360 / (256 * lon_span))
    z_lat = math.log2(map_px[1] * 360 / (256 * lat_span))
    zoom = int(max(10, min(17, math.floor(min(z_lon, z_lat)))))
    return center, zoom


def draw_route(
    m: folium.Map,
    plan: RoutePlan,
    stations: pd.DataFrame,
    origin: tuple[float, float],
    destination: tuple[float, float],
) -> folium.Map:
    """在地圖上繪製推薦路線、換車站、起終點。"""
    folium.Marker(
        location=origin,
        popup="起點",
        icon=folium.Icon(color="green", icon="play", prefix="fa"),
    ).add_to(m)
    folium.Marker(
        location=destination,
        popup="終點",
        icon=folium.Icon(color="red", icon="flag-checkered", prefix="fa"),
    ).add_to(m)

    if not plan.feasible or not plan.segments:
        return m

    coords_by_id = stations.set_index("station_id")[["lat", "lon"]].to_dict("index")

    ride_segments = [s for s in plan.segments if s.mode == "ride"]
    ride_seen = 0
    swap_no = 0
    for seg in plan.segments:
        a = coords_by_id[seg.from_station_id]
        b = coords_by_id[seg.to_station_id]
        mid_lat = (a["lat"] + b["lat"]) / 2
        mid_lon = (a["lon"] + b["lon"]) / 2

        if seg.mode == "wait":
            # 同站續借等冷卻：原地標示時鐘
            folium.Marker(
                location=(a["lat"], a["lon"]),
                popup=folium.Popup(
                    f"<b>同站等冷卻</b><br>{seg.from_name}<br>約 {seg.minutes:.0f} 分",
                    max_width=250,
                ),
                icon=folium.Icon(color="orange", icon="clock-o", prefix="fa"),
            ).add_to(m)
            continue

        is_ride = seg.mode == "ride"
        color = "#1f4e8c" if is_ride else "#2e8b57"
        label = f"{seg.minutes:.0f} 分" if is_ride else f"步行 {seg.minutes:.0f} 分"
        folium.PolyLine(
            locations=[(a["lat"], a["lon"]), (b["lat"], b["lon"])],
            color=color, weight=5 if is_ride else 4, opacity=0.75,
            dash_array=None if is_ride else "6",
        ).add_to(m)
        folium.Marker(
            location=(mid_lat, mid_lon),
            icon=folium.DivIcon(
                icon_size=(90, 20),
                icon_anchor=(45, 10),
                html=(
                    f'<div style="font-size:11px;font-weight:bold;color:{color};'
                    f'background:rgba(255,255,255,0.85);border-radius:4px;'
                    f'padding:1px 4px;text-align:center;">{label}</div>'
                ),
            ),
        ).add_to(m)

        if is_ride:
            ride_seen += 1
            # 騎乘段抵達站若非最後一段騎乘 → 標示換車點
            if ride_seen < len(ride_segments):
                swap_no += 1
                folium.Marker(
                    location=(b["lat"], b["lon"]),
                    popup=folium.Popup(
                        f"<b>換車點 #{swap_no}</b><br>{seg.to_name}<br>"
                        f"上一段: {seg.minutes:.1f} 分 / {seg.distance_km:.2f} km",
                        max_width=250,
                    ),
                    icon=folium.Icon(color="blue", icon="exchange", prefix="fa"),
                ).add_to(m)

    return m
