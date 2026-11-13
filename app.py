"""Streamlit 主程式：YouBike 最省錢騎乘攻略。

執行方式:
    streamlit run app.py
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import networkx as nx
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from config import (
    DEFAULT_CYCLING_SPEED_KMH,
    ROUTE_DETOUR_FACTOR,
    SAFETY_MARGIN_MINUTES,
    active_cities,
    free_minutes_by_city,
)
from config import settings
from core.cooldown import build_cooldown_graph, plan_cooldown_route
from core.directions import (
    build_steps,
    estimate_arrival,
    format_latlon,
    parse_latlon,
    total_trip_minutes,
)
from core.feedback import collect_facts, generate_feedback
from core.geocoder import GeocodeError, geocode_address_verbose
from core.gmaps import GMapsError, get_travel_time
from core.time_estimator import haversine_km
from core.graph_builder import build_station_graph
from core.route_optimizer import (
    MAX_WALK_KM,
    SAME_STATION_COOLDOWN_MIN,
    RoutePlan,
    plan_route,
)
from data.fetcher import FetchError, fetch_city_stations
from data.preprocessor import (
    filter_invalid_stations,
    merge_cities,
    normalize_stations,
)
from visualization.map_renderer import (
    MAP_STYLES,
    draw_route,
    render_base_map,
    stations_near_route,
    view_for_route,
)


# 目前聚焦北北桃生活圈（其餘縣市暫不在主流程支援）。
SUPPORTED_CITIES = active_cities()

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


# ---------- 資料載入（含 Streamlit 快取） ----------

@st.cache_data(ttl=86400, show_spinner=False)
def geocode_cached(address: str) -> tuple[float, float, str]:
    """以地址查經緯度與匹配地名，結果快取一天（減少 Nominatim 請求）。"""
    return geocode_address_verbose(address)


@st.cache_data(ttl=3600, show_spinner=False)
def gmaps_travel_cached(
    o_lat: float, o_lon: float, d_lat: float, d_lon: float
) -> tuple[float, float, str]:
    """以 Google Maps 取單段騎乘時間，結果快取一小時。回傳 (分鐘, 公里, 模式)。"""
    tt = get_travel_time(o_lat, o_lon, d_lat, d_lon, settings.google_maps_api_key())
    return tt.minutes, tt.distance_km, tt.mode


@st.cache_data(ttl=300, show_spinner="抓取 YouBike 站點資料中…")
def load_stations(cities: tuple[str, ...]) -> pd.DataFrame:
    dfs = []
    failures = []
    for city in cities:
        try:
            raw = fetch_city_stations(city)
            dfs.append(normalize_stations(raw, city))
        except (FetchError, ValueError) as e:
            failures.append((city, str(e)))
    if failures:
        for _c, msg in failures:
            # msg 本身已含縣市與原因，避免重複前綴
            st.warning(f"⚠️ {msg}")
        if dfs:
            st.caption("（仍可規劃已成功載入城市的路線；上述城市的站點本次未納入。）")
    if not dfs:
        return pd.DataFrame()
    return filter_invalid_stations(merge_cities(dfs))


# 每張圖可能有上百萬條邊，最多只留幾張在記憶體，避免使用者多次調整設定後記憶體爆掉。
@st.cache_resource(show_spinner="建構站點圖中…", max_entries=4)
def get_graph(
    cities: tuple[str, ...],
    has_tpass: bool,
    safety_margin: int,
    allow_cross_circle: bool,
    require_availability: bool,
    use_cooldown: bool,
    speed_kmh: float = DEFAULT_CYCLING_SPEED_KMH,
) -> tuple[nx.DiGraph, pd.DataFrame]:
    df = load_stations(cities)
    if df.empty:
        return nx.DiGraph(), df
    fmbc = free_minutes_by_city(list(cities), has_tpass)
    common = dict(
        free_minutes=max(fmbc.values()) if fmbc else 30,
        safety_margin=safety_margin,
        speed_kmh=speed_kmh,
        detour_factor=ROUTE_DETOUR_FACTOR,
        allow_cross_circle=allow_cross_circle,
        require_availability=require_availability,
        free_minutes_by_city=fmbc,
    )
    if use_cooldown:
        g = build_cooldown_graph(df, **common)
    else:
        g = build_station_graph(df, **common)
    return g, df


# ---------- UI ----------

def _clear_caches_and_rerun() -> None:
    """清除站點 / 圖快取與規劃結果，重抓最新即時資料後重跑。"""
    st.cache_data.clear()
    st.cache_resource.clear()
    for k in ("result", "google_result", "google_times", "ai_result"):
        st.session_state.pop(k, None)
    st.rerun()


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


def _set_endpoint_from_click(which: str, lat: float, lon: float) -> None:
    st.session_state["o_text" if which == "origin" else "d_text"] = format_latlon(lat, lon)
    st.session_state["_handled_click"] = (lat, lon)


def render_sidebar() -> dict:
    st.sidebar.header("🚲 要去哪裡？")
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
        help="目前北北桃的免費上限與 TPASS 無關：台北/新北 30 分、桃園 60 分。保留此選項供未來其他縣市使用",
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
        cities = st.multiselect(
            "資料來源城市", SUPPORTED_CITIES, default=SUPPORTED_CITIES,
            help="目前聚焦北北桃生活圈，可規劃台北↔新北↔桃園的跨縣市路線",
        )
        if cities:
            fmbc = free_minutes_by_city(list(cities), has_tpass)
            st.caption(
                "各市免費上限：" + "、".join(f"{c} {m} 分" for c, m in fmbc.items())
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
            "允許跨生活圈（會收 600~1135 元調度費）",
            value=False,
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


def render_summary(plan: RoutePlan, depart: datetime | None = None) -> None:
    if not plan.feasible:
        st.error(f"❌ {plan.message}")
        return
    if plan.message:
        st.warning(plan.message)

    total = total_trip_minutes(plan)
    c1, c2 = st.columns(2)
    c1.metric("全程約", f"{total:.0f} 分")
    c2.metric("換車", f"{plan.swap_count} 次")
    c3, c4 = st.columns(2)
    if depart is not None:
        c3.metric("現在出發，約", estimate_arrival(depart, plan).strftime("%H:%M") + " 到")
    fee_risk = "調度費" in (plan.message or "")
    c4.metric("車資", "可能收調度費" if fee_risk else "0 元")
    detail = (
        f"騎車 {plan.total_minutes:.0f} 分 · 步行 "
        f"{plan.walk_to_start_min + plan.walk_from_end_min:.0f} 分"
    )
    if plan.transfer_minutes > 0:
        detail += f" · 換車（步行 / 等冷卻）{plan.transfer_minutes:.0f} 分"
    st.caption(detail)


def render_steps(result: dict) -> None:
    """逐步指引：白話說明每一步，並附 Google 地圖導航連結。"""
    plan: RoutePlan = result["plan"]
    stations: pd.DataFrame = result["stations"]
    coords = {
        sid: (float(lat), float(lon))
        for sid, lat, lon in zip(stations["station_id"], stations["lat"], stations["lon"])
    }
    fmbc = result["fmbc"]
    default_limit = max(fmbc.values()) if fmbc else 30
    limit_for = {
        sid: fmbc.get(city, default_limit) for sid, city in result["id_to_city"].items()
    }
    availability = {
        sid: (int(b or 0), int(d or 0))
        for sid, b, d in zip(
            stations["station_id"], stations["available_bikes"], stations["available_docks"]
        )
    }
    steps = build_steps(
        plan, coords, result["origin"], result["destination"],
        free_limit_for=limit_for, availability=availability,
        tight_margin_min=result.get("safety_margin", SAFETY_MARGIN_MINUTES) + 1,
    )
    if not steps:
        return
    st.subheader("🧭 怎麼騎")
    for i, step in enumerate(steps, 1):
        line = f"**{i}. {step.icon} {step.text}**"
        if step.minutes >= 0.5:
            line += f"　約 {step.minutes:.0f} 分"
        if step.nav_url:
            line += f"　[導航]({step.nav_url})"
        st.markdown(line)
        if step.note:
            st.caption(("⚠️ " if step.warn else "") + step.note)


def render_itinerary(
    plan: RoutePlan,
    fmbc: dict[str, int],
    id_to_city: dict[str, str],
) -> None:
    if not plan.feasible or not plan.segments:
        return
    df = itinerary_dataframe(plan, fmbc, id_to_city)
    st.dataframe(df, hide_index=True, width="stretch")


_MODE_LABEL = {"ride": "🚲 騎乘", "walk": "🚶 步行換車", "wait": "⏱️ 等冷卻"}


def itinerary_dataframe(
    plan: RoutePlan, fmbc: dict[str, int], id_to_city: dict[str, str]
) -> pd.DataFrame:
    """建構詳細行程 DataFrame。

    可能含 ride / walk / wait 混合段；「免費上限 / 免費餘裕」欄對轉乘段為 "—"，
    故整欄統一以字串呈現，避免 pandas→Arrow 混型（int 與 str）序列化失敗。
    """
    rows = []
    for i, seg in enumerate(plan.segments, 1):
        if seg.mode != "ride":
            rows.append({
                "段": i, "類型": _MODE_LABEL.get(seg.mode, seg.mode),
                "起站": seg.from_name, "終站": seg.to_name, "起站縣市": "",
                "免費上限 (分)": "—", "時間 (分)": round(seg.minutes, 1),
                "距離 (km)": round(seg.distance_km, 2), "免費餘裕 (分)": "—",
            })
            continue
        # 免費規則以借車地（起站縣市）為準，逐段可能不同（如桃園 vs 台北）
        city = id_to_city.get(seg.from_station_id, "")
        limit = fmbc.get(city, max(fmbc.values()) if fmbc else 30)
        margin = limit - seg.minutes
        rows.append({
            "段": i, "類型": _MODE_LABEL["ride"],
            "起站": seg.from_name, "終站": seg.to_name, "起站縣市": city,
            "免費上限 (分)": str(limit), "時間 (分)": round(seg.minutes, 1),
            "距離 (km)": round(seg.distance_km, 2), "免費餘裕 (分)": f"{margin:.1f}",
        })
    return pd.DataFrame(rows)


def render_cooldown_advice(plan: RoutePlan) -> None:
    """顯示同站續借冷卻提醒與鄰近改借建議。"""
    if not plan.swap_advice:
        return
    lo, hi = SAME_STATION_COOLDOWN_MIN
    with st.expander("🔁 每個換車點附近可改借的站（避開同站冷卻）"):
        st.caption(
            f"在換車點還車後，於同一站再借同型車可能需等 {lo}~{hi} 分鐘冷卻。"
            "建議改到鄰近站借車，或改用雙卡輪替。"
        )
        for i, adv in enumerate(plan.swap_advice, 1):
            st.markdown(f"**換車點 #{i}：{adv.station_name}**")
            if not adv.alternatives:
                st.caption("附近 300m 內查無其他有車可借的站，建議於原站等候冷卻時間。")
                continue
            alt_rows = [
                {
                    "改借站": name,
                    "步行 (分)": round(walk_min, 1),
                    "距離 (km)": round(dist, 2),
                    "可借車": bikes,
                }
                for name, walk_min, dist, bikes in adv.alternatives
            ]
            st.dataframe(
                pd.DataFrame(alt_rows), hide_index=True, width="stretch"
            )


def _render_endpoint_diagnostic(result: dict) -> None:
    """顯示實際選用的起/終借還站，以及它們距「你設定的起終點」多遠。

    若距離偏大，通常代表起終點座標/地址解析有誤（例如想去淡水卻解析到市中心），
    讓使用者一眼看出路線為何怪異。
    """
    plan = result["plan"]
    if not plan.feasible or not plan.segments:
        return
    coords = result["stations"].set_index("station_id")[["lat", "lon"]].to_dict("index")
    s_id = plan.segments[0].from_station_id
    e_id = plan.segments[-1].to_station_id
    s, e = coords.get(s_id), coords.get(e_id)
    if not s or not e:
        return
    o_lat, o_lon = result["origin"]
    d_lat, d_lon = result["destination"]
    d_start = haversine_km(o_lat, o_lon, s["lat"], s["lon"])
    d_end = haversine_km(d_lat, d_lon, e["lat"], e["lon"])
    st.caption(f"🚲 起點借車站：{plan.segments[0].from_name}（距你設定的起點 {d_start:.2f} km）")
    st.caption(f"🏁 終點還車站：{plan.segments[-1].to_name}（距你設定的終點 {d_end:.2f} km）")


def _route_signature(plan: RoutePlan) -> tuple:
    """以各段起終站 id 組出路線指紋，用來判斷快取的 Google 結果是否仍對應此路線。"""
    return tuple((s.from_station_id, s.to_station_id) for s in plan.segments)


def _ride_edge_key(from_id: str, to_id: str, use_cooldown: bool):
    """回傳該騎乘段在圖中對應的邊鍵（一般圖 vs node-split 冷卻圖節點型態不同）。"""
    if use_cooldown:
        return ((from_id, "out"), (to_id, "in"))
    return (from_id, to_id)


def _plan_on(
    graph, stations, origin, destination, strategy, use_cooldown,
    max_walk_km: float = MAX_WALK_KM,
) -> RoutePlan:
    planner = plan_cooldown_route if use_cooldown else plan_route
    return planner(
        graph, stations, origin, destination,
        strategy=strategy, max_walk_km=max_walk_km,
    )


def plan_with_google_validation(
    graph: nx.DiGraph, stations: pd.DataFrame,
    origin: tuple[float, float], destination: tuple[float, float],
    strategy: str, fmbc: dict[str, int], use_cooldown: bool, max_iter: int = 4,
    max_walk_km: float = MAX_WALK_KM,
):
    """規劃路線，並以 Google 實測時間驗證：若某段實際 > 該段免費上限，移除該邊後重新
    規劃，直到各段都通過或無法再避開。

    回傳 (plan, google_rows, any_over, google_times, removed_count)。
    """
    removed: list = []
    default_limit = max(fmbc.values()) if fmbc else 30
    plan = _plan_on(
        graph, stations, origin, destination, strategy, use_cooldown, max_walk_km
    )
    rows, any_over, gtimes = [], False, {}
    for it in range(max_iter + 1):
        if not plan.feasible:
            break
        # rows / gtimes 始終對應「目前的 plan」
        rows, any_over, gtimes = _compute_google_refinement(plan, stations, fmbc)
        over_edges = []
        for i, seg in enumerate(plan.segments, 1):
            if seg.mode != "ride" or i not in gtimes:
                continue
            limit = fmbc.get(_seg_city(seg, stations), default_limit)
            if gtimes[i][0] > limit:  # Google 實測 > 免費上限 → 該段要避開
                over_edges.append(
                    _ride_edge_key(seg.from_station_id, seg.to_station_id, use_cooldown)
                )
        if not over_edges or it == max_iter:
            break  # 全部通過，或已用完重試次數（rows 對應目前 plan）
        removed.extend(over_edges)
        view = nx.restricted_view(graph, [], removed)
        plan = _plan_on(
            view, stations, origin, destination, strategy, use_cooldown, max_walk_km
        )

    if plan.feasible and any_over and removed:
        plan.message = (
            ((plan.message + "　") if plan.message else "")
            + "（已盡量避開 Google 實測超時的路段，但仍有路段超時，可能無完全免費的替代路線）"
        )
    return plan, rows, any_over, gtimes, len(removed)


def _seg_city(seg, stations: pd.DataFrame) -> str:
    row = stations.loc[stations["station_id"] == seg.from_station_id, "city"]
    return row.iloc[0] if len(row) else ""


def _compute_google_refinement(
    plan: RoutePlan, stations: pd.DataFrame, fmbc: dict[str, int]
) -> tuple[list[dict], bool, dict[int, tuple[float, str]]]:
    """逐段（只 ride 段）以 Google Maps 校正時間。回傳 (表格列, 是否有超時, google_times)。"""
    coords = stations.set_index("station_id")[["lat", "lon", "city"]].to_dict("index")
    rows: list[dict] = []
    any_over = False
    google_times: dict[int, tuple[float, str]] = {}
    for i, seg in enumerate(plan.segments, 1):
        if seg.mode != "ride":
            continue  # 步行 / 等冷卻段不需 Google 校正
        a = coords[seg.from_station_id]
        b = coords[seg.to_station_id]
        limit = fmbc.get(a["city"], max(fmbc.values()) if fmbc else 30)
        try:
            g_min, g_km, mode = gmaps_travel_cached(
                a["lat"], a["lon"], b["lat"], b["lon"]
            )
            over = g_min > limit
            any_over = any_over or over
            google_times[i] = (g_min, mode)
            rows.append({
                "段": i, "起站": seg.from_name, "終站": seg.to_name,
                "估算 (分)": round(seg.minutes, 1),
                # 字串呈現，與失敗列的 "—" 同型，避免 Arrow 混型序列化失敗
                "Google (分)": f"{g_min:.1f}",
                "Google 模式": mode,
                "免費上限 (分)": limit,
                "超時": "⚠️" if over else "",
            })
        except GMapsError as e:
            rows.append({
                "段": i, "起站": seg.from_name, "終站": seg.to_name,
                "估算 (分)": round(seg.minutes, 1),
                "Google (分)": "—", "Google 模式": f"失敗：{e}",
                "免費上限 (分)": limit, "超時": "",
            })
    return rows, any_over, google_times


def _store_google_result(plan: RoutePlan, rows, any_over, google_times) -> None:
    sig = _route_signature(plan)
    st.session_state["google_result"] = {"sig": sig, "rows": rows, "any_over": any_over}
    st.session_state["google_times"] = {"sig": sig, "data": google_times}


def render_google_refinement(
    plan: RoutePlan, stations: pd.DataFrame, fmbc: dict[str, int]
) -> None:
    """顯示 Google 校正結果；規劃時已自動跑過，這裡提供重新查詢按鈕。"""
    if not settings.google_maps_api_key():
        st.info("（選用）在 .env 設定 GOOGLE_MAPS_API_KEY 後，騎乘時間會自動以 Google 校正。")
        return

    sig = _route_signature(plan)
    if st.button("🛰️ 重新用 Google Maps 校正騎乘時間"):
        with st.spinner("查詢 Google Maps 中…"):
            rows, any_over, gt = _compute_google_refinement(plan, stations, fmbc)
        _store_google_result(plan, rows, any_over, gt)

    gr = st.session_state.get("google_result")
    if gr and gr["sig"] == sig:
        st.caption("🛰️ Google Maps 校正後的各段實際時間：")
        st.dataframe(pd.DataFrame(gr["rows"]), hide_index=True, width="stretch")
        if gr["any_over"]:
            st.warning("⚠️ 依 Google 校正，部分路段實際時間超過免費上限，該段可能產生費用。")
        else:
            st.success("✅ 依 Google 校正，各路段仍在免費上限內。")


def _gemini_error_hint(error: str) -> str:
    """依 Gemini 錯誤訊息給對應的排解提示。"""
    e = error.lower()
    if "429" in error or "quota" in e or "resource_exhausted" in e or "rate" in e:
        return (
            "這是 Gemini 配額／速率限制（與本程式無關）。free tier 顯示 limit: 0 通常代表"
            "此金鑰所屬專案沒有免費額度。可：①到 AI Studio 用「新建專案」重新產生金鑰以取得"
            "免費額度、②或為該專案啟用計費改用付費額度、③或稍等顯示的秒數後重試。"
            "AI 建議為選用功能，下方本地摘要一樣完整可用。"
        )
    if "api key not valid" in e or "api_key_invalid" in e or "401" in error:
        return "金鑰無效，請確認 .env 的 GEMINI_API_KEY 沒有多餘空格或引號，並已重啟 streamlit。"
    if "not found" in e or "404" in error:
        return "模型名稱可能有誤，請在 .env 改用 gemini-2.5-flash。"
    if "has not been used" in e or "service_disabled" in e or "403" in error:
        return "請到 Google Cloud Console 為該專案啟用 Generative Language API，或改用 AI Studio 金鑰。"
    return "常見原因：金鑰無效、模型名稱有誤，或未啟用 Generative Language API。"


def render_ai_feedback(
    plan: RoutePlan,
    stations: pd.DataFrame,
    fmbc: dict[str, int],
    id_to_city: dict[str, str],
    origin_label: str,
    destination_label: str,
    has_tpass: bool,
) -> None:
    """產生 AI 行程建議：客觀事實由程式計算，Gemini 僅負責語氣潤飾。"""
    st.subheader("✨ AI 行程建議")
    availability = {
        sid: (int(b or 0), int(d or 0))
        for sid, b, d in zip(
            stations["station_id"],
            stations["available_bikes"],
            stations["available_docks"],
        )
    }
    # 若使用者已跑過 Google 校正且對應同一條路線，將結果併入 AI 事實
    google_times = None
    cached = st.session_state.get("google_times")
    if cached and cached.get("sig") == _route_signature(plan):
        google_times = cached["data"]

    facts = collect_facts(
        plan, fmbc, id_to_city,
        origin_label=origin_label, destination_label=destination_label,
        has_tpass=has_tpass, availability=availability, google_times=google_times,
    )
    api_key = settings.gemini_api_key()
    if not api_key:
        st.caption("（選用）在 .env 設定 GEMINI_API_KEY 後，AI 會把以下事實潤飾成親切建議。")
    if google_times:
        st.caption("已納入 Google 校正時間作為事實來源。")

    sig = _route_signature(plan)
    if st.button("產生建議", key="ai_feedback_btn"):
        with st.spinner("整理建議中…"):
            text, source, error = generate_feedback(
                facts, api_key, settings.gemini_model()
            )
        st.session_state["ai_result"] = {
            "sig": sig, "text": text, "source": source, "error": error,
        }

    # 顯示（含先前已產生的建議，只要仍對應同一路線）
    ar = st.session_state.get("ai_result")
    if ar and ar["sig"] == sig:
        if ar["source"] == "gemini":
            st.markdown(ar["text"])
            st.caption(f"由 Gemini（{settings.gemini_model()}）依客觀事實潤飾生成。")
        else:
            if api_key and ar.get("error"):
                st.warning(
                    f"Gemini 呼叫失敗，已改顯示下方本地事實摘要。\n\n"
                    f"原因：{ar['error']}\n\n{_gemini_error_hint(ar['error'])}"
                )
            st.text(ar["text"])


# ---------- 主流程 ----------

def _resolve_point(text: str, role: str) -> tuple[float, float, str | None] | None:
    """把起點 / 終點文字轉成座標。

    「緯度, 經度」直接使用；其餘當地址查詢。回傳 (lat, lon, 匹配地名或 None)，
    失敗時顯示錯誤並回傳 None。
    """
    if not text:
        st.error(f"❌ 請輸入{role}")
        return None
    ll = parse_latlon(text)
    if ll is not None:
        return ll[0], ll[1], None
    try:
        lat, lon, name = geocode_cached(text)
    except GeocodeError as e:
        st.error(f"❌ {role}「{text}」找不到：{e}")
        return None
    return lat, lon, name


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


def _plan_and_store(inp: dict, graph: nx.DiGraph, stations: pd.DataFrame) -> None:
    """解析起終點、規劃路線，結果存入 session_state。"""
    o = _resolve_point(inp["origin_text"], "起點")
    d = _resolve_point(inp["destination_text"], "終點")
    if o is None or d is None:
        return
    origin, destination = (o[0], o[1]), (d[0], d[1])
    matched = []
    if o[2]:
        matched.append(f"📍 起點「{inp['origin_text']}」→ {o[2]}")
    if d[2]:
        matched.append(f"🏁 終點「{inp['destination_text']}」→ {d[2]}")

    t0 = time.time()
    fmbc_now = free_minutes_by_city(list(inp["cities"]), inp["has_tpass"])
    n_removed = 0
    if settings.google_maps_api_key():
        # 以 Google 實測驗證，超出免費上限的路段自動避開重規劃
        with st.spinner("規劃並以 Google Maps 驗證各段時間（自動避開超時路段）…"):
            plan, g_rows, g_over, g_times, n_removed = plan_with_google_validation(
                graph, stations, origin, destination,
                inp["strategy"], fmbc_now, inp["use_cooldown"],
                max_walk_km=inp["max_walk_km"],
            )
        if plan.feasible:
            _store_google_result(plan, g_rows, g_over, g_times)
    else:
        plan = _plan_on(
            graph, stations, origin, destination,
            inp["strategy"], inp["use_cooldown"], inp["max_walk_km"],
        )
    elapsed_ms = (time.time() - t0) * 1000
    st.session_state["result"] = {
        "plan": plan,
        "origin": origin, "destination": destination,
        "o_label": inp["origin_text"] or "起點",
        "d_label": inp["destination_text"] or "終點",
        "matched": matched,
        "fmbc": fmbc_now,
        "id_to_city": dict(zip(stations["station_id"], stations["city"])),
        "stations": stations,
        "has_tpass": inp["has_tpass"],
        "safety_margin": inp["safety_margin"],
        "elapsed_ms": elapsed_ms,
        "n_removed": n_removed,
        "depart": datetime.now(ZoneInfo("Asia/Taipei")),
    }
    _update_query_params(inp)


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
    if inp["submit"]:
        _plan_and_store(inp, graph, stations)

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
