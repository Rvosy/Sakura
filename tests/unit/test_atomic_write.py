"""tests/unit/test_atomic_write.py — 原子写入测试。

覆盖：
- 正常写入与覆盖写入
- backup 滚动保留上一版本
- 写入中途失败时旧文件完好、无临时文件残留（崩溃模拟）
- save_yaml_mapping 集成（写后可读回 + 产生 .bak）
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from app.config.yaml_config import load_yaml_mapping, save_yaml_mapping
import app.storage.atomic as atomic_module
from app.storage.atomic import atomic_write_text, rename_with_retry, replace_with_retry


_TEST_TEMP_ROOT = Path(__file__).resolve().parents[2] / "temp" / "test_atomic_write"


class _RetryableWinOSError(OSError):
    def __init__(self, winerror: int) -> None:
        super().__init__(f"winerror {winerror}")
        self.winerror = winerror


def _make_test_dir(name: str) -> Path:
    """创建继承仓库 ACL 的唯一测试目录，避免 tempfile 在 Windows 沙箱中丢权限。"""
    path = _TEST_TEMP_ROOT / f"{name}_{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    return path


def _no_tmp_leftovers(directory: Path) -> bool:
    return not any(p.suffix == ".tmp" for p in directory.iterdir())


class TestAtomicWriteText:
    def test_creates_file_and_parent_dirs(self) -> None:
        root = _make_test_dir("create")
        target = root / "nested" / "config.yaml"
        atomic_write_text(target, "hello")
        assert target.read_text(encoding="utf-8") == "hello"
        assert _no_tmp_leftovers(target.parent)

    def test_overwrites_existing(self) -> None:
        root = _make_test_dir("overwrite")
        target = root / "config.yaml"
        atomic_write_text(target, "v1")
        atomic_write_text(target, "v2")
        assert target.read_text(encoding="utf-8") == "v2"
        assert _no_tmp_leftovers(root)

    def test_backup_keeps_previous_version(self) -> None:
        root = _make_test_dir("backup")
        target = root / "config.yaml"
        atomic_write_text(target, "v1", backup=True)
        # 首次写入没有旧版本，不应产生 .bak
        assert not (root / "config.yaml.bak").exists()
        atomic_write_text(target, "v2", backup=True)
        assert target.read_text(encoding="utf-8") == "v2"
        assert (root / "config.yaml.bak").read_text(encoding="utf-8") == "v1"
        atomic_write_text(target, "v3", backup=True)
        assert (root / "config.yaml.bak").read_text(encoding="utf-8") == "v2"

    def test_no_backup_by_default(self) -> None:
        root = _make_test_dir("no_backup")
        target = root / "config.yaml"
        atomic_write_text(target, "v1")
        atomic_write_text(target, "v2")
        assert not (root / "config.yaml.bak").exists()

    def test_failure_preserves_original_and_cleans_tmp(self, monkeypatch: pytest.MonkeyPatch) -> None:
        root = _make_test_dir("crash")
        target = root / "config.yaml"
        atomic_write_text(target, "original")

        def broken_replace(src: str, dst: str) -> None:
            raise OSError("simulated crash before replace")

        monkeypatch.setattr(os, "replace", broken_replace)
        with pytest.raises(OSError, match="simulated crash"):
            atomic_write_text(target, "new content")
        monkeypatch.undo()

        # 旧文件保持原样，临时文件不残留
        assert target.read_text(encoding="utf-8") == "original"
        assert _no_tmp_leftovers(root)

    def test_unicode_content(self) -> None:
        root = _make_test_dir("unicode")
        target = root / "config.yaml"
        atomic_write_text(target, "角色: 桜\nメッセージ: こんにちは")
        assert "桜" in target.read_text(encoding="utf-8")


class TestRetryHelpers:
    def test_rename_with_retry_retries_transient_windows_lock(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root = _make_test_dir("rename_retry")
        source = root / "source.txt"
        target = root / "target.txt"
        source.write_text("ok", encoding="utf-8")
        original_rename = Path.rename
        calls = 0

        def flaky_rename(self: Path, target_path: Path) -> Path:
            nonlocal calls
            calls += 1
            if calls < 3:
                raise _RetryableWinOSError(32)
            return original_rename(self, target_path)

        monkeypatch.setattr(atomic_module.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(Path, "rename", flaky_rename)

        rename_with_retry(source, target)

        assert calls == 3
        assert target.read_text(encoding="utf-8") == "ok"

    def test_replace_with_retry_retries_transient_windows_lock(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root = _make_test_dir("replace_retry")
        source = root / "source.txt"
        target = root / "target.txt"
        source.write_text("new", encoding="utf-8")
        target.write_text("old", encoding="utf-8")
        original_replace = os.replace
        calls = 0

        def flaky_replace(source_path: Path, target_path: Path) -> None:
            nonlocal calls
            calls += 1
            if calls < 2:
                raise _RetryableWinOSError(5)
            original_replace(source_path, target_path)

        monkeypatch.setattr(atomic_module.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(os, "replace", flaky_replace)

        replace_with_retry(source, target)

        assert calls == 2
        assert target.read_text(encoding="utf-8") == "new"
        assert not source.exists()

    def test_replace_with_retry_preserves_non_retryable_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root = _make_test_dir("replace_non_retry")
        source = root / "source.txt"
        target = root / "target.txt"
        source.write_text("new", encoding="utf-8")
        target.write_text("old", encoding="utf-8")
        calls = 0

        def broken_replace(_source_path: Path, _target_path: Path) -> None:
            nonlocal calls
            calls += 1
            raise OSError("not retryable")

        monkeypatch.setattr(os, "replace", broken_replace)

        with pytest.raises(OSError, match="not retryable"):
            replace_with_retry(source, target)

        assert calls == 1
        assert target.read_text(encoding="utf-8") == "old"


class TestSaveYamlMappingAtomic:
    def test_roundtrip_and_backup(self) -> None:
        root = _make_test_dir("yaml")
        target = root / "api.yaml"
        save_yaml_mapping(target, {"llm": {"model": "m1"}})
        save_yaml_mapping(target, {"llm": {"model": "m2"}})
        assert load_yaml_mapping(target) == {"llm": {"model": "m2"}}
        backup = root / "api.yaml.bak"
        assert backup.exists()
        assert load_yaml_mapping(backup) == {"llm": {"model": "m1"}}
