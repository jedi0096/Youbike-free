"""載入進度：建圖進度回報、背景執行與進度卡 HTML。"""
from __future__ import annotations

import time

import pandas as pd
import pytest

from core.cooldown import build_cooldown_graph
from core.graph_builder import build_station_graph
from ui.progress import loading_card_html, run_with_ticks


def _stations(n: int = 30) -> pd.DataFrame:
    rows = []
    for i in range(n):
        rows.append({
            "station_id": f"S{i}", "name": f"站{i}",
            "lat": 25.0 + 0.002 * (i % 6), "lon": 121.5 + 0.002 * (i // 6),
            "available_bikes": 5, "available_docks": 5, "city": "台北市",
        })
    return pd.DataFrame(rows)


@pytest.mark.parametrize("builder", [build_station_graph, build_cooldown_graph])
def test_builders_report_progress_from_zero_to_one(builder):
    seen: list[float] = []
    g = builder(_stations(), 30, 3, 12.0, 1.3, progress=seen.append)
    assert g.number_of_edges() > 0
    assert seen[0] == 0.0 and seen[-1] == 1.0
    assert seen == sorted(seen)
    assert all(0.0 <= f <= 1.0 for f in seen)


@pytest.mark.parametrize("builder", [build_station_graph, build_cooldown_graph])
def test_builders_progress_does_not_change_graph(builder):
    a = builder(_stations(), 30, 3, 12.0, 1.3)
    b = builder(_stations(), 30, 3, 12.0, 1.3, progress=lambda f: None)
    assert sorted(a.edges(data=True)) == sorted(b.edges(data=True))


def test_run_with_ticks_fast_call_never_ticks():
    ticks: list[float] = []
    assert run_with_ticks(lambda: 42, ticks.append, show_after=0.5) == 42
    assert ticks == []


def test_run_with_ticks_slow_call_ticks_and_returns():
    ticks: list[float] = []

    def slow():
        time.sleep(0.35)
        return "done"

    assert run_with_ticks(slow, ticks.append, show_after=0.05, tick=0.05) == "done"
    assert len(ticks) >= 2
    assert ticks == sorted(ticks)


def test_run_with_ticks_reraises_errors():
    def boom():
        raise ValueError("壞掉了")

    with pytest.raises(ValueError, match="壞掉了"):
        run_with_ticks(boom, lambda t: None)


def test_loading_card_marks_steps_and_percent():
    html = loading_card_html(["抓站點", "建連線", "找路線"], 1, 0.5, elapsed_sec=3.2, note="說明")
    assert "載入中（2/3）" in html
    assert "✓ 抓站點" in html
    assert "● 建連線 50%" in html
    assert "○ 找路線" in html
    assert "3 秒" in html and "說明" in html
    # 整體進度 = (1 + 0.5) / 3
    assert "width:50.0%" in html
    assert "unknown" not in html


def test_loading_card_unknown_fraction_animates_and_escapes():
    html = loading_card_html(["<b>站</b>"], 0, None, elapsed_sec=0.2)
    assert "yb-load-bar unknown" in html
    assert "&lt;b&gt;站&lt;/b&gt;" in html
    assert "秒" not in html  # 不到 1 秒不顯示秒數


def test_get_graph_reports_fetch_then_build_and_cleans_up(monkeypatch):
    import ui.cache as cache

    phases: list = []
    key = cache.graph_key(("台北市",), False, 3, False, True, False, 99.0)

    def fake_load(cities):
        phases.append(cache.graph_build_progress(key))
        return _stations()

    def fake_build(df, progress=None, **kw):
        progress(0.5)
        phases.append(cache.graph_build_progress(key))
        return build_station_graph(df, **kw)

    monkeypatch.setattr(cache, "load_stations", fake_load)
    monkeypatch.setattr(cache, "build_station_graph", fake_build)
    cache.get_graph.clear()
    g, df = cache.get_graph(*key)
    assert g.number_of_edges() > 0 and len(df) == 30
    assert phases == [("fetch", None), ("build", 0.5)]
    # 準備好之後不留進度
    assert cache.graph_build_progress(key) is None
    cache.get_graph.clear()
