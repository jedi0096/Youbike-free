"""選用功能：Google Maps 時間校正、Gemini AI 行程建議。"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from config import settings
from core.feedback import collect_facts, generate_feedback
from core.route_optimizer import RoutePlan
from ui.planning import (
    _compute_google_refinement,
    _route_signature,
    _store_google_result,
)


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
