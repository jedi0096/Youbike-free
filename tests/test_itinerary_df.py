"""行程表 DataFrame 的 Arrow 相容性回歸測試。

node-split 模式下，ride 段有數值的免費上限、walk/wait 段為 "—"，
若同欄混入 int 與 str，pandas→pyarrow 序列化（streamlit 顯示）會崩潰。
"""
from __future__ import annotations

import pyarrow as pa

from ui.results import itinerary_dataframe
from core.route_optimizer import RoutePlan, RouteSegment


def _mixed_plan() -> RoutePlan:
    segs = [
        RouteSegment("A1", "A2", "A站", "B站", 25.0, 5.0, mode="ride"),
        RouteSegment("A2", "A5", "B站", "B鄰", 2.5, 0.2, mode="walk"),
        RouteSegment("A5", "A4", "B鄰", "D站", 20.0, 4.0, mode="ride"),
        RouteSegment("A4", "A4", "D站", "D站", 12.0, 0.0, mode="wait"),
        RouteSegment("A4", "A9", "D站", "E站", 15.0, 3.0, mode="ride"),
    ]
    return RoutePlan(
        segments=segs, total_minutes=60.0, swap_count=2,
        walk_to_start_min=3.0, walk_from_end_min=4.0,
        strategy="shortest_time", feasible=True, transfer_minutes=14.5,
    )


_FMBC = {"台北市": 30}
_ID2CITY = {"A1": "台北市", "A2": "台北市", "A5": "台北市", "A4": "台北市", "A9": "台北市"}


def test_itinerary_dataframe_is_arrow_serializable():
    df = itinerary_dataframe(_mixed_plan(), _FMBC, _ID2CITY)
    # 重現 streamlit 內部行為：若混型會在此拋 ArrowInvalid
    table = pa.Table.from_pandas(df)
    assert table.num_rows == 5


def test_mixed_columns_are_uniform_string_typed():
    df = itinerary_dataframe(_mixed_plan(), _FMBC, _ID2CITY)
    for col in ("免費上限 (分)", "免費餘裕 (分)"):
        assert df[col].map(type).eq(str).all(), f"{col} 欄應全為 str"


def test_ride_only_plan_still_serializable():
    plan = RoutePlan(
        segments=[RouteSegment("A1", "A2", "A", "B", 10.0, 1.5, mode="ride")],
        total_minutes=10.0, swap_count=0,
        walk_to_start_min=1.0, walk_from_end_min=1.0,
        strategy="fewest_swaps", feasible=True,
    )
    df = itinerary_dataframe(plan, _FMBC, _ID2CITY)
    pa.Table.from_pandas(df)  # 不應拋出
    assert list(df["類型"]) == ["🚲 騎乘"]
