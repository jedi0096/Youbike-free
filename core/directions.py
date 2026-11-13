"""把規劃結果轉成「一步一步」的白話指引，以及介面用的小工具。

這裡全是純函式（不依賴 Streamlit），方便單獨測試：
    - parse_latlon：辨識使用者輸入的是不是「緯度, 經度」
    - google_maps_dir_url：產生 Google 地圖導航連結（不需 API 金鑰）
    - build_steps：把 RoutePlan 轉成逐步指引
    - estimate_arrival：依出發時間推算預計抵達時刻
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlencode

from core.route_optimizer import RoutePlan

# 「25.0478, 121.5170」或「25.0478 121.5170」這類輸入
_LATLON_RE = re.compile(
    r"^\s*\(?\s*(-?\d{1,3}(?:\.\d+)?)\s*[,，\s]\s*(-?\d{1,3}(?:\.\d+)?)\s*\)?\s*$"
)

# 台灣本島與離島的大致範圍，用來擋掉經緯度寫反等明顯錯誤
_TW_LAT = (21.0, 26.5)
_TW_LON = (118.0, 122.5)


def parse_latlon(text: str) -> tuple[float, float] | None:
    """若輸入是「緯度, 經度」就回傳座標，否則回傳 None（當作地址處理）。

    經緯度寫反（121.5, 25.0）會自動對調；不在台灣範圍內的座標回傳 None。
    """
    if not text:
        return None
    m = _LATLON_RE.match(text)
    if not m:
        return None
    a, b = float(m.group(1)), float(m.group(2))
    for lat, lon in ((a, b), (b, a)):
        if _TW_LAT[0] <= lat <= _TW_LAT[1] and _TW_LON[0] <= lon <= _TW_LON[1]:
            return lat, lon
    return None


def format_latlon(lat: float, lon: float) -> str:
    return f"{lat:.5f}, {lon:.5f}"


def google_maps_dir_url(
    origin: tuple[float, float],
    destination: tuple[float, float],
    travelmode: str = "bicycling",
) -> str:
    """Google 地圖導航連結（公開網址格式，不需金鑰）。手機會直接開啟地圖 App。"""
    params = {
        "api": "1",
        "origin": f"{origin[0]:.6f},{origin[1]:.6f}",
        "destination": f"{destination[0]:.6f},{destination[1]:.6f}",
        "travelmode": travelmode,
    }
    return "https://www.google.com/maps/dir/?" + urlencode(params)


@dataclass
class Step:
    """逐步指引中的一步。"""
    icon: str
    text: str
    minutes: float = 0.0
    nav_url: str = ""        # Google 地圖導航連結（騎乘 / 步行段）
    note: str = ""           # 補充說明（例如免費餘裕、冷卻提醒）
    warn: bool = False       # 是否需要特別注意（餘裕過少、車況不佳等）


def build_steps(
    plan: RoutePlan,
    coords: dict[str, tuple[float, float]],
    origin: tuple[float, float],
    destination: tuple[float, float],
    free_limit_for: dict[str, int] | None = None,
    availability: dict[str, tuple[int, int]] | None = None,
    tight_margin_min: float = 4.0,
) -> list[Step]:
    """把 RoutePlan 轉成白話的逐步指引。

    Args:
        coords: {station_id: (lat, lon)}
        free_limit_for: {station_id: 該站借車的免費上限分鐘}；提供時會算出每段餘裕。
        availability: {station_id: (可借車, 可還位)}；提供時會提示即時車況。
        tight_margin_min: 免費餘裕小於此值時標示提醒。
    """
    if not plan.feasible:
        return []
    availability = availability or {}
    free_limit_for = free_limit_for or {}
    advice_by_station = {a.station_id: a.alternatives for a in plan.swap_advice}
    steps: list[Step] = []

    if not plan.segments:
        steps.append(Step(
            "🚶", "起終點離同一個站最近，直接走過去就好",
            minutes=plan.walk_to_start_min + plan.walk_from_end_min,
            nav_url=google_maps_dir_url(origin, destination, "walking"),
        ))
        return steps

    first = plan.segments[0]
    bikes, _ = availability.get(first.from_station_id, (None, None))
    note = f"目前可借 {bikes} 台" if bikes is not None else ""
    steps.append(Step(
        "🚶", f"步行到「{first.from_name}」借車",
        minutes=plan.walk_to_start_min,
        nav_url=google_maps_dir_url(origin, coords[first.from_station_id], "walking"),
        note=note, warn=bikes is not None and bikes < 3,
    ))

    n_ride = sum(1 for s in plan.segments if s.mode == "ride")
    ride_no = 0
    for idx, seg in enumerate(plan.segments):
        a, b = coords[seg.from_station_id], coords[seg.to_station_id]
        if seg.mode == "wait":
            steps.append(Step(
                "⏱️", f"在「{seg.from_name}」等候冷卻後再借車",
                minutes=seg.minutes,
                note="同站還車後約需等 10~15 分鐘才能再借",
            ))
            continue
        if seg.mode == "walk":
            bikes, _ = availability.get(seg.to_station_id, (None, None))
            steps.append(Step(
                "🔁", f"還車後步行到隔壁的「{seg.to_name}」借車",
                minutes=seg.minutes,
                nav_url=google_maps_dir_url(a, b, "walking"),
                note=(f"目前可借 {bikes} 台" if bikes is not None else "")
                + "（避開同站冷卻）",
            ))
            continue

        ride_no += 1
        is_last_ride = ride_no == n_ride
        action = "還車" if is_last_ride else "還車換車"
        notes: list[str] = []
        warn = False
        limit = free_limit_for.get(seg.from_station_id)
        if limit is not None:
            margin = limit - seg.minutes
            notes.append(f"免費上限 {limit} 分，還有 {margin:.0f} 分餘裕")
            warn = margin < tight_margin_min
        _, docks = availability.get(seg.to_station_id, (None, None))
        if docks is not None:
            notes.append(f"目前可還 {docks} 位")
            warn = warn or docks < 3
        # 一般模式下，下一段若直接從同站出發 → 提醒冷卻，並附上最近的改借站
        nxt = plan.segments[idx + 1] if idx + 1 < len(plan.segments) else None
        if nxt is not None and nxt.mode == "ride" and nxt.from_station_id == seg.to_station_id:
            alts = advice_by_station.get(seg.to_station_id) or []
            if alts:
                name, walk_min, _dist, n_bikes = alts[0]
                notes.append(
                    f"換車：同站再借要等冷卻，可走 {walk_min:.0f} 分到「{name}」借"
                    f"（可借 {n_bikes} 台）"
                )
            else:
                notes.append("換車：附近沒有其他有車的站，同站再借需等 10~15 分冷卻")
        steps.append(Step(
            "🚲", f"騎到「{seg.to_name}」{action}（{seg.distance_km:.1f} km）",
            minutes=seg.minutes,
            nav_url=google_maps_dir_url(a, b, "bicycling"),
            note="；".join(notes), warn=warn,
        ))

    last = plan.segments[-1]
    steps.append(Step(
        "🏁", "步行抵達終點",
        minutes=plan.walk_from_end_min,
        nav_url=google_maps_dir_url(coords[last.to_station_id], destination, "walking"),
    ))
    return steps


def total_trip_minutes(plan: RoutePlan) -> float:
    """含步行與換車的全程時間（分鐘）。"""
    return (plan.total_minutes + plan.transfer_minutes
            + plan.walk_to_start_min + plan.walk_from_end_min)


def estimate_arrival(depart: datetime, plan: RoutePlan) -> datetime:
    """依出發時間推算預計抵達時刻。"""
    return depart + timedelta(minutes=total_trip_minutes(plan))
