"""側欄：起終點、騎乘偏好、進階設定，以及網址參數（分享 / 書籤）。"""
from __future__ import annotations

import streamlit as st

from config import (
    ACTIVE_CIRCLE,
    CITY_CONFIG,
    DEFAULT_CYCLING_SPEED_KMH,
    REGIONS,
    SAFETY_MARGIN_MINUTES,
    free_minutes_by_city,
    supported_cities,
)
from core.route_optimizer import MAX_WALK_KM
from ui.cache import RESULT_KEYS, _clear_caches_and_rerun
from visualization.map_renderer import MAP_STYLES

# 可選區域：只列出至少有一個縣市設定好資料來源的區域。
SUPPORTED_REGIONS = [r for r in REGIONS if supported_cities(r)]

# 各區域的範例起終點（地址），切換區域時自動帶入，避免起終點落在別的縣市。
REGION_EXAMPLES: dict[str, tuple[str, str]] = {
    "北北桃": ("台北車站", "淡水捷運站"),
    "竹竹苗": ("新竹火車站", "竹北火車站"),
    "台中": ("台中火車站", "逢甲大學"),
    "嘉南高屏": ("高雄車站", "駁二藝術特區"),
    "台東": ("台東轉運站", "台東森林公園"),
}


# 騎乘速度選項：介面文字 → 平均時速（km/h）
SPEED_PRESETS: dict[str, float] = {
    "悠閒（10 km/h）": 10.0,
    "一般（12 km/h）": DEFAULT_CYCLING_SPEED_KMH,
    "輕快（15 km/h）": 15.0,
    "運動（18 km/h）": 18.0,
}
_DEFAULT_SPEED_LABEL = next(
    k for k, v in SPEED_PRESETS.items() if v == DEFAULT_CYCLING_SPEED_KMH
)
_STRATEGY_LABEL = {"fewest_swaps": "最少換車", "shortest_time": "最短時間"}
_STATION_DISPLAY = ["路線附近的站", "全部站點", "不顯示站點"]
_DEFAULT_ORIGIN = "台北車站"
_DEFAULT_DESTINATION = "淡水捷運站"


def _init_state_from_query_params() -> None:
    """第一次開頁時，用網址參數（分享 / 書籤連結）帶入起終點與偏好。"""
    if st.session_state.get("_qp_loaded"):
        return
    st.session_state["_qp_loaded"] = True
    qp = st.query_params
    if qp.get("region") in SUPPORTED_REGIONS:
        st.session_state.setdefault("region", qp["region"])
    st.session_state.setdefault("o_text", qp.get("from", _DEFAULT_ORIGIN))
    st.session_state.setdefault("d_text", qp.get("to", _DEFAULT_DESTINATION))
    if qp.get("strategy") in _STRATEGY_LABEL:
        st.session_state.setdefault("strategy", qp["strategy"])
    if qp.get("tpass") in ("0", "1"):
        st.session_state.setdefault("tpass", qp["tpass"] == "1")
    speed = qp.get("speed")
    for label, v in SPEED_PRESETS.items():
        if speed and speed == f"{v:g}":
            st.session_state.setdefault("speed", label)
    st.session_state.setdefault("speed", _DEFAULT_SPEED_LABEL)
    # 連結同時帶有起終點 → 開頁後自動規劃一次
    st.session_state["_auto_plan"] = bool(qp.get("from") and qp.get("to"))


def _update_query_params(inp: dict) -> None:
    """把目前的起終點與偏好寫進網址，複製網址即可分享或加入書籤。"""
    st.query_params.from_dict({
        "region": inp["region"],
        "from": inp["origin_text"],
        "to": inp["destination_text"],
        "strategy": inp["strategy"],
        "tpass": "1" if inp["has_tpass"] else "0",
        "speed": f"{inp['speed_kmh']:g}",
    })


def _swap_endpoints() -> None:
    st.session_state["o_text"], st.session_state["d_text"] = (
        st.session_state.get("d_text", ""), st.session_state.get("o_text", ""),
    )


def _on_region_change() -> None:
    """切換區域時，把起終點換成該區域的範例地點，並清掉舊區域的規劃結果。"""
    region = st.session_state.get("region")
    if region in REGION_EXAMPLES:
        st.session_state["o_text"], st.session_state["d_text"] = REGION_EXAMPLES[region]
    for k in RESULT_KEYS:
        st.session_state.pop(k, None)


def render_sidebar() -> dict:
    st.sidebar.header("🚲 要去哪裡？")
    st.session_state.setdefault(
        "region", ACTIVE_CIRCLE if ACTIVE_CIRCLE in SUPPORTED_REGIONS else SUPPORTED_REGIONS[0]
    )
    region = st.sidebar.selectbox(
        "區域", SUPPORTED_REGIONS, key="region", on_change=_on_region_change,
        help="一次只載入一個區域的站點，避免把全台站點建成一張圖而吃光記憶體",
    )
    st.sidebar.text_input(
        "起點", key="o_text",
        help="可輸入地址、地標（如「台北車站」），或「緯度, 經度」；"
             "也可以直接在地圖上點一下設定",
    )
    st.sidebar.button("⇅ 起終點對調", on_click=_swap_endpoints)
    st.sidebar.text_input("終點", key="d_text")
    submit = st.sidebar.button("規劃路線", type="primary", width="stretch")

    st.sidebar.divider()
    st.sidebar.subheader("我的騎乘偏好")
    has_tpass = st.sidebar.toggle(
        "持有 TPASS 月票", key="tpass",
        help="影響部分縣市的免費上限：台南、高雄要有 TPASS 才有前 30 分鐘免費；"
             "北北桃與 TPASS 無關（台北/新北 30 分、桃園 60 分）",
    )
    strategy = st.sidebar.radio(
        "路線偏好",
        options=list(_STRATEGY_LABEL),
        format_func=_STRATEGY_LABEL.get,
        key="strategy",
        horizontal=True,
        help="最少換車：換車次數最少；最短時間：騎乘總時間最短（可能多換幾次車）",
    )
    speed_label = st.sidebar.select_slider(
        "我的騎乘速度", options=list(SPEED_PRESETS), key="speed",
        help="騎得越快，每段能騎越遠、換車越少；騎得慢就選悠閒，避免估算太樂觀而超時",
    )
    safety_margin = st.sidebar.slider(
        "提早還車的緩衝（分鐘）", min_value=0, max_value=10,
        value=SAFETY_MARGIN_MINUTES,
        help="每段在免費上限前提早幾分鐘還車，避免紅燈或繞路害你超時",
    )

    with st.sidebar.expander("⚙️ 進階設定"):
        region_cities = supported_cities(region)
        cities = st.multiselect(
            f"資料來源縣市（{region}）", region_cities, default=region_cities,
            key=f"cities_{region}",
            help="同區域內大多可跨縣市借還；需付跨區調度費的組合依下方「允許跨區還車」處理",
        )
        max_walk_km = st.slider(
            "起終點最多走多遠去找站（公里）", min_value=0.3, max_value=2.0,
            value=float(MAX_WALK_KM), step=0.1,
        )
        require_availability = st.checkbox(
            "只走有車可借 / 有位可還的站",
            value=True,
            help="依即時車輛數過濾：借不到車的站不當起點、還不了車的站不當終點。"
                 "電輔車（2.0E）不適用免費時段，所以新北的站只計算一般車；"
                 "台北的開放資料沒有分開電輔車數量，顯示的可借數可能含電輔車。",
        )
        use_cooldown = st.checkbox(
            "換車時避開同站冷卻（步行到鄰站借車）",
            value=False,
            help="同站還車後要等 10~15 分鐘才能再借。開啟後，路線會把"
                 "「走到隔壁站借」或「原地等」的時間一起算進去",
        )
        allow_cross_circle = st.checkbox(
            "允許跨區還車（會收 600~1135 元調度費）",
            value=False,
        )

    if cities:
        fmbc = free_minutes_by_city(list(cities), has_tpass)
        st.sidebar.caption(
            "各市免費上限：" + "、".join(f"{c} {m} 分" for c, m in fmbc.items())
        )
        no_free = [c for c, m in fmbc.items() if m <= 0]
        if no_free:
            st.sidebar.warning(
                "、".join(no_free) + " 目前沒有免費時段"
                + ("（開啟 TPASS 可能適用）" if not has_tpass else "")
                + "，這些縣市的站點不會被當成借車點。"
            )
        unverified = [c for c in cities if not CITY_CONFIG[c].get("verified", True)]
        if unverified:
            st.sidebar.caption(
                "ℹ️ " + "、".join(unverified)
                + " 的免費規則尚未對照官網確認，請以 YouBike 官網公告為準。"
            )

    with st.sidebar.expander("🗺️ 地圖顯示"):
        map_style = st.selectbox("底圖樣式", list(MAP_STYLES), key="map_style")
        station_display = st.radio(
            "規劃後要顯示哪些站", _STATION_DISPLAY, key="station_display",
            help="只顯示路線附近的站，地圖比較清爽，手機上也比較順",
        )

    st.sidebar.divider()
    if st.sidebar.button("🔄 重抓即時車況", help="清除快取、重抓最新即時車況並重建圖；"
                         "若出現「找不到路線」常是快取的車況過舊所致"):
        _clear_caches_and_rerun()

    if st.session_state.pop("_auto_plan", False):
        submit = True

    return {
        "region": region,
        "cities": tuple(cities),
        "has_tpass": has_tpass,
        "safety_margin": int(safety_margin),
        "strategy": strategy,
        "speed_kmh": SPEED_PRESETS[speed_label],
        "max_walk_km": float(max_walk_km),
        "allow_cross_circle": allow_cross_circle,
        "require_availability": require_availability,
        "use_cooldown": use_cooldown,
        "origin_text": st.session_state.get("o_text", "").strip(),
        "destination_text": st.session_state.get("d_text", "").strip(),
        "map_tiles": MAP_STYLES[map_style],
        "station_display": station_display,
        "submit": submit,
    }
