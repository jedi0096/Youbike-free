"""建站點圖之前的記憶體檢查（自己架設、主機記憶體有限時的保險）。

一張站點圖依側欄設定約佔 0.4~2 GB。Streamlit 快取的 max_entries 只限制「張數」，
而且是新圖建好之後才淘汰舊圖，所以在記憶體有上限的主機上（例如 docker compose 的
mem_limit），連續換幾次設定就可能新舊圖並存而超過上限，整個 App 被系統強制結束。

這個模組只負責回答「現在還剩多少記憶體」；要不要先清掉舊圖由呼叫端（app.py）決定。
讀不到資訊的環境（Windows / macOS 直接執行）一律視為「無法判斷」，不做任何事。
"""
from __future__ import annotations

import threading
from pathlib import Path

_MB = 1024 * 1024

# cgroup v2（新版 Docker / Docker Desktop）與 v1 的記憶體資訊位置
_CGROUP_V2 = Path("/sys/fs/cgroup")
_CGROUP_V1 = Path("/sys/fs/cgroup/memory")
_MEMINFO = Path("/proc/meminfo")

# cgroup v1 沒設上限時，limit_in_bytes 會是一個接近 2^63 的數字
_NO_LIMIT = 1 << 60

# 同一時間只建一張圖：兩張大圖同時建，需要的記憶體會加倍。
# 放在模組層級（而不是 app.py）是因為 Streamlit 每次互動都會重跑 app.py，
# 只有被 import 的模組才會在整個程式存活期間保持同一份。
BUILD_LOCK = threading.Lock()


def _read_int(path: Path) -> int | None:
    """讀取只有一個整數的檔案；不存在、讀不到或不是整數（如 "max"）回傳 None。"""
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _read_keyed_int(path: Path, key: str) -> int | None:
    """從「名稱 數值」逐行格式的檔案（memory.stat、/proc/meminfo）取出某一項。"""
    try:
        for line in path.read_text().splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].rstrip(":") == key:
                return int(parts[1])
    except (OSError, ValueError):
        pass
    return None


def _cgroup_free_bytes(v2: Path = _CGROUP_V2, v1: Path = _CGROUP_V1) -> int | None:
    """容器的記憶體上限減去目前用量；沒有設上限或讀不到時回傳 None。

    用量會扣掉 inactive_file（隨時可以釋放的檔案快取），算法與 `docker stats` 相同。
    """
    candidates = (
        (v2 / "memory.max", v2 / "memory.current", v2 / "memory.stat", "inactive_file"),
        (v1 / "memory.limit_in_bytes", v1 / "memory.usage_in_bytes",
         v1 / "memory.stat", "total_inactive_file"),
    )
    for limit_path, usage_path, stat_path, inactive_key in candidates:
        limit = _read_int(limit_path)
        usage = _read_int(usage_path)
        if limit is None or usage is None or limit <= 0 or limit >= _NO_LIMIT:
            continue
        reclaimable = _read_keyed_int(stat_path, inactive_key) or 0
        return limit - max(usage - reclaimable, 0)
    return None


def _system_available_bytes(meminfo: Path = _MEMINFO) -> int | None:
    """整台主機（或虛擬機）還能用的記憶體；讀不到時回傳 None。"""
    kb = _read_keyed_int(meminfo, "MemAvailable")
    return None if kb is None else kb * 1024


def available_memory_mb() -> float | None:
    """這個程式還能再用多少記憶體（MB）；無法判斷時回傳 None。

    同時看「容器上限還剩多少」與「主機還剩多少」，取較小者。
    """
    known = [v for v in (_cgroup_free_bytes(), _system_available_bytes()) if v is not None]
    if not known:
        return None
    return min(known) / _MB


def needs_room(headroom_mb: int) -> bool:
    """剩餘記憶體是否少於 headroom_mb。headroom_mb <= 0（停用）或無法判斷時回傳 False。"""
    if headroom_mb <= 0:
        return False
    available = available_memory_mb()
    return available is not None and available < headroom_mb
