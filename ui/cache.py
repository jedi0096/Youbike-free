"""資料載入與快取：站點、站點圖、地址查詢、Google 時間（都用 Streamlit 快取）。"""
from __future__ import annotations

import gc
import logging

import networkx as nx
import pandas as pd
import streamlit as st

from config import DEFAULT_CYCLING_SPEED_KMH, ROUTE_DETOUR_FACTOR, free_minutes_by_city
from config import settings
from core import memory_guard
from core.cooldown import build_cooldown_graph
from core.geocoder import geocode_address_verbose
from core.gmaps import get_travel_time
from core.graph_builder import build_station_graph
from data.fetcher import FetchError, fetch_city_stations
from data.preprocessor import (
    filter_invalid_stations,
    merge_cities,
    normalize_stations,
)

_LOG = logging.getLogger(__name__)

# 規劃結果相關的 session_state 鍵；換區域或清快取時一起清掉。
RESULT_KEYS = ("result", "google_result", "google_times", "ai_result")


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


# 站點圖是整個 App 最吃記憶體的東西：側欄每一種設定組合各建一張，以模擬的北北桃
# 站點實測，一張約 0.4~2 GB（騎得越快、圖越大）。自己架設後 App 會長時間不重啟，
# 所以快取加上三道保險，數值都可用環境變數調整（見 .env.example 與 DEPLOY.md）：
# - max_entries：最多同時留幾張圖。
# - ttl：圖裡含有建圖當下的即時車況（有車可借 / 有位可還的過濾、地圖上的站點顏色），
#   不定期重建的話，App 開著幾天，路線就會用到幾天前的車況。
# - 建圖前的記憶體檢查（get_graph 內）：max_entries 是新圖建好才淘汰舊圖，新舊並存時
#   可能超過主機的記憶體上限；剩餘記憶體不夠就先把舊圖清掉再建。
GRAPH_CACHE_TTL_SECONDS = settings.graph_cache_ttl_seconds()
GRAPH_CACHE_MAX_ENTRIES = settings.graph_cache_max_entries()
GRAPH_BUILD_HEADROOM_MB = settings.graph_build_headroom_mb()


@st.cache_resource(
    ttl=GRAPH_CACHE_TTL_SECONDS,
    max_entries=GRAPH_CACHE_MAX_ENTRIES,
    show_spinner="建構站點圖中…",
)
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
    # 走到這裡表示快取裡沒有這張圖，需要重建。一次只建一張，並先確認記憶體放得下。
    with memory_guard.BUILD_LOCK:
        if memory_guard.needs_room(GRAPH_BUILD_HEADROOM_MB):
            _LOG.warning(
                "可用記憶體少於 %d MB，建新站點圖前先清除快取中的舊圖", GRAPH_BUILD_HEADROOM_MB
            )
            get_graph.clear()
            # 舊版 NetworkX 的圖用過之後帶有循環參照，要回收過才會真的把記憶體還回去
            gc.collect()
        if use_cooldown:
            g = build_cooldown_graph(df, **common)
        else:
            g = build_station_graph(df, **common)
    return g, df


def _clear_caches_and_rerun() -> None:
    """清除站點 / 圖快取與規劃結果，重抓最新即時資料後重跑。"""
    st.cache_data.clear()
    st.cache_resource.clear()
    for k in RESULT_KEYS:
        st.session_state.pop(k, None)
    st.rerun()
