"""測試 .env 載入與設定取值。"""
from __future__ import annotations

from pathlib import Path

import pytest

from config import settings


@pytest.fixture
def env_file(tmp_path: Path) -> Path:
    p = tmp_path / ".env"
    p.write_text(
        "# 這是註解\n"
        "\n"
        'GEMINI_API_KEY="abc123"\n'
        "GOOGLE_MAPS_API_KEY=gmkey\n"
        "EMPTY_VAL=\n"
        "WITH_EQUALS=a=b=c\n",
        encoding="utf-8",
    )
    return p


def test_load_env_parses_values(env_file, monkeypatch):
    for k in ("GEMINI_API_KEY", "GOOGLE_MAPS_API_KEY", "EMPTY_VAL", "WITH_EQUALS"):
        monkeypatch.delenv(k, raising=False)
    settings.load_env(env_file, override=True)
    import os
    assert os.environ["GEMINI_API_KEY"] == "abc123"  # 去除引號
    assert os.environ["GOOGLE_MAPS_API_KEY"] == "gmkey"
    # 值內含 = 應完整保留
    assert os.environ["WITH_EQUALS"] == "a=b=c"


def test_existing_env_takes_priority_without_override(env_file, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "preset")
    settings.load_env(env_file, override=False)
    import os
    assert os.environ["GEMINI_API_KEY"] == "preset"


def test_get_treats_empty_as_unset(monkeypatch):
    monkeypatch.setenv("SOME_KEY", "")
    settings._loaded = True  # 略過自動載入
    assert settings.get("SOME_KEY", "fallback") == "fallback"


def test_gemini_model_default(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    settings._loaded = True
    assert settings.gemini_model() == "gemini-2.5-flash"


def test_load_env_missing_file_is_noop(tmp_path):
    settings.load_env(tmp_path / "nope.env", override=True)
    assert settings._loaded is True


def test_get_int_reads_valid_integer(monkeypatch):
    monkeypatch.setenv("SOME_INT", " 7 ")
    settings._loaded = True
    assert settings.get_int("SOME_INT", 3) == 7


@pytest.mark.parametrize("raw", ["", "abc", "1.5"])
def test_get_int_falls_back_on_unset_or_invalid(monkeypatch, raw):
    monkeypatch.setenv("SOME_INT", raw)
    settings._loaded = True
    assert settings.get_int("SOME_INT", 3) == 3


def test_get_int_falls_back_when_missing(monkeypatch):
    monkeypatch.delenv("SOME_INT", raising=False)
    settings._loaded = True
    assert settings.get_int("SOME_INT", 3) == 3


def test_get_int_enforces_minimum(monkeypatch):
    settings._loaded = True
    monkeypatch.setenv("SOME_INT", "0")
    assert settings.get_int("SOME_INT", 3, minimum=1) == 3
    monkeypatch.setenv("SOME_INT", "1")
    assert settings.get_int("SOME_INT", 3, minimum=1) == 1


def test_graph_cache_defaults(monkeypatch):
    monkeypatch.delenv("GRAPH_CACHE_TTL_SECONDS", raising=False)
    monkeypatch.delenv("GRAPH_CACHE_MAX_ENTRIES", raising=False)
    settings._loaded = True
    assert settings.graph_cache_ttl_seconds() == 600
    assert settings.graph_cache_max_entries() == 2


def test_graph_cache_settings_from_env(monkeypatch):
    monkeypatch.setenv("GRAPH_CACHE_TTL_SECONDS", "120")
    monkeypatch.setenv("GRAPH_CACHE_MAX_ENTRIES", "1")
    settings._loaded = True
    assert settings.graph_cache_ttl_seconds() == 120
    assert settings.graph_cache_max_entries() == 1


def test_graph_cache_ignores_nonpositive_values(monkeypatch):
    # 0 或負數會讓快取失去作用（或被 Streamlit 拒絕），退回預設值
    monkeypatch.setenv("GRAPH_CACHE_TTL_SECONDS", "0")
    monkeypatch.setenv("GRAPH_CACHE_MAX_ENTRIES", "-1")
    settings._loaded = True
    assert settings.graph_cache_ttl_seconds() == 600
    assert settings.graph_cache_max_entries() == 2


def test_graph_build_headroom_default(monkeypatch):
    monkeypatch.delenv("GRAPH_BUILD_HEADROOM_MB", raising=False)
    settings._loaded = True
    assert settings.graph_build_headroom_mb() == 2048


def test_graph_build_headroom_from_env(monkeypatch):
    settings._loaded = True
    monkeypatch.setenv("GRAPH_BUILD_HEADROOM_MB", "512")
    assert settings.graph_build_headroom_mb() == 512
    # 0 是合法值：代表停用建圖前的記憶體檢查
    monkeypatch.setenv("GRAPH_BUILD_HEADROOM_MB", "0")
    assert settings.graph_build_headroom_mb() == 0
    monkeypatch.setenv("GRAPH_BUILD_HEADROOM_MB", "-5")
    assert settings.graph_build_headroom_mb() == 2048


def test_placeholder_key_from_env_example_counts_as_unset(monkeypatch):
    # 把 .env.example 直接複製成 .env、沒有填金鑰時，不應該拿 "YOUR_KEY" 去呼叫 API
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "YOUR_KEY")
    monkeypatch.setenv("GEMINI_API_KEY", "YOUR_KEY")
    settings._loaded = True
    assert settings.google_maps_api_key() is None
    assert settings.gemini_api_key() is None
    assert settings.get("GOOGLE_MAPS_API_KEY", "fallback") == "fallback"
