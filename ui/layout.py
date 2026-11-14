"""版面：地圖鋪滿整個畫面，搜尋卡與結果卡浮在地圖上。

Streamlit 的 st.container(key=...) 會在網頁上產生 class「st-key-<key>」，
這裡用 CSS 把那幾個容器固定在畫面上的位置：

    KEY_MAP     地圖，鋪滿整個視窗
    KEY_SIDE    左側欄位（桌機）：上面放搜尋卡、下面放結果卡
    KEY_SEARCH  搜尋卡：起終點、路線偏好、規劃按鈕
    KEY_SHEET   結果卡：桌機在左下角；手機貼在畫面底部、可上下捲

手機（寬度 640px 以下）改成搜尋卡貼頂、結果卡貼底。
"""
from __future__ import annotations

import streamlit as st

KEY_MAP = "yb_map"
KEY_SIDE = "yb_side"
KEY_SEARCH = "yb_search"
KEY_SHEET = "yb_sheet"

# 手機版的寬度門檻（px）。地圖裡調整視野的 JS 也用同一個數字。
MOBILE_MAX_WIDTH_PX = 640
# 卡片寬度與離畫面邊緣的距離（px），地圖調整視野時要避開這塊。
CARD_WIDTH_PX = 400
CARD_GAP_PX = 16

_CSS = """
<style>
/* ---- 讓地圖能鋪滿：拿掉 Streamlit 的頁首與內距 ---- */
header[data-testid="stHeader"] { display: none; }
[data-testid="stMainBlockContainer"] { padding: 0 !important; max-width: none !important; }
[data-testid="stMain"] { overflow: hidden; }

/* ---- 地圖 ---- */
.st-key-%(map)s { position: fixed; inset: 0; z-index: 1; }
.st-key-%(map)s iframe {
  width: 100vw !important; height: 100vh !important; height: 100dvh !important;
  display: block; border: 0;
}

/* ---- 左側欄位：本身不擋滑鼠，只有卡片可以點 ---- */
.st-key-%(side)s {
  position: fixed; z-index: 20; pointer-events: none;
  top: %(gap)dpx; left: %(gap)dpx; bottom: %(gap)dpx; width: %(card)dpx;
  display: flex; flex-direction: column; justify-content: space-between; gap: 12px;
}
.st-key-%(side)s > div { pointer-events: none; width: 100%%; }
.st-key-%(side)s > div:has(> .st-key-%(search)s) { flex: 0 0 auto; max-height: 62%%; display: flex; }
.st-key-%(side)s > div:has(> .st-key-%(sheet)s) { flex: 0 1 auto; min-height: 0; display: flex; }

.st-key-%(search)s, .st-key-%(sheet)s {
  pointer-events: auto; background: #fff; border-radius: 16px;
  box-shadow: 0 8px 30px rgba(0, 0, 0, .18);
  padding: 14px 16px; overflow-y: auto; overflow-x: hidden; min-height: 0;
  flex-wrap: nowrap;
}
.st-key-%(search)s { gap: .55rem; }
.st-key-%(sheet)s { gap: .6rem; }

/* ---- 搜尋卡內部 ---- */
.yb-brand { font-weight: 800; font-size: 16px; color: #1f2a24; }
.yb-brand b { color: #1e7a4a; }
/* 起點藍點、終點紅點 */
.st-key-o_text, .st-key-d_text { position: relative; }
.st-key-o_text input, .st-key-d_text input { padding-left: 30px; }
.st-key-o_text::before, .st-key-d_text::before {
  content: ""; position: absolute; left: 11px; top: 50%%; z-index: 2;
  width: 10px; height: 10px; border-radius: 50%%; transform: translateY(-50%%);
  pointer-events: none;
}
.st-key-o_text::before { background: #1e6fd6; }
.st-key-d_text::before { background: #e04848; }
/* 卡片裡的左右分欄在手機上也維持並排（Streamlit 預設會改成上下疊） */
.st-key-%(search)s [data-testid="stHorizontalBlock"] { flex-wrap: nowrap; gap: .5rem; }
.st-key-%(search)s [data-testid="stColumn"] { min-width: 0; }
.st-key-swap_btn button { padding-left: 0; padding-right: 0; width: 100%%; }

/* ---- 結果卡內部 ---- */
.yb-big { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; color: #1f2a24; }
.yb-big .n { font-size: 32px; font-weight: 800; line-height: 1.1; }
.yb-pill { border-radius: 999px; padding: 3px 10px; font-size: 13px; font-weight: 700;
  background: #e5f4ec; color: #1e7a4a; }
.yb-pill.warn { background: #fdf0d8; color: #a86200; }
.yb-sub { font-size: 13px; color: #6b7770; margin-top: 2px; }
.yb-legs { display: flex; gap: 3px; margin: 12px 0 8px; }
.yb-legs div { height: 8px; border-radius: 4px; min-width: 4px; }
.yb-legs .ride { background: #1e7a4a; }
.yb-legs .ride.tight { background: #f0a020; }
.yb-legs .ride.over { background: #e04848; }
.yb-legs .walk { background: #9aa59f; }
.yb-legs .wait { background: #c9b037; }
.yb-stats { display: flex; flex-wrap: wrap; gap: 6px 18px; font-size: 12px; color: #6b7770; }
.yb-stats b { display: block; font-size: 16px; color: #1f2a24; font-weight: 700; }
.yb-stats .tight b { color: #d98200; }
.yb-stats .over b { color: #d03030; }
.yb-credit { font-size: 11px; color: #8a948e; }

/* ---- 手機：搜尋卡貼頂、結果卡貼底 ---- */
@media (max-width: %(mobile)dpx) {
  .st-key-%(side)s { inset: 0; width: auto; display: block; }
  .st-key-%(side)s > div:has(> .st-key-%(search)s),
  .st-key-%(side)s > div:has(> .st-key-%(sheet)s) { display: block; max-height: none; }
  .st-key-%(search)s {
    position: fixed; top: 8px; left: 8px; right: 8px; width: auto;
    max-height: 60vh; max-height: 60dvh; padding: 10px 12px; gap: .4rem;
  }
  .st-key-%(sheet)s {
    position: fixed; left: 0; right: 0; bottom: 0; width: auto;
    max-height: 44vh; max-height: 44dvh;
    border-radius: 20px 20px 0 0; padding: 12px 16px 18px;
  }
  .yb-brand { display: none; }
  .yb-big .n { font-size: 28px; }
}
</style>
"""


def inject_css() -> None:
    """把版面 CSS 放進頁面。每次重跑都要呼叫（Streamlit 每次都會重畫整頁）。"""
    st.html(_CSS % {
        "map": KEY_MAP, "side": KEY_SIDE, "search": KEY_SEARCH, "sheet": KEY_SHEET,
        "gap": CARD_GAP_PX, "card": CARD_WIDTH_PX, "mobile": MOBILE_MAX_WIDTH_PX,
    })
