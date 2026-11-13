"""core.memory_guard：建站點圖前的記憶體檢查。"""
from pathlib import Path

import pytest

from core import memory_guard

GB = 1024 ** 3
MB = 1024 ** 2


def _write(directory: Path, **files: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (directory / name.replace("__", ".")).write_text(content)
    return directory


def test_cgroup_v2_free_is_limit_minus_working_set(tmp_path):
    v2 = _write(
        tmp_path / "v2",
        memory__max=f"{3 * GB}\n",
        memory__current=f"{1 * GB}\n",
        memory__stat=f"anon {900 * MB}\ninactive_file {100 * MB}\nactive_file 0\n",
    )
    # 用量要扣掉可回收的檔案快取：3 GB - (1 GB - 100 MB)
    assert memory_guard._cgroup_free_bytes(v2, tmp_path / "none") == 2 * GB + 100 * MB


def test_cgroup_v2_without_limit_is_unknown(tmp_path):
    v2 = _write(tmp_path / "v2", memory__max="max\n", memory__current=f"{GB}\n")
    assert memory_guard._cgroup_free_bytes(v2, tmp_path / "none") is None


def test_cgroup_v1_is_used_when_v2_is_absent(tmp_path):
    v1 = _write(
        tmp_path / "v1",
        memory__limit_in_bytes=f"{2 * GB}\n",
        memory__usage_in_bytes=f"{512 * MB}\n",
        memory__stat=f"cache 0\ntotal_inactive_file {12 * MB}\n",
    )
    assert memory_guard._cgroup_free_bytes(tmp_path / "none", v1) == 2 * GB - 500 * MB


def test_cgroup_v1_without_limit_is_unknown(tmp_path):
    v1 = _write(
        tmp_path / "v1",
        memory__limit_in_bytes="9223372036854771712\n",
        memory__usage_in_bytes=f"{GB}\n",
    )
    assert memory_guard._cgroup_free_bytes(tmp_path / "none", v1) is None


def test_cgroup_missing_is_unknown(tmp_path):
    assert memory_guard._cgroup_free_bytes(tmp_path / "a", tmp_path / "b") is None


def test_cgroup_stat_file_is_optional(tmp_path):
    v2 = _write(tmp_path / "v2", memory__max=f"{GB}\n", memory__current=f"{300 * MB}\n")
    assert memory_guard._cgroup_free_bytes(v2, tmp_path / "none") == GB - 300 * MB


def test_cgroup_usage_never_goes_negative(tmp_path):
    v2 = _write(
        tmp_path / "v2",
        memory__max=f"{GB}\n",
        memory__current=f"{10 * MB}\n",
        memory__stat=f"inactive_file {50 * MB}\n",
    )
    assert memory_guard._cgroup_free_bytes(v2, tmp_path / "none") == GB


def test_system_available_reads_meminfo(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        "MemTotal:       16000000 kB\nMemFree:         1000000 kB\n"
        "MemAvailable:    2097152 kB\n"
    )
    assert memory_guard._system_available_bytes(meminfo) == 2 * GB


def test_system_available_unknown_without_meminfo(tmp_path):
    assert memory_guard._system_available_bytes(tmp_path / "nope") is None


@pytest.mark.parametrize(
    "cgroup_free, system_free, expected_mb",
    [
        (3 * GB, 1 * GB, 1024.0),   # 主機比容器上限更緊 → 取主機
        (1 * GB, 8 * GB, 1024.0),   # 容器上限較緊 → 取容器
        (None, 2 * GB, 2048.0),     # 沒設容器上限
        (512 * MB, None, 512.0),
        (None, None, None),         # 例如直接在 Windows / macOS 上執行
    ],
)
def test_available_memory_takes_the_tighter_bound(
    monkeypatch, cgroup_free, system_free, expected_mb
):
    monkeypatch.setattr(memory_guard, "_cgroup_free_bytes", lambda: cgroup_free)
    monkeypatch.setattr(memory_guard, "_system_available_bytes", lambda: system_free)
    assert memory_guard.available_memory_mb() == expected_mb


@pytest.mark.parametrize(
    "available_mb, headroom_mb, expected",
    [
        (500.0, 2048, True),     # 不夠 → 要先清出空間
        (2048.0, 2048, False),   # 剛好夠
        (4096.0, 2048, False),
        (None, 2048, False),     # 無法判斷 → 不動作
    ],
)
def test_needs_room(monkeypatch, available_mb, headroom_mb, expected):
    monkeypatch.setattr(memory_guard, "available_memory_mb", lambda: available_mb)
    assert memory_guard.needs_room(headroom_mb) is expected


def test_needs_room_disabled_does_not_even_look(monkeypatch):
    def _boom():
        raise AssertionError("停用時不應該去讀記憶體資訊")

    monkeypatch.setattr(memory_guard, "available_memory_mb", _boom)
    assert memory_guard.needs_room(0) is False
    assert memory_guard.needs_room(-1) is False


def test_available_memory_on_this_machine_is_sane():
    # 真的讀一次本機：能判斷就必須是合理的數字，不能判斷就是 None，兩者都不該丟例外。
    mb = memory_guard.available_memory_mb()
    assert mb is None or mb > -1e6
