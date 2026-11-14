"""Streamlit 主程式：YouBike 最省錢騎乘攻略。

執行方式:
    streamlit run app.py

各區塊放在 ui/ 底下：
    ui/layout.py    版面 CSS：地圖鋪滿畫面，搜尋卡與結果卡浮在上面
    ui/controls.py  搜尋卡的輸入（起終點、偏好）、網址參數
    ui/cache.py     站點 / 站點圖 / 地址 / Google 時間的快取
    ui/planning.py  起終點解析、規劃、Google 驗證
    ui/map_view.py  地圖與點地圖設定起終點
    ui/results.py   摘要、逐步指引、行程表、冷卻提醒
    ui/progress.py  載入進度卡（抓站點、建圖、規劃時顯示跑到哪）
    ui/extras.py    Google 校正、AI 建議（選用）
"""
from __future__ import annotations

import os

import streamlit as st
from streamlit_folium import st_folium

from ui.cache import get_graph, graph_build_progress, graph_key
from ui.extras import render_ai_feedback, render_google_refinement
from ui.map_view import _build_map, _render_map_click_actions
from ui.planning import _plan_and_store
from ui.progress import LoadingCard, run_with_ticks
from ui.controls import (
    _init_state_from_query_params,
    _update_query_params,
    render_controls,
)
from ui.layout import KEY_LOADING, KEY_MAP, KEY_SEARCH, KEY_SHEET, KEY_SIDE, inject_css
from ui.results import (
    _render_endpoint_diagnostic,
    render_cooldown_advice,
    render_itinerary,
    render_steps,
    render_trip_summary,
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


def _render_sheet(result: dict | None, graph, stations) -> None:
    """結果卡：摘要永遠顯示；詳細內容（怎麼騎 / 明細 / 更多）可以收起來讓出地圖。"""
    if result is None:
        st.markdown(
            "輸入起點和終點（地址、地標都可以），再按 **規劃路線**。  \n"
            "💡 也可以直接在地圖上點一下，選「設為起點」或「設為終點」。"
        )
        st.caption(f"已載入 {len(stations)} 個站點 · 地圖資料 © OpenStreetMap contributors")
        return

    plan = result["plan"]
    render_trip_summary(result)
    # 地址被解析成哪裡：找不到路線時最需要看這個（常是地址被解析到別的地方）
    for line in result.get("matched", []):
        st.caption(line)
    if result.get("n_removed"):
        st.caption(
            f"🛰️ 已依 Google 實測自動避開 {result['n_removed']} 段可能超時的路段。"
        )
    if not plan.feasible:
        return

    st.session_state.setdefault("sheet_open", True)
    is_open = st.session_state["sheet_open"]
    st.button(
        "▾ 收起詳情，多看一點地圖" if is_open else "▴ 展開逐步指引",
        key="sheet_toggle", on_click=_toggle_sheet, width="stretch",
        type="secondary" if is_open else "primary",
    )
    if not is_open:
        return

    tab_steps, tab_detail, tab_more = st.tabs(["🧭 怎麼騎", "📋 明細", "✨ 更多"])
    with tab_steps:
        render_steps(result, heading=False)
    with tab_detail:
        _render_endpoint_diagnostic(result)
        render_itinerary(plan, result["fmbc"], result["id_to_city"])
        render_cooldown_advice(plan)
        st.caption(
            "🔗 網址已更新成這條路線，複製網址就能分享，或加入書籤下次直接用。"
        )
        st.caption(
            f"已載入 {len(stations)} 站、{graph.number_of_edges()} 條連線 · "
            f"規劃耗時 {result['elapsed_ms']:.0f} ms"
        )
    with tab_more:
        render_google_refinement(plan, result["stations"], result["fmbc"])
        render_ai_feedback(
            plan, result["stations"], result["fmbc"],
            result["id_to_city"], result["o_label"], result["d_label"],
            result["has_tpass"],
        )
    st.html('<div class="yb-credit">地圖資料 © OpenStreetMap contributors</div>')


def _load_graph(inp: dict, loading: LoadingCard):
    """取得站點圖（含站點資料），期間更新進度卡。

    站點圖還在快取裡時很快就回來，進度卡不會出現；沒有快取時進度卡會顯示
    「抓即時車況」→「建立連線（幾 %）」。
    """
    args = (
        inp["cities"],
        inp["has_tpass"],
        inp["safety_margin"],
        inp["allow_cross_circle"],
        inp["require_availability"],
        inp["use_cooldown"],
        inp["speed_kmh"],
    )
    key = graph_key(*args)
    note = "第一次建立會比較久（約 10~20 秒），之後會記住一陣子。"

    def tick(elapsed: float) -> None:
        state = graph_build_progress(key)
        if state is not None and state[0] == "build":
            loading.show(1, state[1], elapsed_sec=elapsed, note=note)
        else:
            loading.show(0, elapsed_sec=elapsed)

    return run_with_ticks(lambda: get_graph(*args), tick)


def _toggle_sheet() -> None:
    st.session_state["sheet_open"] = not st.session_state.get("sheet_open", True)


def main() -> None:
    _bridge_secrets_to_env()
    st.set_page_config(
        page_title="YouBike 免費騎乘規劃", page_icon="🚲", layout="wide",
        initial_sidebar_state="collapsed",
    )
    inject_css()
    _init_state_from_query_params()

    # 版面：地圖鋪滿畫面，左邊（手機是上下）浮著搜尋卡與結果卡。
    # 訊息（載入失敗、找不到地址…）都寫進搜尋卡，否則會被地圖蓋住看不到。
    side = st.container(key=KEY_SIDE)
    card = side.container(key=KEY_SEARCH)

    # 載入進度卡：抓站點、建圖、規劃時浮在地圖中間（ui/progress.py）
    load_slot = st.container(key=KEY_LOADING).empty()

    with card:
        inp = render_controls()
        if not inp["cities"]:
            st.info("請在「⚙ 偏好 → 進階設定」選擇至少一個城市。")
            return

        steps = ["抓取 YouBike 即時車況", "建立站點之間的連線"]
        if inp["submit"]:
            steps.append("找出免費換車路線")
        loading = LoadingCard(load_slot, steps)
        try:
            graph, stations = _load_graph(inp, loading)
            if stations.empty:
                st.error("無法載入任何站點資料，請稍後按「⚙ 偏好」裡的「重抓即時車況」再試一次。")
                return

            # 只有按下「規劃路線」才重算；結果存入 session_state，後續按 Google / AI 等按鈕
            # 觸發 rerun 時仍能保留（否則 submit 變 False，畫面會跳回預設訊息）。
            if inp["submit"]:
                planned = run_with_ticks(
                    lambda: _plan_and_store(inp, graph, stations),
                    lambda t: loading.show(2, elapsed_sec=t),
                )
                if planned:
                    _update_query_params(inp)
                    st.session_state["sheet_open"] = True
        finally:
            loading.clear()

    result = st.session_state.get("result")
    m = _build_map(inp, stations, result)
    with st.container(key=KEY_MAP):
        # height 只是初始值，實際高度由 ui/layout.py 的 CSS 撐滿整個視窗
        out = st_folium(
            m, width=None, height=600, key="main_map",
            returned_objects=["last_clicked"],
        )
    with card:
        _render_map_click_actions((out or {}).get("last_clicked"))

    with side.container(key=KEY_SHEET):
        _render_sheet(result, graph, stations)


if __name__ == "__main__":
    main()
