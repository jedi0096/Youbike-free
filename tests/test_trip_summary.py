"""結果卡摘要（總時間、分段進度條、各段免費餘裕）的測試。"""
from __future__ import annotations

from datetime import datetime

from core.route_optimizer import RoutePlan, RouteSegment
from ui.results import Leg, trip_legs, trip_summary_html


def _plan(**kw) -> RoutePlan:
    segs = [
        RouteSegment("A1", "A2", "A站", "B站", 27.0, 5.0, mode="ride"),
        RouteSegment("A2", "A5", "B站", "B鄰", 2.0, 0.2, mode="walk"),
        RouteSegment("A5", "T1", "B鄰", "桃園站", 20.0, 4.0, mode="ride"),
        RouteSegment("T1", "T1", "桃園站", "桃園站", 12.0, 0.0, mode="wait"),
        RouteSegment("T1", "T2", "桃園站", "終站", 45.0, 9.0, mode="ride"),
    ]
    base = dict(
        segments=segs, total_minutes=92.0, swap_count=2,
        walk_to_start_min=3.0, walk_from_end_min=4.0,
        strategy="fewest_swaps", feasible=True, transfer_minutes=14.0,
    )
    base.update(kw)
    return RoutePlan(**base)


_FMBC = {"台北市": 30, "桃園市": 60}
_ID2CITY = {"A1": "台北市", "A2": "台北市", "A5": "台北市", "T1": "桃園市", "T2": "桃園市"}


def test_trip_legs_order_and_kinds():
    legs = trip_legs(_plan(), _FMBC, _ID2CITY)
    assert [leg.kind for leg in legs] == [
        "walk", "ride", "walk", "ride", "wait", "ride", "walk",
    ]
    assert legs[0].minutes == 3.0 and legs[-1].minutes == 4.0


def test_trip_legs_limit_follows_borrow_city():
    rides = [leg for leg in trip_legs(_plan(), _FMBC, _ID2CITY) if leg.kind == "ride"]
    # 免費上限看借車站：前兩段在台北借（30 分），最後一段在桃園借（60 分）
    assert [leg.limit for leg in rides] == [30, 30, 60]
    assert [leg.margin for leg in rides] == [3.0, 10.0, 15.0]


def test_trip_legs_non_ride_has_no_margin():
    assert Leg("walk", 5.0).margin is None
    assert Leg("wait", 12.0).margin is None


def test_trip_legs_skips_zero_walks_and_infeasible():
    legs = trip_legs(_plan(walk_to_start_min=0.0, walk_from_end_min=0.0), _FMBC, _ID2CITY)
    assert legs[0].kind == "ride" and legs[-1].kind == "ride"
    assert trip_legs(_plan(feasible=False), _FMBC, _ID2CITY) == []


def test_trip_legs_unknown_city_uses_largest_limit():
    rides = [leg for leg in trip_legs(_plan(), _FMBC, {}) if leg.kind == "ride"]
    assert {leg.limit for leg in rides} == {60}


def test_summary_html_free_trip():
    plan = _plan()
    legs = trip_legs(plan, _FMBC, _ID2CITY)
    html = trip_summary_html(plan, legs, datetime(2026, 11, 15, 8, 0), tight_margin_min=2.5)
    # 全程 = 走 3 + 騎 92 + 換車 14 + 走 4
    assert "113 分" in html
    assert "全程 0 元" in html
    assert "09:53" in html
    assert "換車 2 次" in html
    assert "27/30" in html and "45/60" in html
    assert "剩 3 分" in html
    # 餘裕都不低於門檻 → 沒有橘色 / 紅色
    assert "tight" not in html and "over" not in html
    # 進度條一段一格
    assert html.count('style="flex:') == len(legs)


def test_summary_html_marks_tight_and_over():
    plan = _plan()
    legs = trip_legs(plan, _FMBC, _ID2CITY)
    assert "tight" in trip_summary_html(plan, legs, tight_margin_min=4.0)

    plan.segments[0].minutes = 33.0
    legs = trip_legs(plan, _FMBC, _ID2CITY)
    html = trip_summary_html(plan, legs)
    assert "有路段超過免費時間" in html
    assert "超過 3 分" in html
    assert "全程 0 元" not in html


def test_summary_html_dispatch_fee_risk():
    plan = _plan(message="跨區還車可能收調度費")
    html = trip_summary_html(plan, trip_legs(plan, _FMBC, _ID2CITY))
    assert "可能收調度費" in html and "全程 0 元" not in html


def test_summary_html_walk_only_trip():
    plan = RoutePlan(
        segments=[], total_minutes=0.0, swap_count=0,
        walk_to_start_min=2.0, walk_from_end_min=3.0,
        strategy="fewest_swaps", feasible=True,
    )
    legs = trip_legs(plan, _FMBC, _ID2CITY)
    html = trip_summary_html(plan, legs)
    assert [leg.kind for leg in legs] == ["walk", "walk"]
    assert "5 分" in html and "最緊" not in html
