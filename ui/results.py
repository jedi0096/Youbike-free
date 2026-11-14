"""規劃結果：摘要、逐步指引、詳細行程表、換車點冷卻提醒。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd
import streamlit as st

from config import SAFETY_MARGIN_MINUTES
from core.directions import build_steps, estimate_arrival, total_trip_minutes
from core.route_optimizer import SAME_STATION_COOLDOWN_MIN, RoutePlan
from core.time_estimator import haversine_km


@dataclass
class Leg:
    """結果卡進度條上的一小段。"""
    kind: str                 # "ride" | "walk" | "wait"
    minutes: float
    limit: int | None = None  # 騎乘段的免費上限（分）；步行 / 等待為 None

    @property
    def margin(self) -> float | None:
        """騎乘段離免費上限還剩幾分鐘；步行 / 等待為 None。"""
        return None if self.limit is None else self.limit - self.minutes


def trip_legs(plan: RoutePlan, fmbc: dict[str, int], id_to_city: dict[str, str]) -> list[Leg]:
    """把整趟行程拆成進度條用的小段：走去借車 → 各段騎乘 / 換車 → 走到終點。

    免費上限以借車站所在縣市為準（與詳細行程表一致）。
    """
    if not plan.feasible:
        return []
    default_limit = max(fmbc.values()) if fmbc else 30
    legs: list[Leg] = []
    if plan.walk_to_start_min > 0:
        legs.append(Leg("walk", plan.walk_to_start_min))
    for seg in plan.segments:
        if seg.mode == "ride":
            city = id_to_city.get(seg.from_station_id, "")
            legs.append(Leg("ride", seg.minutes, fmbc.get(city, default_limit)))
        else:
            legs.append(Leg("wait" if seg.mode == "wait" else "walk", seg.minutes))
    if plan.walk_from_end_min > 0:
        legs.append(Leg("walk", plan.walk_from_end_min))
    return legs


def _leg_class(leg: Leg, tight_margin_min: float) -> str:
    if leg.margin is None:
        return leg.kind
    if leg.margin < 0:
        return "ride over"
    return "ride tight" if leg.margin < tight_margin_min else "ride"


def trip_summary_html(
    plan: RoutePlan,
    legs: list[Leg],
    depart: datetime | None = None,
    tight_margin_min: float = 4.0,
) -> str:
    """結果卡最上面那塊：總時間、車資、抵達時間、分段進度條、各段「用了幾分 / 上限」。

    只輸出數字與固定文字（不含站名等外部資料），所以不需要跳脫 HTML。
    """
    total = total_trip_minutes(plan)
    fee_risk = "調度費" in (plan.message or "")
    rides = [leg for leg in legs if leg.kind == "ride"]
    over = any(leg.margin is not None and leg.margin < 0 for leg in rides)
    if fee_risk:
        pill = '<span class="yb-pill warn">可能收調度費</span>'
    elif over:
        pill = '<span class="yb-pill warn">有路段超過免費時間</span>'
    else:
        pill = '<span class="yb-pill">全程 0 元</span>'

    sub = []
    if depart is not None:
        sub.append(f"現在出發約 {estimate_arrival(depart, plan).strftime('%H:%M')} 到")
    sub.append(f"換車 {plan.swap_count} 次")
    walk = plan.walk_to_start_min + plan.walk_from_end_min
    detail = f"騎 {plan.total_minutes:.0f} 分、走 {walk:.0f} 分"
    if plan.transfer_minutes > 0:
        detail += f"、換車 {plan.transfer_minutes:.0f} 分"
    sub.append(detail)

    bar = "".join(
        f'<div class="{_leg_class(leg, tight_margin_min)}" '
        f'style="flex:{max(leg.minutes, 0.5):.1f}"></div>'
        for leg in legs
    )
    stats = "".join(
        f'<div class="{_leg_class(leg, tight_margin_min).replace("ride", "").strip()}">'
        f"第{i}段<b>{leg.minutes:.0f}/{leg.limit}</b></div>"
        for i, leg in enumerate(rides, 1)
    )
    if rides:
        tightest = min(leg.margin for leg in rides)
        cls = "over" if tightest < 0 else ("tight" if tightest < tight_margin_min else "")
        text = f"超過 {-tightest:.0f} 分" if tightest < 0 else f"剩 {tightest:.0f} 分"
        stats += f'<div class="{cls}">最緊<b>{text}</b></div>'

    return (
        f'<div class="yb-big"><span class="n">{total:.0f} 分</span>{pill}</div>'
        f'<div class="yb-sub">{" · ".join(sub)}</div>'
        f'<div class="yb-legs">{bar}</div>'
        f'<div class="yb-stats">{stats}</div>'
    )


def render_trip_summary(result: dict) -> None:
    """結果卡的摘要。找不到路線時顯示原因。"""
    plan: RoutePlan = result["plan"]
    if not plan.feasible:
        st.error(f"❌ {plan.message}")
        return
    if plan.message:
        st.warning(plan.message)
    # 規劃時每段都會留至少「緩衝」分鐘；餘裕比緩衝還少（例如 Google 校正後變慢）才標橘色
    tight = result.get("safety_margin", SAFETY_MARGIN_MINUTES) - 0.5
    legs = trip_legs(plan, result["fmbc"], result["id_to_city"])
    st.html(trip_summary_html(plan, legs, result.get("depart"), tight))


def render_steps(result: dict, heading: bool = True) -> None:
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
    if heading:
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
    # 結果卡只有約 360px 寬：數字欄放前面、站名放後面，窄螢幕也先看得到重點
    df = df[[
        "段", "類型", "時間 (分)", "免費上限 (分)", "免費餘裕 (分)", "距離 (km)",
        "起站", "終站", "起站縣市",
    ]]
    small = st.column_config.Column(width="small")
    st.dataframe(
        df, hide_index=True, width="stretch",
        column_config={c: small for c in df.columns[:6]},
    )


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
