"""載入進度卡：抓站點、建站點圖、規劃路線時，在地圖上方顯示「跑到哪一步、幾成」。

Streamlit 在執行耗時的函式時整個腳本會停住，畫面不會更新。為了讓進度條會動，
耗時的工作放進背景執行緒（run_with_ticks），畫面這邊每隔一小段時間更新一次進度卡。
背景執行緒帶著目前頁面的 Streamlit context，裡面的 st.warning 等訊息照樣顯示在原處。
"""
from __future__ import annotations

import contextvars
import html
import threading
import time
from typing import Callable, TypeVar

import streamlit as st
from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx

T = TypeVar("T")

# 工作超過這個秒數才顯示進度卡，避免快取命中時進度卡一閃而過
SHOW_AFTER_SEC = 0.4
# 進度卡多久更新一次
TICK_SEC = 0.25


def loading_card_html(
    steps: list[str],
    current: int,
    fraction: float | None = None,
    elapsed_sec: float = 0.0,
    note: str = "",
) -> str:
    """進度卡的 HTML。

    Args:
        steps: 每一步的名稱。
        current: 正在跑第幾步（從 0 算）。
        fraction: 這一步完成了幾成（0~1）；不知道時給 None，進度條改成來回跑的動畫。
        elapsed_sec: 這一步已經跑了幾秒。
        note: 補充說明（例如「第一次比較久」）。
    """
    n = max(len(steps), 1)
    frac = 0.0 if fraction is None else min(max(fraction, 0.0), 1.0)
    overall = (current + frac) / n
    items = []
    for i, name in enumerate(steps):
        label = html.escape(name)
        if i < current:
            items.append(f'<li class="done">✓ {label}</li>')
        elif i == current:
            pct = f" {frac * 100:.0f}%" if fraction is not None else ""
            items.append(f'<li class="now">● {label}{pct}</li>')
        else:
            items.append(f'<li>○ {label}</li>')
    bar_cls = "yb-load-bar" + ("" if fraction is not None else " unknown")
    secs = f"{elapsed_sec:.0f} 秒" if elapsed_sec >= 1 else ""
    note_html = f'<div class="yb-load-note">{html.escape(note)}</div>' if note else ""
    return (
        '<div class="yb-load" role="status" aria-live="polite">'
        f'<div class="yb-load-title"><span class="yb-spin"></span>'
        f"載入中（{current + 1}/{n}）<span class=\"t\">{secs}</span></div>"
        f'<div class="{bar_cls}"><i style="width:{overall * 100:.1f}%"></i></div>'
        f'<ol class="yb-load-steps">{"".join(items)}</ol>'
        f"{note_html}</div>"
    )


class LoadingCard:
    """浮在地圖上的進度卡。用 show() 更新、clear() 收起來。"""

    def __init__(self, slot, steps: list[str]):
        self._slot = slot
        self.steps = steps

    def show(self, current: int, fraction: float | None = None,
             elapsed_sec: float = 0.0, note: str = "") -> None:
        self._slot.html(loading_card_html(self.steps, current, fraction, elapsed_sec, note))

    def clear(self) -> None:
        self._slot.empty()


def run_with_ticks(
    fn: Callable[[], T],
    on_tick: Callable[[float], None],
    show_after: float = SHOW_AFTER_SEC,
    tick: float = TICK_SEC,
) -> T:
    """在背景執行緒跑 fn()，跑的期間每 tick 秒呼叫一次 on_tick(已經過秒數)。

    fn 很快就跑完（例如快取命中）時完全不呼叫 on_tick，畫面不會閃。
    fn 丟出的例外會在這裡原樣丟出。
    """
    out: dict = {}
    ctx = contextvars.copy_context()  # 讓背景執行緒也寫進同一個畫面位置（例如搜尋卡）

    def target() -> None:
        try:
            out["value"] = ctx.run(fn)
        except BaseException as e:  # noqa: BLE001 — 原樣交回主執行緒
            out["error"] = e

    worker = threading.Thread(target=target, name="yb-loading", daemon=True)
    script_ctx = get_script_run_ctx()
    if script_ctx is not None:
        add_script_run_ctx(worker, script_ctx)
    start = time.monotonic()
    worker.start()
    worker.join(show_after)
    while worker.is_alive():
        on_tick(time.monotonic() - start)
        worker.join(tick)
    if "error" in out:
        raise out["error"]
    return out["value"]


def new_loading_card(container_key: str, steps: list[str]) -> LoadingCard:
    """在指定 key 的容器裡建立進度卡（位置與外觀由 ui/layout.py 的 CSS 決定）。"""
    return LoadingCard(st.container(key=container_key).empty(), steps)
