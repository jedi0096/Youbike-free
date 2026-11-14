"""規劃流程：起終點轉座標、規劃路線、用 Google 實測時間驗證並避開超時路段。"""
from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

import networkx as nx
import pandas as pd
import streamlit as st

from config import free_minutes_by_city
from config import settings
from core.cooldown import plan_cooldown_route
from core.directions import parse_latlon
from core.geocoder import GeocodeError
from core.gmaps import GMapsError
from core.route_optimizer import MAX_WALK_KM, RoutePlan, plan_route
from ui.cache import geocode_cached, gmaps_travel_cached


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


def _plan_and_store(inp: dict, graph: nx.DiGraph, stations: pd.DataFrame) -> bool:
    """解析起終點、規劃路線，結果存入 session_state。起終點解析失敗時回傳 False。"""
    o = _resolve_point(inp["origin_text"], "起點")
    d = _resolve_point(inp["destination_text"], "終點")
    if o is None or d is None:
        return False
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
    return True
