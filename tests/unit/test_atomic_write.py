"""保存失败保留原文件，备份可恢复，Windows 短暂文件锁可重试。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.config.yaml_config import load_yaml_mapping, save_yaml_mapping
from app.storage import atomic
from app.storage.atomic import atomic_write_text, rename_with_retry, replace_with_retry


class _WindowsLockError(OSError):
    def __init__(self, winerror: int) -> None:
        super().__init__(f"winerror {winerror}")
        self.winerror = winerror


def test_create_and_overwrite_unicode_file_without_backup(tmp_path):
    target = tmp_path / "nested/config.yaml"
    atomic_write_text(target, "角色: 桜\nメッセージ: こんにちは")
    assert target.read_text(encoding="utf-8") == "角色: 桜\nメッセージ: こんにちは"

    atomic_write_text(target, "角色: 新角色")
    assert target.read_text(encoding="utf-8") == "角色: 新角色"
    assert list(target.parent.iterdir()) == [target]


def test_backup_keeps_previous_version(tmp_path):
    target = tmp_path / "config.yaml"
    backup = tmp_path / "config.yaml.bak"
    atomic_write_text(target, "v1", backup=True)
    assert not backup.exists()
    atomic_write_text(target, "v2", backup=True)
    assert target.read_text() == "v2"
    assert backup.read_text() == "v1"
    atomic_write_text(target, "v3", backup=True)
    assert target.read_text() == "v3"
    assert backup.read_text() == "v2"


def test_failed_commit_preserves_original_and_cleans_temporary_file(tmp_path, monkeypatch):
    target = tmp_path / "config.yaml"
    target.write_text("original", encoding="utf-8")

    def fail_replace(*_args):
        raise OSError("simulated crash before replace")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated crash"):
        atomic_write_text(target, "new content")
    assert target.read_text() == "original"
    assert list(tmp_path.iterdir()) == [target]


def test_rename_retries_transient_windows_lock(tmp_path, monkeypatch):
    source, target = tmp_path / "source.txt", tmp_path / "target.txt"
    source.write_text("ok", encoding="utf-8")
    rename = Path.rename
    attempts = 0

    def locked_then_rename(path, destination):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise _WindowsLockError(32)
        return rename(path, destination)

    monkeypatch.setattr(atomic.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(Path, "rename", locked_then_rename)
    rename_with_retry(source, target)
    assert target.read_text() == "ok"
    assert not source.exists()


def test_replace_retries_transient_windows_lock(tmp_path, monkeypatch):
    source, target = tmp_path / "source.txt", tmp_path / "target.txt"
    source.write_text("new", encoding="utf-8")
    target.write_text("old", encoding="utf-8")
    replace = os.replace
    attempts = 0

    def locked_then_replace(source_path, target_path):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _WindowsLockError(5)
        replace(source_path, target_path)

    monkeypatch.setattr(atomic.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(os, "replace", locked_then_replace)
    replace_with_retry(source, target)
    assert target.read_text() == "new"
    assert not source.exists()


def test_replace_preserves_non_retryable_error_and_files(tmp_path, monkeypatch):
    source, target = tmp_path / "source.txt", tmp_path / "target.txt"
    source.write_text("new", encoding="utf-8")
    target.write_text("old", encoding="utf-8")
    attempts = []

    def fail_replace(*_args):
        attempts.append(True)
        raise OSError("not retryable")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="not retryable"):
        replace_with_retry(source, target)
    assert len(attempts) == 1
    assert target.read_text() == "old"
    assert source.read_text() == "new"


def test_yaml_save_roundtrip_and_backup(tmp_path):
    target = tmp_path / "api.yaml"
    save_yaml_mapping(target, {"llm": {"model": "m1"}})
    save_yaml_mapping(target, {"llm": {"model": "m2"}})
    assert load_yaml_mapping(target) == {"llm": {"model": "m2"}}
    assert load_yaml_mapping(tmp_path / "api.yaml.bak") == {"llm": {"model": "m1"}}
