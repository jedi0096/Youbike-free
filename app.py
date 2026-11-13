"""Streamlit 主程式：YouBike 最省錢騎乘攻略。

執行方式:
    streamlit run app.py

各區塊放在 ui/ 底下：
    ui/sidebar.py   側欄輸入、網址參數
    ui/cache.py     站點 / 站點圖 / 地址 / Google 時間的快取
    ui/planning.py  起終點解析、規劃、Google 驗證
    ui/map_view.py  地圖與點地圖設定起終點
    ui/results.py   摘要、逐步指引、行程表、冷卻提醒
    ui/extras.py    Google 校正、AI 建議（選用）
"""
from __future__ import annotations

import os

import streamlit as st
from streamlit_folium import st_folium

from ui.cache import get_graph
from ui.extras import render_ai_feedback, render_google_refinement
from ui.map_view import _build_map, _render_map_click_actions
from ui.planning import _plan_and_store
from ui.results import (
    _render_endpoint_diagnostic,
    render_cooldown_advice,
    render_itinerary,
    render_steps,
    render_summary,
)
from ui.sidebar import (
    _init_state_from_query_params,
    _update_query_params,
    render_sidebar,
)

# Streamlit Cloud 以 st.secrets 提供金鑰，但 config.settings 讀的是環境變數，
# 故啟動時把 secrets 橋接到 os.environ（本機若用 .env 則此處通常為空，不受影響）。
_SECRET_KEYS = ("GOOGLE_MAPS_API_KEY", "GEMINI_API_KEY", "GEMINI_MODEL")


def _bridge_secrets_to_env() -> None:
    for key in _SECRET_KEYS:
        try:
            val = st.secrets[key]  # 無 secrets 檔時會丟例外
        except Exception:
            continue
        if val:
            os.environ.setdefault(key, str(val))


def main() -> None:
    _bridge_secrets_to_env()
    st.set_page_config(page_title="YouBike 免費騎乘規劃", page_icon="🚲", layout="wide")
    _init_state_from_query_params()
    st.title("🚲 YouBike 最省錢騎乘攻略")
    st.caption("自動規劃中途換車路線，每段都在免費時間內還車，全程免費抵達目的地")

    inp = render_sidebar()

    if not inp["cities"]:
        st.info("請在側欄「進階設定」選擇至少一個城市。")
        return

    graph, stations = get_graph(
        inp["cities"],
        inp["has_tpass"],
        inp["safety_margin"],
        inp["allow_cross_circle"],
        inp["require_availability"],
        inp["use_cooldown"],
        inp["speed_kmh"],
    )
    if stations.empty:
        st.error("無法載入任何站點資料，請稍後按側欄的「重抓即時車況」再試一次。")
        return

    # 只有按下「規劃路線」才重算；結果存入 session_state，後續按 Google / AI 等按鈕
    # 觸發 rerun 時仍能保留（否則 submit 變 False，畫面會跳回預設訊息）。
    if inp["submit"] and _plan_and_store(inp, graph, stations):
        _update_query_params(inp)

    result = st.session_state.get("result")
    m = _build_map(inp, stations, result)

    map_col, info_col = st.columns([2, 1])
    with map_col:
        out = st_folium(
            m, width=None, height=560, key="main_map",
            returned_objects=["last_clicked"],
        )
        _render_map_click_actions((out or {}).get("last_clicked"))
    with info_col:
        if result is None:
            st.info(
                "在左側輸入起點和終點（地址、地標都可以），再按「規劃路線」。\n\n"
                "💡 也可以直接在地圖上點一下，選「設為起點」或「設為終點」。"
            )
            st.caption(f"已載入 {len(stations)} 個站點")
        else:
            render_summary(result["plan"], result.get("depart"))
            for line in result.get("matched", []):
                st.caption(line)
            if result.get("n_removed"):
                st.caption(
                    f"🛰️ 已依 Google 實測自動避開 {result['n_removed']} 段可能超時的路段。"
                )
            _render_endpoint_diagnostic(result)
            st.caption(
                "🔗 網址已更新成這條路線，複製網址就能分享，或加入書籤下次直接用。"
            )
            st.caption(
                f"已載入 {len(stations)} 站、{graph.number_of_edges()} 條連線 · "
                f"規劃耗時 {result['elapsed_ms']:.0f} ms"
            )

    if result is not None and result["plan"].feasible:
        render_steps(result)
        with st.expander("📋 詳細行程表（每段時間、距離、免費餘裕）"):
            render_itinerary(result["plan"], result["fmbc"], result["id_to_city"])
        render_cooldown_advice(result["plan"])
        render_google_refinement(result["plan"], result["stations"], result["fmbc"])
        render_ai_feedback(
            result["plan"], result["stations"], result["fmbc"],
            result["id_to_city"], result["o_label"], result["d_label"],
            result["has_tpass"],
        )


if __name__ == "__main__":
    main()
