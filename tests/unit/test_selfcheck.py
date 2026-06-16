"""tests/unit/test_selfcheck.py — 启动自检与单实例锁测试。

覆盖：
- 正常环境自检通过
- 数据目录不可创建（被同名文件占位）→ fatal
- 已存在配置文件只读 → warning
- qdrant 残留锁 → warning
- 磁盘空间不足 → warning（mock disk_usage）
- 单实例锁互斥与释放、崩溃残留可接管语义
"""

from __future__ import annotations

import os
import shutil
import stat
import uuid
from pathlib import Path

import pytest

from app.core.instance import SingleInstanceGuard
from app.core.selfcheck import (
    SEVERITY_FATAL,
    SEVERITY_WARNING,
    run_startup_self_check,
)
from app.storage.paths import StoragePaths


_TEST_TEMP_ROOT = Path(__file__).resolve().parents[2] / "temp" / "test_selfcheck"


def _make_test_dir(name: str) -> Path:
    """创建继承仓库 ACL 的唯一测试目录，避免 tempfile 在 Windows 沙箱中丢权限。"""
    path = _TEST_TEMP_ROOT / f"{name}_{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    return path


class TestRunStartupSelfCheck:
    def test_clean_environment_passes(self) -> None:
        base = _make_test_dir("clean")
        report = run_startup_self_check(base)
        assert report.ok
        assert not report.fatal_issues

    def test_data_dir_blocked_by_file_is_fatal(self) -> None:
        base = _make_test_dir("blocked")
        # data 位置被同名文件占住 → mkdir 失败 → 唯一的 fatal 场景
        (base / "data").write_text("not a directory", encoding="utf-8")
        report = run_startup_self_check(base)
        keys = [i.key for i in report.fatal_issues]
        assert "data_dir_not_writable" in keys
        assert report.fatal_message()

    def test_readonly_config_file_is_warning(self) -> None:
        base = _make_test_dir("readonly_config")
        paths = StoragePaths(base)
        paths.ensure_dirs()
        config = paths.api_config()
        config.write_text("llm: {}\n", encoding="utf-8")
        os.chmod(config, stat.S_IREAD)
        try:
            report = run_startup_self_check(base)
        finally:
            os.chmod(config, stat.S_IWRITE | stat.S_IREAD)
        assert not report.fatal_issues
        keys = [i.key for i in report.warning_issues]
        assert "config_file_not_accessible" in keys

    def test_qdrant_lock_present_is_warning(self) -> None:
        base = _make_test_dir("qdrant_lock")
        paths = StoragePaths(base)
        lock = paths.qdrant_lock()
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("", encoding="utf-8")
        report = run_startup_self_check(base)
        assert not report.fatal_issues
        assert "qdrant_lock_present" in [i.key for i in report.warning_issues]

    def test_low_disk_space_is_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        base = _make_test_dir("low_disk")
        fake_usage = shutil._ntuple_diskusage(total=10**9, used=10**9 - 1024, free=1024)  # type: ignore[attr-defined]
        monkeypatch.setattr("app.core.selfcheck.shutil.disk_usage", lambda _: fake_usage)
        report = run_startup_self_check(base)
        assert "disk_space_low" in [i.key for i in report.warning_issues]

    def test_severity_partition(self) -> None:
        base = _make_test_dir("partition")
        (base / "data").write_text("x", encoding="utf-8")
        report = run_startup_self_check(base)
        for issue in report.fatal_issues:
            assert issue.severity == SEVERITY_FATAL
        for issue in report.warning_issues:
            assert issue.severity == SEVERITY_WARNING


class TestSingleInstanceGuard:
    def test_acquire_and_mutual_exclusion(self) -> None:
        base = _make_test_dir("lock")
        first = SingleInstanceGuard(base)
        assert first.acquire()
        second = SingleInstanceGuard(base)
        # 同一锁文件（即使同进程的另一个 QLockFile 对象）必须互斥
        assert not second.acquire()
        assert "Sakura" in second.holder_description() or "进程" in second.holder_description()
        first.release()
        third = SingleInstanceGuard(base)
        assert third.acquire()
        third.release()

    def test_release_is_idempotent(self) -> None:
        base = _make_test_dir("lock_idem")
        guard = SingleInstanceGuard(base)
        assert guard.acquire()
        guard.release()
        guard.release()

    def test_lock_file_lives_under_data(self) -> None:
        base = _make_test_dir("lock_path")
        guard = SingleInstanceGuard(base)
        assert guard.acquire()
        assert StoragePaths(base).instance_lock().exists()
        guard.release()
