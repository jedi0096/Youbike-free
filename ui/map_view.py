"""主畫面地圖：畫站點與路線，以及點地圖設定起終點。"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core.directions import format_latlon
from ui.layout import (
    CARD_GAP_PX,
    CARD_WIDTH_PX,
    KEY_SEARCH,
    KEY_SHEET,
    MOBILE_MAX_WIDTH_PX,
)
from visualization.map_renderer import (
    ViewPadding,
    draw_route,
    fit_view_to_overlays,
    render_base_map,
    route_points,
    stations_near_route,
    view_for_route,
)

# 浮動卡片蓋住地圖的範圍：桌機是左邊一整欄；手機是上面的搜尋卡與下面的結果卡。
# 瀏覽器端會量卡片實際大小；量不到時才用這裡的估計值
# （手機搜尋卡約 215px 高、結果卡最高佔畫面 44%）。
_VIEW_PADDING = ViewPadding(
    left=CARD_GAP_PX + CARD_WIDTH_PX,
    mobile_max_width=MOBILE_MAX_WIDTH_PX,
    mobile_top=215,
    mobile_bottom_ratio=0.44,
    search_selector=f".st-key-{KEY_SEARCH}",
    sheet_selector=f".st-key-{KEY_SHEET}",
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
    st.caption(f"📍 你在地圖上點的位置：{format_latlon(lat, lon)}")
    c1, c2 = st.columns(2)
    c1.button("設為起點", on_click=_set_endpoint_from_click,
              args=("origin", lat, lon), width="stretch")
    c2.button("設為終點", on_click=_set_endpoint_from_click,
              args=("destination", lat, lon), width="stretch")


def _build_map(inp: dict, stations: pd.DataFrame, result: dict | None):
    if result is None:
        # 尚未規劃：顯示全部站點，方便在地圖上點選起終點
        m = render_base_map(
            stations, zoom_start=12, tiles=inp["map_tiles"], zoom_control=False
        )
        return fit_view_to_overlays(m, _VIEW_PADDING)
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
    m = render_base_map(
        shown, center=center, zoom_start=zoom, tiles=inp["map_tiles"], zoom_control=False
    )
    m = draw_route(m, plan, r_st, r_o, r_d)
    return fit_view_to_overlays(m, _VIEW_PADDING, route_points(plan, r_st, r_o, r_d))
