from __future__ import annotations

import os
from pathlib import Path
import pytest

from app.storage.atomic import atomic_write_text, rename_with_retry


def test_atomic_write_text_normal(tmp_path: Path) -> None:
    target = tmp_path / "subdir" / "test.txt"
    atomic_write_text(target, "hello world")
    assert target.read_text(encoding="utf-8") == "hello world"


def test_atomic_write_text_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "test.txt"
    target.write_text("old content", encoding="utf-8")
    atomic_write_text(target, "new content")
    assert target.read_text(encoding="utf-8") == "new content"


def test_atomic_write_text_backup(tmp_path: Path) -> None:
    target = tmp_path / "test.txt"
    target.write_text("old content", encoding="utf-8")
    atomic_write_text(target, "new content", backup=True)
    
    assert target.read_text(encoding="utf-8") == "new content"
    backup_file = tmp_path / "test.txt.bak"
    assert backup_file.read_text(encoding="utf-8") == "old content"


def test_atomic_write_text_failure_rollback(tmp_path: Path) -> None:
    target = tmp_path / "test.txt"
    target.write_text("surviving content", encoding="utf-8")
    
    # Passing non-string target to force TypeError inside os.fdopen or handle.write
    with pytest.raises(TypeError):
        atomic_write_text(target, 12345)  # type: ignore[arg-type]
        
    # The original file should remain unchanged and temporary file cleaned up
    assert target.read_text(encoding="utf-8") == "surviving content"
    # Ensure no lingering temp files
    temp_files = list(tmp_path.glob(".test.txt.*.tmp"))
    assert not temp_files


def test_rename_with_retry(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    target = tmp_path / "target.txt"
    source.write_text("content", encoding="utf-8")
    
    rename_with_retry(source, target)
    assert not source.exists()
    assert target.read_text(encoding="utf-8") == "content"
