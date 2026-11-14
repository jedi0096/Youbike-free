"""版面：地圖鋪滿整個畫面，搜尋卡與結果卡浮在地圖上。

Streamlit 的 st.container(key=...) 會在網頁上產生 class「st-key-<key>」，
這裡用 CSS 把那幾個容器固定在畫面上的位置：

    KEY_MAP     地圖，鋪滿整個視窗
    KEY_SIDE    左側欄位（桌機）：上面放搜尋卡、下面放結果卡
    KEY_SEARCH  搜尋卡：起終點、路線偏好、規劃按鈕
    KEY_SHEET   結果卡：桌機在左下角；手機貼在畫面底部、可上下捲
    KEY_LOADING 載入進度卡：抓站點、建圖、規劃時浮在地圖正中間（見 ui/progress.py）

手機（寬度 640px 以下）改成搜尋卡貼頂、結果卡貼底。
"""
from __future__ import annotations

import streamlit as st

KEY_MAP = "yb_map"
KEY_SIDE = "yb_side"
KEY_SEARCH = "yb_search"
KEY_SHEET = "yb_sheet"
KEY_LOADING = "yb_loading"

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
/* 地圖還沒畫出來前，底色先用接近地圖的淺灰綠，不會一片白 */
[data-testid="stApp"] { background: #eef1ec; }

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
    /* 結果卡最高 44%%，兩張卡加起來不能超過畫面，否則會疊在一起 */
    max-height: calc(56vh - 16px); max-height: calc(56dvh - 16px);
    padding: 10px 12px; gap: .4rem;
  }
  .st-key-%(sheet)s {
    position: fixed; left: 0; right: 0; bottom: 0; width: auto;
    max-height: 44vh; max-height: 44dvh;
    border-radius: 20px 20px 0 0; padding: 12px 16px 18px;
  }
  .yb-brand { display: none; }
  .yb-big .n { font-size: 28px; }
}
/* 矮小的手機（例如 360x640）：結果卡再矮一點，留空間看地圖 */
@media (max-width: %(mobile)dpx) and (max-height: 700px) {
  .st-key-%(search)s { max-height: calc(66vh - 16px); max-height: calc(66dvh - 16px); }
  .st-key-%(sheet)s { max-height: 34vh; max-height: 34dvh; }
}
/* 手機橫放（寬但很矮）：左欄整條一起捲動，不要把兩張卡各自擠扁 */
@media (min-width: %(mobile_plus)dpx) and (max-height: 480px) {
  .st-key-%(side)s { overflow-y: auto; pointer-events: auto; justify-content: flex-start; }
  .st-key-%(side)s > div:has(> .st-key-%(search)s),
  .st-key-%(side)s > div:has(> .st-key-%(sheet)s) {
    flex: 0 0 auto !important; max-height: none !important;
  }
  .st-key-%(search)s, .st-key-%(sheet)s { overflow: visible; flex: 0 0 auto; }
}
/* ---- 載入進度卡（ui/progress.py） ---- */
.st-key-%(loading)s {
  /* 要蓋在「偏好」彈出選單（z-index 約 1000060）之上，按「重抓即時車況」時才看得到 */
  position: fixed; z-index: 1000070; left: 50%%; top: 50%%;
  transform: translate(-50%%, -50%%); width: min(340px, calc(100vw - 32px));
  pointer-events: none;
}
.st-key-%(loading)s:not(:has(.yb-load)) { display: none; }
.yb-load {
  background: #fff; border-radius: 16px; padding: 14px 16px;
  box-shadow: 0 10px 34px rgba(0, 0, 0, .22); color: #1f2a24; font-size: 14px;
}
.yb-load-title { display: flex; align-items: center; gap: 8px; font-weight: 700; }
.yb-load-title .t { margin-left: auto; font-weight: 400; color: #6b7770; font-size: 12px; }
.yb-spin {
  width: 14px; height: 14px; border-radius: 50%%; flex: none;
  border: 2px solid #cfe9da; border-top-color: #1e7a4a;
  animation: yb-spin .8s linear infinite;
}
.yb-load-bar {
  position: relative; height: 8px; border-radius: 4px; background: #eef0ec;
  margin: 10px 0 8px; overflow: hidden;
}
.yb-load-bar i {
  position: absolute; left: 0; top: 0; bottom: 0; border-radius: 4px;
  background: repeating-linear-gradient(-45deg, #1e7a4a 0 10px, #2e9e5b 10px 20px);
  background-size: 28px 28px; animation: yb-stripe .8s linear infinite;
  transition: width .25s ease;
}
/* 不知道這一步跑了幾成時，多一段來回跑的光條，讓人知道還在跑 */
.yb-load-bar.unknown::after {
  content: ""; position: absolute; top: 0; bottom: 0; width: 30%%;
  background: linear-gradient(90deg, transparent, rgba(46, 158, 91, .55), transparent);
  animation: yb-sweep 1.2s ease-in-out infinite;
}
.yb-load-steps {
  list-style: none; margin: 0 !important; padding: 0 !important; font-size: 13px; color: #8a948e;
}
.yb-load-steps li { margin: 2px 0 !important; padding: 0 !important; }
.yb-load-steps li.done { color: #1e7a4a; }
.yb-load-steps li.now { color: #1f2a24; font-weight: 700; }
.yb-load-note { margin-top: 6px; font-size: 12px; color: #6b7770; }
@keyframes yb-spin { to { transform: rotate(360deg); } }
@keyframes yb-stripe { to { background-position: 28px 0; } }
@keyframes yb-sweep { from { left: -30%%; } to { left: 100%%; } }
@media (prefers-reduced-motion: reduce) {
  .yb-spin, .yb-load-bar i, .yb-load-bar.unknown::after { animation: none; }
}
@media (max-width: %(mobile)dpx) {
  /* 手機：避開上面的搜尋卡，放在畫面中間偏下 */
  .st-key-%(loading)s { top: 58%%; }
}
</style>
"""


def inject_css() -> None:
    """把版面 CSS 放進頁面。每次重跑都要呼叫（Streamlit 每次都會重畫整頁）。"""
    st.html(_CSS % {
        "map": KEY_MAP, "side": KEY_SIDE, "search": KEY_SEARCH, "sheet": KEY_SHEET,
        "loading": KEY_LOADING,
        "gap": CARD_GAP_PX, "card": CARD_WIDTH_PX, "mobile": MOBILE_MAX_WIDTH_PX,
        "mobile_plus": MOBILE_MAX_WIDTH_PX + 1,
    })
