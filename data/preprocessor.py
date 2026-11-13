"""資料清洗與欄位標準化模組。

各縣市 API 回傳欄位名稱不一致，本模組將其統一為標準欄位，並過濾異常站點。

標準欄位:
    station_id, name, lat, lon, total, available_bikes, available_docks,
    city, updated_at, active
"""
from __future__ import annotations

from typing import Any

import pandas as pd


STANDARD_COLUMNS = [
    "station_id",
    "name",
    "lat",
    "lon",
    "total",
    "available_bikes",
    "available_docks",
    "city",
    "updated_at",
    "active",
]


# 各縣市原始欄位 -> 標準欄位的映射。
# 同一標準欄位接受多個候選原始欄位名稱，取第一個存在的。
FIELD_MAP: dict[str, list[str]] = {
    # 各欄位最後一個候選為 YouBike 官方全台資料（apis.youbike.com.tw）的欄位名
    "station_id": ["sno", "StationID", "station_no"],
    "name": ["sna", "StationName", "station_name", "name_tw"],
    "lat": ["latitude", "lat", "Latitude"],
    "lon": ["longitude", "lng", "Longitude"],
    "total": ["Quantity", "tot_quantity", "tot", "total", "parking_spaces"],
    "available_bikes": ["available_rent_bikes", "sbi_quantity", "sbi", "AvailableRentBikes",
                        "available_spaces"],
    "available_docks": ["available_return_bikes", "bemp_quantity", "bemp", "AvailableReturnBikes",
                        "empty_spaces"],
    "updated_at": ["srcUpdateTime", "updateTime", "mday", "updated_at"],
    "active": ["act", "Status", "status"],
}


def _pick(record: dict[str, Any], candidates: list[str]) -> Any:
    """從候選 key 中取第一個存在的值。"""
    for key in candidates:
        if key in record:
            return record[key]
    return None


def normalize_stations(raw: list[dict[str, Any]], city: str) -> pd.DataFrame:
    """將原始 API 資料正規化為標準 DataFrame。

    Args:
        raw: API 原始回傳的 station list。
        city: 縣市名稱（會寫入 city 欄位）。

    Returns:
        包含 STANDARD_COLUMNS 欄位的 DataFrame。
    """
    rows = []
    for r in raw:
        row = {std: _pick(r, keys) for std, keys in FIELD_MAP.items()}
        row["city"] = city
        rows.append(row)

    df = pd.DataFrame(rows, columns=STANDARD_COLUMNS)

    for col in ("lat", "lon"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ("total", "available_bikes", "available_docks"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

    df["active"] = df["active"].apply(
        lambda v: True if v is None else str(v) == "1"
    )
    df["station_id"] = df["station_id"].astype(str)
    df["name"] = df["name"].astype(str)
    return df


def filter_invalid_stations(df: pd.DataFrame) -> pd.DataFrame:
    """過濾異常站點：座標為 0 或 NaN、空名稱、總車位為 0、停用站。"""
    mask = (
        df["lat"].notna() & df["lon"].notna()
        & (df["lat"] != 0) & (df["lon"] != 0)
        & df["name"].str.len().gt(0)
        & df["total"].fillna(0).gt(0)
        & df["active"]
    )
    return df[mask].reset_index(drop=True)


def merge_cities(dfs: list[pd.DataFrame]) -> pd.DataFrame:
    """合併多縣市資料為單一 DataFrame，並以 city 前綴避免 station_id 撞號。"""
    if not dfs:
        return pd.DataFrame(columns=STANDARD_COLUMNS)
    merged = pd.concat(dfs, ignore_index=True)
    merged["station_id"] = merged["city"] + "_" + merged["station_id"]
    return merged.drop_duplicates(subset=["station_id"]).reset_index(drop=True)
