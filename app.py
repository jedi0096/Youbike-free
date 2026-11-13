"""Streamlit 主程式：YouBike 最省錢騎乘攻略。

執行方式:
    streamlit run app.py
"""
from __future__ import annotations

import os
import time

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
from core.feedback import collect_facts, generate_feedback
from core.geocoder import GeocodeError, geocode_address_verbose
from core.gmaps import GMapsError, get_travel_time
from core.time_estimator import haversine_km
from core.graph_builder import build_station_graph
from core.route_optimizer import (
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
from visualization.map_renderer import draw_route, render_base_map


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


@st.cache_resource(show_spinner="建構站點圖中…")
def get_graph(
    cities: tuple[str, ...],
    has_tpass: bool,
    safety_margin: int,
    allow_cross_circle: bool,
    require_availability: bool,
    use_cooldown: bool,
) -> tuple[nx.DiGraph, pd.DataFrame]:
    df = load_stations(cities)
    if df.empty:
        return nx.DiGraph(), df
    fmbc = free_minutes_by_city(list(cities), has_tpass)
    common = dict(
        free_minutes=max(fmbc.values()) if fmbc else 30,
        safety_margin=safety_margin,
        speed_kmh=DEFAULT_CYCLING_SPEED_KMH,
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


def render_sidebar() -> dict:
    st.sidebar.header("路線設定")
    if st.sidebar.button("🔄 清除快取並重新整理", help="重抓最新即時車況並重建圖；"
                         "若出現「找不到路線」常是快取的車況過舊所致"):
        _clear_caches_and_rerun()
    cities = st.sidebar.multiselect(
        "資料來源城市（北北桃）", SUPPORTED_CITIES, default=SUPPORTED_CITIES,
        help="目前聚焦北北桃生活圈，可規劃台北↔新北↔桃園的跨縣市路線",
    )
    has_tpass = st.sidebar.toggle(
        "持有 TPASS 月票",
        value=False,
        help="目前北北桃的免費上限與 TPASS 無關：台北/新北 30 分、桃園 60 分。保留此選項供未來其他縣市使用",
    )
    if cities:
        fmbc = free_minutes_by_city(list(cities), has_tpass)
        st.sidebar.caption(
            "各市免費上限：" + "、".join(f"{c} {m} 分" for c, m in fmbc.items())
        )
    safety_margin = st.sidebar.number_input(
        "安全餘裕（分鐘）", min_value=0, max_value=10,
        value=SAFETY_MARGIN_MINUTES,
        help="建議提前幾分鐘還車，避免估算誤差超時",
    )
    strategy = st.sidebar.radio(
        "路線策略",
        options=["fewest_swaps", "shortest_time"],
        format_func=lambda s: "最少換車" if s == "fewest_swaps" else "最短總時間",
    )
    allow_cross_circle = st.sidebar.checkbox(
        "允許跨生活圈（會收 600~1135 元調度費）",
        value=False,
    )
    require_availability = st.sidebar.checkbox(
        "只走有車可借 / 有位可還的站",
        value=True,
        help="依即時車輛數過濾：借不到車的站不當起點、還不了車的站不當終點。"
             "電輔車（2.0E）不適用免費時段，所以新北的站只計算一般車；"
             "台北的開放資料沒有分開電輔車數量，顯示的可借數可能含電輔車。",
    )
    use_cooldown = st.sidebar.checkbox(
        "啟用步行換車（同站續借冷卻模型）",
        value=False,
        help="進階：換車時改以「步行到鄰站借車」或「原站等冷卻」建模，"
             "讓路線把同站續借的 10~15 分鐘冷卻一併納入最佳化",
    )

    st.sidebar.divider()
    st.sidebar.subheader("起點 / 終點")

    use_address = st.sidebar.toggle(
        "用地址 / 地標查詢", value=False,
        help="輸入地址或地標（如「台北車站」），自動轉成經緯度（需網路）",
    )

    o_addr = d_addr = ""
    if use_address:
        o_addr = st.sidebar.text_input("起點地址", value="台北車站")
        d_addr = st.sidebar.text_input("終點地址", value="淡水捷運站")
        o_lat, o_lon = 25.0478, 121.5170
        d_lat, d_lon = 25.1677, 121.4456
    else:
        st.sidebar.caption("直接輸入經緯度")
        o_lat = st.sidebar.number_input(
            "起點緯度", value=25.0478, format="%.5f", key="o_lat"
        )
        o_lon = st.sidebar.number_input(
            "起點經度", value=121.5170, format="%.5f", key="o_lon"
        )
        d_lat = st.sidebar.number_input(
            "終點緯度", value=25.1677, format="%.5f", key="d_lat"
        )
        d_lon = st.sidebar.number_input(
            "終點經度", value=121.4456, format="%.5f", key="d_lon"
        )
    submit = st.sidebar.button("規劃路線", type="primary", width="stretch")

    return {
        "cities": tuple(cities),
        "has_tpass": has_tpass,
        "safety_margin": int(safety_margin),
        "strategy": strategy,
        "allow_cross_circle": allow_cross_circle,
        "require_availability": require_availability,
        "use_cooldown": use_cooldown,
        "use_address": use_address,
        "origin_address": o_addr,
        "destination_address": d_addr,
        "origin": (o_lat, o_lon),
        "destination": (d_lat, d_lon),
        "submit": submit,
    }


def render_summary(plan: RoutePlan) -> None:
    if not plan.feasible:
        st.error(f"❌ {plan.message}")
        return
    if plan.message:
        st.warning(plan.message)

    cols = st.columns(4)
    cols[0].metric("換車次數", plan.swap_count)
    cols[1].metric("總騎乘時間", f"{plan.total_minutes:.1f} 分")
    cols[2].metric("步行（起 / 終）",
                   f"{plan.walk_to_start_min:.1f} / {plan.walk_from_end_min:.1f} 分")
    total = (plan.total_minutes + plan.transfer_minutes
             + plan.walk_to_start_min + plan.walk_from_end_min)
    cols[3].metric("總時間（含步行）", f"{total:.1f} 分")
    if plan.transfer_minutes > 0:
        st.caption(f"含換車（步行 / 等冷卻）時間約 {plan.transfer_minutes:.1f} 分")


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
    st.subheader("🔁 換車點冷卻提醒")
    st.caption(
        f"在換車點還車後，於同一站再借同型車可能需等 {lo}~{hi} 分鐘冷卻。"
        "建議改到鄰近站借車，或改用雙卡輪替。"
    )
    for i, adv in enumerate(plan.swap_advice, 1):
        with st.expander(f"換車點 #{i}：{adv.station_name}", expanded=False):
            if not adv.alternatives:
                st.write("附近 300m 內查無其他有車可借的站，建議於原站等候冷卻時間。")
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


def _plan_on(graph, stations, origin, destination, strategy, use_cooldown) -> RoutePlan:
    if use_cooldown:
        return plan_cooldown_route(graph, stations, origin, destination, strategy=strategy)
    return plan_route(graph, stations, origin, destination, strategy=strategy)


def plan_with_google_validation(
    graph: nx.DiGraph, stations: pd.DataFrame,
    origin: tuple[float, float], destination: tuple[float, float],
    strategy: str, fmbc: dict[str, int], use_cooldown: bool, max_iter: int = 4,
):
    """規劃路線，並以 Google 實測時間驗證：若某段實際 > 該段免費上限，移除該邊後重新
    規劃，直到各段都通過或無法再避開。

    回傳 (plan, google_rows, any_over, google_times, removed_count)。
    """
    removed: list = []
    default_limit = max(fmbc.values()) if fmbc else 30
    plan = _plan_on(graph, stations, origin, destination, strategy, use_cooldown)
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
        plan = _plan_on(view, stations, origin, destination, strategy, use_cooldown)

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

def _resolve_addresses(
    origin_address: str, destination_address: str
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """將起終點地址轉成經緯度。任一失敗則顯示錯誤並回傳 None。"""
    try:
        o_lat, o_lon, o_name = geocode_cached(origin_address)
        d_lat, d_lon, d_name = geocode_cached(destination_address)
    except GeocodeError as e:
        st.error(f"❌ 地址解析失敗：{e}")
        return None
    # 顯示實際匹配到的地名，方便確認是否解析正確（地名不符常是路線怪異的主因）
    st.info(
        f"📍 起點「{origin_address}」→ {o_name}（{o_lat:.5f}, {o_lon:.5f}）\n\n"
        f"🏁 終點「{destination_address}」→ {d_name}（{d_lat:.5f}, {d_lon:.5f}）"
    )
    return (o_lat, o_lon), (d_lat, d_lon)


def main() -> None:
    _bridge_secrets_to_env()
    st.set_page_config(page_title="YouBike 最省錢攻略", layout="wide")
    st.title("🚲 YouBike 最省錢騎乘攻略")
    st.caption("自動規劃中途換車路線，全程免費抵達目的地")

    inp = render_sidebar()

    if not inp["cities"]:
        st.info("請從側欄選擇至少一個城市。")
        return

    graph, stations = get_graph(
        inp["cities"],
        inp["has_tpass"],
        inp["safety_margin"],
        inp["allow_cross_circle"],
        inp["require_availability"],
        inp["use_cooldown"],
    )
    if stations.empty:
        st.error("無法載入任何站點資料。")
        return

    st.caption(f"已載入 {len(stations)} 站、{graph.number_of_edges()} 條邊")

    origin = inp["origin"]
    destination = inp["destination"]
    if inp["use_address"]:
        resolved = _resolve_addresses(
            inp["origin_address"], inp["destination_address"]
        )
        if resolved is None:
            return
        origin, destination = resolved

    # 只有按下「規劃路線」才重算；結果存入 session_state，後續按 Google / AI 等按鈕
    # 觸發 rerun 時仍能保留（否則 submit 變 False，畫面會跳回預設訊息）。
    if inp["submit"]:
        t0 = time.time()
        fmbc_now = free_minutes_by_city(list(inp["cities"]), inp["has_tpass"])
        n_removed = 0
        if settings.google_maps_api_key():
            # 以 Google 實測驗證，超出免費上限的路段自動避開重規劃
            with st.spinner("規劃並以 Google Maps 驗證各段時間（自動避開超時路段）…"):
                plan, g_rows, g_over, g_times, n_removed = plan_with_google_validation(
                    graph, stations, origin, destination,
                    inp["strategy"], fmbc_now, inp["use_cooldown"],
                )
            if plan.feasible:
                sig = _route_signature(plan)
                st.session_state["google_result"] = {
                    "sig": sig, "rows": g_rows, "any_over": g_over,
                }
                st.session_state["google_times"] = {"sig": sig, "data": g_times}
        else:
            plan = _plan_on(
                graph, stations, origin, destination,
                inp["strategy"], inp["use_cooldown"],
            )
        elapsed_ms = (time.time() - t0) * 1000
        if inp["use_address"]:
            o_label = inp["origin_address"] or "起點"
            d_label = inp["destination_address"] or "終點"
        else:
            o_label = f"({origin[0]:.4f}, {origin[1]:.4f})"
            d_label = f"({destination[0]:.4f}, {destination[1]:.4f})"
        st.session_state["result"] = {
            "plan": plan,
            "origin": origin, "destination": destination,
            "o_label": o_label, "d_label": d_label,
            "fmbc": fmbc_now,
            "id_to_city": dict(zip(stations["station_id"], stations["city"])),
            "stations": stations,
            "has_tpass": inp["has_tpass"],
            "elapsed_ms": elapsed_ms,
            "n_removed": n_removed,
        }

    result = st.session_state.get("result")

    # 地圖
    if result is not None:
        r_o, r_d, r_st = result["origin"], result["destination"], result["stations"]
        center = ((r_o[0] + r_d[0]) / 2, (r_o[1] + r_d[1]) / 2)
        m = render_base_map(r_st, center=center, zoom_start=12)
        m = draw_route(m, result["plan"], r_st, r_o, r_d)
    else:
        center = ((origin[0] + destination[0]) / 2,
                  (origin[1] + destination[1]) / 2)
        m = render_base_map(stations, center=center, zoom_start=12)

    map_col, info_col = st.columns([2, 1])
    with map_col:
        st_folium(m, width=None, height=620, returned_objects=[])
    with info_col:
        if result is None:
            st.info("設定好起終點後，按側欄的「規劃路線」開始。")
        else:
            st.caption(f"規劃耗時 {result['elapsed_ms']:.0f} ms")
            render_summary(result["plan"])
            if result.get("n_removed"):
                st.caption(
                    f"🛰️ 已依 Google 實測自動避開 {result['n_removed']} 段可能超時的路段。"
                )
            _render_endpoint_diagnostic(result)

    if result is not None and result["plan"].feasible:
        st.subheader("詳細行程")
        render_itinerary(result["plan"], result["fmbc"], result["id_to_city"])
        render_google_refinement(result["plan"], result["stations"], result["fmbc"])
        render_cooldown_advice(result["plan"])
        render_ai_feedback(
            result["plan"], result["stations"], result["fmbc"],
            result["id_to_city"], result["o_label"], result["d_label"],
            result["has_tpass"],
        )


if __name__ == "__main__":
    main()
