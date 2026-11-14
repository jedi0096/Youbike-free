"""Folium 地圖渲染：站點標記、路線繪製、換車點標示。"""
from __future__ import annotations

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


def render_base_map(
    stations: pd.DataFrame,
    center: tuple[float, float] | None = None,
    zoom_start: int = 13,
) -> folium.Map:
    """建立含所有站點 CircleMarker 的底圖。"""
    if center is None:
        center = (float(stations["lat"].mean()), float(stations["lon"].mean()))
    m = folium.Map(location=center, zoom_start=zoom_start, tiles="OpenStreetMap")
    fg = folium.FeatureGroup(name="站點")
    for _, row in stations.iterrows():
        bikes = int(row["available_bikes"] or 0)
        docks = int(row["available_docks"] or 0)
        color = _station_color(bikes, docks)
        ebikes = row.get("available_ebikes")
        if ebikes is None or pd.isna(ebikes):
            bike_text = f"可借: {bikes}（可能含電輔車）"
        else:
            bike_text = f"可借一般車: {bikes}<br>電輔車: {int(ebikes)}（不免費）"
        folium.CircleMarker(
            location=(row["lat"], row["lon"]),
            radius=4,
            color=color,
            fill=True,
            fill_opacity=0.7,
            popup=folium.Popup(
                f"<b>{row['name']}</b><br>"
                f"{bike_text}<br>可還: {docks}<br>"
                f"{row['city']}",
                max_width=250,
            ),
        ).add_to(fg)
    fg.add_to(m)
    return m


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
