"""環境變數與 .env 載入。

集中管理 API 金鑰與功能開關。採用零相依的 .env 解析（不需 python-dotenv），
方便在無法安裝套件的環境也能運作。.env 已列入 .gitignore，金鑰不會被提交。
"""
from __future__ import annotations

import os
from pathlib import Path

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
_loaded = False

# .env.example 裡的佔位字。把範例檔複製成 .env 卻沒換成真的金鑰時，視為「未設定」，
# 否則 App 會拿這個假金鑰去呼叫 Google，每次規劃都白等一輪失敗。
_PLACEHOLDERS = frozenset({"YOUR_KEY"})


def load_env(path: Path | None = None, override: bool = False) -> None:
    """讀取 .env 檔並寫入 os.environ。

    Args:
        path: .env 路徑，預設為專案根目錄。
        override: True 時以 .env 覆蓋既有環境變數；預設 False（環境變數優先）。
    """
    global _loaded
    env_path = path or _ENV_PATH
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if not key:
                continue
            if override or key not in os.environ:
                os.environ[key] = val
    _loaded = True


def get(key: str, default: str | None = None) -> str | None:
    """取環境變數（首次呼叫會自動載入 .env）。空字串與範例檔的佔位字視為未設定。"""
    if not _loaded:
        load_env()
    val = os.environ.get(key)
    if val is None or val == "" or val in _PLACEHOLDERS:
        return default
    return val


def google_maps_api_key() -> str | None:
    """Google Maps Platform 金鑰（Distance Matrix / Directions）。"""
    return get("GOOGLE_MAPS_API_KEY")


def gemini_api_key() -> str | None:
    """Google Gemini 金鑰（AI 回饋語氣潤飾）。"""
    return get("GEMINI_API_KEY")


def gemini_model() -> str:
    """Gemini 模型名稱，預設用便宜快速的 flash。"""
    return get("GEMINI_MODEL", "gemini-2.5-flash") or "gemini-2.5-flash"


def get_int(key: str, default: int, minimum: int | None = None) -> int:
    """取整數型環境變數。未設定、不是整數，或小於 minimum 時回傳 default。"""
    raw = get(key)
    if raw is None:
        return default
    try:
        val = int(raw.strip())
    except ValueError:
        return default
    if minimum is not None and val < minimum:
        return default
    return val


def graph_cache_ttl_seconds() -> int:
    """站點圖快取的有效秒數。到期後下一次使用會以最新車況重建。"""
    return get_int("GRAPH_CACHE_TTL_SECONDS", 600, minimum=1)


def graph_cache_max_entries() -> int:
    """最多同時保留幾張站點圖（每種側欄設定組合一張）。記憶體充裕的主機可調大。"""
    return get_int("GRAPH_CACHE_MAX_ENTRIES", 2, minimum=1)


def graph_build_headroom_mb() -> int:
    """建新站點圖前至少要有多少可用記憶體（MB），不夠就先清掉舊圖。0 = 停用這項檢查。"""
    return get_int("GRAPH_BUILD_HEADROOM_MB", 2048, minimum=0)
