"""YouBike Open API 資料擷取模組。

負責呼叫各縣市公開 API，取得即時站點資料，並提供本地 JSON 快取以避免重複請求。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
import urllib3

from config import CITY_CONFIG, YOUBIKE_NATIONAL_URL

# 部分政府開放資料 API 的憑證有瑕疵（例如新北市 data.ntpc.gov.tw 缺 Subject Key
# Identifier），對應 city 會以 verify_ssl=False 連線。抑制相關警告以保持 CLI 乾淨。
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

CACHE_DIR = Path(__file__).parent / "cache"
DEFAULT_CACHE_TTL_SECONDS = 300  # 5 分鐘
# (連線逾時, 讀取逾時)；連線設短一點，伺服器無回應時可快速失敗、不卡住 UI。
REQUEST_TIMEOUT: tuple[int, int] = (5, 15)
MAX_ATTEMPTS = 2  # 對暫時性連線/逾時錯誤重試一次


class FetchError(RuntimeError):
    """API 擷取失敗。"""


def fetch_city_stations(
    city: str,
    use_cache: bool = True,
    cache_ttl: int = DEFAULT_CACHE_TTL_SECONDS,
    cache_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """擷取指定縣市的所有 YouBike 站點即時資料。

    Args:
        city: 縣市名稱（須存在於 CITY_CONFIG）。
        use_cache: 是否使用本地快取。
        cache_ttl: 快取有效秒數。
        cache_dir: 自訂快取目錄（測試用）。

    Returns:
        站點資料列表，每筆為原始 API 回傳的 dict。
    """
    if city not in CITY_CONFIG:
        raise ValueError(f"未知縣市: {city}")
    cfg = CITY_CONFIG[city]
    base = cache_dir or CACHE_DIR

    if cfg.get("source") == "youbike_national":
        return _fetch_from_national(city, cfg, use_cache, cache_ttl, base)

    api_url = cfg.get("api_url") or ""
    if not api_url or api_url.startswith("TODO"):
        raise FetchError(f"{city} 的 API URL 尚未設定")
    verify_ssl = cfg.get("verify_ssl", True)

    if use_cache:
        cached = _load_cache(city, cache_ttl, base)
        if cached is not None:
            return cached

    data = _request_json(city, api_url, verify_ssl)

    # 部分開放資料 API（如 CKAN data.gov.tw）會把站點清單包在巢狀 dict 裡，
    # 例如 {"result": {"records": [...]}}。以 data_path 設定取出實際清單。
    data_path = cfg.get("data_path")
    if data_path:
        data = _extract_nested(city, data, data_path)

    if not isinstance(data, list):
        raise FetchError(f"{city} API 回傳格式非 list: {type(data).__name__}")

    _save_cache(city, data, base)
    return data


NATIONAL_CACHE_KEY = "_youbike_national"


def _fetch_from_national(
    city: str, cfg: dict[str, Any], use_cache: bool, cache_ttl: int, base: Path
) -> list[dict[str, Any]]:
    """從 YouBike 官方全台站點資料取出指定縣市（以 area_code 篩選）。

    全台資料只抓一次並快取，同一區域內的多個縣市共用，不會重複下載。
    """
    area_code = str(cfg.get("area_code") or "")
    if not area_code:
        raise FetchError(f"{city} 尚未設定 area_code")

    if use_cache:
        cached = _load_cache(city, cache_ttl, base)
        if cached is not None:
            return cached

    national = _load_cache(NATIONAL_CACHE_KEY, cache_ttl, base) if use_cache else None
    if national is None:
        data = _request_json("YouBike 全台資料", YOUBIKE_NATIONAL_URL, True)
        if isinstance(data, dict):
            # 防禦：若官方日後改成包一層 {"retVal": [...]} 之類的格式
            data = next((v for v in data.values() if isinstance(v, list)), None)
        if not isinstance(data, list):
            raise FetchError("YouBike 全台資料格式非 list")
        national = data
        _save_cache(NATIONAL_CACHE_KEY, national, base)

    records = [
        r for r in national
        if isinstance(r, dict) and str(r.get("area_code", "")).upper() == area_code.upper()
    ]
    if not records:
        raise FetchError(
            f"{city} 在 YouBike 全台資料中查無站點（area_code={area_code}），已略過該市"
        )
    _save_cache(city, records, base)
    return records


def _request_json(city: str, api_url: str, verify_ssl: bool) -> Any:
    """GET 並回傳 JSON；對連線/逾時類暫時性錯誤重試，失敗時擲出精簡友善訊息。"""
    host = urlparse(api_url).hostname or api_url
    last_err: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = requests.get(api_url, timeout=REQUEST_TIMEOUT, verify=verify_ssl)
            resp.raise_for_status()
            return resp.json()
        except (requests.ConnectionError, requests.Timeout) as e:
            last_err = e  # 暫時性：連線逾時 / 連不上 → 重試
            continue
        except requests.RequestException as e:
            raise FetchError(f"{city} 擷取失敗（{host}）：{e}") from e

    if isinstance(last_err, requests.Timeout):
        raise FetchError(f"{city} 資料來源 {host} 連線逾時，已略過該市") from last_err
    raise FetchError(f"{city} 資料來源 {host} 連線失敗，已略過該市") from last_err


def _extract_nested(city: str, data: Any, path: list[str]) -> Any:
    """依 path 逐層深入巢狀 dict，取出實際的站點清單。"""
    cur = data
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            raise FetchError(
                f"{city} API 回傳缺少預期欄位 '{key}'（data_path={path}）"
            )
        cur = cur[key]
    return cur


def _cache_path(city: str, base: Path) -> Path:
    return base / f"{city}.json"


def _load_cache(city: str, ttl: int, base: Path) -> list[dict[str, Any]] | None:
    """讀取快取，若過期或不存在則回傳 None。"""
    path = _cache_path(city, base)
    if not path.exists():
        return None
    age = time.time() - path.stat().st_mtime
    if age > ttl:
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _save_cache(city: str, data: list[dict[str, Any]], base: Path) -> None:
    """將擷取結果寫入 JSON 快取檔。"""
    base.mkdir(parents=True, exist_ok=True)
    path = _cache_path(city, base)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def fetch_all_cities(
    cities: list[str],
    use_cache: bool = True,
    cache_ttl: int = DEFAULT_CACHE_TTL_SECONDS,
) -> dict[str, list[dict[str, Any]]]:
    """批次擷取多個縣市的站點資料。擷取失敗的城市會被略過並印出警告。"""
    result: dict[str, list[dict[str, Any]]] = {}
    for city in cities:
        try:
            result[city] = fetch_city_stations(city, use_cache, cache_ttl)
        except (FetchError, ValueError) as e:
            print(f"[警告] 略過 {city}: {e}")
    return result
