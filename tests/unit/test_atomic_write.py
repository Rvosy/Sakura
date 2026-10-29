"""保存失败保留原文件，备份可恢复，Windows 短暂文件锁可重试。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.config.yaml_config import load_yaml_mapping, save_yaml_mapping
import app.storage.atomic as atomic_module
from app.storage.atomic import atomic_write_text, rename_with_retry, replace_with_retry


class _RetryableWinOSError(OSError):
    def __init__(self, winerror: int) -> None:
        super().__init__(f"winerror {winerror}")
        self.winerror = winerror


def _no_tmp_leftovers(directory: Path) -> bool:
    return not any(p.suffix == ".tmp" for p in directory.iterdir())


class TestAtomicWriteText:
    def test_backup_keeps_previous_version(self, tmp_path: Path) -> None:
        target = tmp_path / "config.yaml"
        atomic_write_text(target, "v1", backup=True)
        # 首次写入没有旧版本，不应产生 .bak
        assert not (tmp_path / "config.yaml.bak").exists()
        atomic_write_text(target, "v2", backup=True)
        assert target.read_text(encoding="utf-8") == "v2"
        assert (tmp_path / "config.yaml.bak").read_text(encoding="utf-8") == "v1"
        atomic_write_text(target, "v3", backup=True)
        assert (tmp_path / "config.yaml.bak").read_text(encoding="utf-8") == "v2"

    def test_failure_preserves_original_and_cleans_tmp(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = tmp_path / "config.yaml"
        atomic_write_text(target, "original")

        def broken_replace(src: str, dst: str) -> None:
            raise OSError("simulated crash before replace")

        monkeypatch.setattr(os, "replace", broken_replace)
        with pytest.raises(OSError, match="simulated crash"):
            atomic_write_text(target, "new content")
        monkeypatch.undo()

        # 旧文件保持原样，临时文件不残留
        assert target.read_text(encoding="utf-8") == "original"
        assert _no_tmp_leftovers(tmp_path)


class TestRetryHelpers:
    def test_rename_with_retry_retries_transient_windows_lock(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source = tmp_path / "source.txt"
        target = tmp_path / "target.txt"
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

        assert target.read_text(encoding="utf-8") == "ok"

    def test_replace_with_retry_retries_transient_windows_lock(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source = tmp_path / "source.txt"
        target = tmp_path / "target.txt"
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

        assert target.read_text(encoding="utf-8") == "new"
        assert not source.exists()


class TestSaveYamlMappingAtomic:
    def test_roundtrip_and_backup(self, tmp_path: Path) -> None:
        target = tmp_path / "api.yaml"
        save_yaml_mapping(target, {"llm": {"model": "m1"}})
        save_yaml_mapping(target, {"llm": {"model": "m2"}})
        assert load_yaml_mapping(target) == {"llm": {"model": "m2"}}
        backup = tmp_path / "api.yaml.bak"
        assert backup.exists()
        assert load_yaml_mapping(backup) == {"llm": {"model": "m1"}}
