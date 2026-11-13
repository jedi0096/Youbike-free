"""資料清洗與欄位標準化模組。

各縣市 API 回傳欄位名稱不一致，本模組將其統一為標準欄位，並過濾異常站點。

標準欄位:
    station_id, name, lat, lon, total, available_bikes, available_docks,
    city, updated_at, active, available_ebikes

關於電輔車（YouBike 2.0E）：
    電輔車不適用「前 30 分鐘免費」，從第一分鐘就要收費（每 30 分鐘 20 元）。
    因此 available_bikes 只算「一般車（2.0）」，電輔車另存於 available_ebikes。
    - 新北 API 有分開的 yb2_quantity / eyb_quantity，可正確拆開。
    - YouBike 官方格式的 available_spaces_detail {yb2, eyb} 也支援。
    - 台北開放資料目前只給總數，無法分辨，available_ebikes 為空值（NA），
      available_bikes 可能含電輔車。
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
    "available_ebikes",
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


# 一般車（YouBike 2.0）與電輔車（2.0E）分開計數的欄位
REGULAR_BIKE_KEYS = ["yb2_quantity", "yb2"]
EBIKE_KEYS = ["eyb_quantity", "eyb"]


def _split_bike_counts(record: dict[str, Any]) -> tuple[Any, Any] | None:
    """若資料有一般車 / 電輔車分開的數量，回傳 (一般車, 電輔車)，否則回傳 None。"""
    sources = [record]
    detail = record.get("available_spaces_detail")
    if isinstance(detail, dict):
        sources.append(detail)
    for src in sources:
        regular = _pick(src, REGULAR_BIKE_KEYS)
        ebikes = _pick(src, EBIKE_KEYS)
        if regular is not None or ebikes is not None:
            return regular, ebikes
    return None


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
        split = _split_bike_counts(r)
        if split is not None:
            regular, ebikes = split
            row["available_ebikes"] = ebikes
            if regular is not None:
                # 只有一般車適用免費時段，可借車數只算一般車
                row["available_bikes"] = regular
            elif ebikes is not None and row["available_bikes"] is not None:
                try:
                    row["available_bikes"] = max(0, int(row["available_bikes"]) - int(ebikes))
                except (TypeError, ValueError):
                    pass
        rows.append(row)

    df = pd.DataFrame(rows, columns=STANDARD_COLUMNS)

    for col in ("lat", "lon"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ("total", "available_bikes", "available_docks", "available_ebikes"):
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
