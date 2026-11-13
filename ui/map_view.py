"""主畫面地圖：畫站點與路線，以及點地圖設定起終點。"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core.directions import format_latlon
from visualization.map_renderer import (
    draw_route,
    render_base_map,
    stations_near_route,
    view_for_route,
)


def _set_endpoint_from_click(which: str, lat: float, lon: float) -> None:
    st.session_state["o_text" if which == "origin" else "d_text"] = format_latlon(lat, lon)
    st.session_state["_handled_click"] = (lat, lon)


def _render_map_click_actions(clicked: dict | None) -> None:
    """地圖被點擊時，提供「設為起點 / 設為終點」按鈕。"""
    if not clicked:
        return
    lat, lon = float(clicked["lat"]), float(clicked["lng"])
    if st.session_state.get("_handled_click") == (lat, lon):
        return
    c0, c1, c2 = st.columns([2, 1, 1])
    c0.caption(f"📍 你點的位置：{format_latlon(lat, lon)}")
    c1.button("設為起點", on_click=_set_endpoint_from_click,
              args=("origin", lat, lon), width="stretch")
    c2.button("設為終點", on_click=_set_endpoint_from_click,
              args=("destination", lat, lon), width="stretch")


def _build_map(inp: dict, stations: pd.DataFrame, result: dict | None):
    if result is None:
        # 尚未規劃：顯示全部站點，方便在地圖上點選起終點
        m = render_base_map(stations, zoom_start=12, tiles=inp["map_tiles"])
        return m
    r_o, r_d, r_st, plan = (
        result["origin"], result["destination"], result["stations"], result["plan"]
    )
    display = inp["station_display"]
    if display == "全部站點":
        shown = r_st
    elif display == "不顯示站點":
        shown = r_st.iloc[0:0]
    else:
        shown = stations_near_route(r_st, plan, r_o, r_d)
    center, zoom = view_for_route(plan, r_st, r_o, r_d)
    m = render_base_map(shown, center=center, zoom_start=zoom, tiles=inp["map_tiles"])
    return draw_route(m, plan, r_st, r_o, r_d)
