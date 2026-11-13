"""測試 Gemini 錯誤訊息 → 排解提示的對應。"""
from __future__ import annotations

from ui.extras import _gemini_error_hint


def test_quota_429_hint():
    err = "HTTP 429：You exceeded your current quota ... limit: 0, model: gemini-2.5-flash"
    hint = _gemini_error_hint(err)
    assert "配額" in hint
    assert "免費額度" in hint


def test_invalid_key_hint():
    assert "金鑰無效" in _gemini_error_hint("HTTP 400：API key not valid. Please pass a valid API key.")


def test_model_not_found_hint():
    assert "模型名稱" in _gemini_error_hint("HTTP 404：models/gemini-x is not found")


def test_api_disabled_hint():
    err = "HTTP 403：Generative Language API has not been used in project 123 before or it is disabled"
    assert "啟用 Generative Language API" in _gemini_error_hint(err)


def test_generic_hint_fallback():
    assert "常見原因" in _gemini_error_hint("某種未知錯誤")
