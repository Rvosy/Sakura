"""tests/unit/test_cleanup_tool.py — 清理工具测试。

覆盖：
- dry-run 不删除任何文件（默认行为的硬约束）
- apply 只删除白名单项
- 用户数据（角色卡/历史/配置/记忆/笔记/语音包）绝不进入清理列表
- 迁移备份按保留天数过滤
- 孤儿字节码只报告不删除
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

from tools.cleanup import (
    CleanupItem,
    find_orphan_bytecode,
    run_cleanup,
)
from app.storage.paths import StoragePaths


_TEST_TEMP_ROOT = Path(__file__).resolve().parents[2] / "temp" / "test_cleanup_tool"


def _make_base(name: str) -> Path:
    path = _TEST_TEMP_ROOT / f"{name}_{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    return path


def _build_populated_install(base: Path) -> dict[str, Path]:
    """构造一个带用户数据和可清理垃圾的安装目录。"""
    paths = StoragePaths(base)
    refs: dict[str, Path] = {}

    # 可清理垃圾
    paths.tts_cache_dir.mkdir(parents=True)
    refs["tts_wav"] = paths.tts_cache_dir / "stale.wav"
    refs["tts_wav"].write_bytes(b"RIFF" + b"\x00" * 64)

    old_backup = paths.migration_backup_dir / "20250101-000000_v0_to_v1"
    old_backup.mkdir(parents=True)
    (old_backup / ".env").write_text("OLD=1", encoding="utf-8")
    stale_time = time.time() - 60 * 86400
    os.utime(old_backup, (stale_time, stale_time))
    refs["old_backup"] = old_backup

    fresh_backup = paths.migration_backup_dir / "20991231-000000_v1_to_v2"
    fresh_backup.mkdir(parents=True)
    refs["fresh_backup"] = fresh_backup

    bundle_tmp = paths.tts_bundles_dir / "tmp"
    bundle_tmp.mkdir(parents=True)
    (bundle_tmp / "half.zip").write_bytes(b"x" * 10)
    refs["bundle_tmp"] = bundle_tmp

    pycache = base / "app" / "voice" / "__pycache__"
    pycache.mkdir(parents=True)
    (pycache / "tts.cpython-313.pyc").write_bytes(b"pyc")
    refs["pycache"] = pycache

    # 用户数据（绝不触碰）
    history = paths.chat_history_for("sakura")
    history.parent.mkdir(parents=True)
    history.write_text("{}\n", encoding="utf-8")
    refs["history"] = history

    character = base / "characters" / "sakura" / "character.json"
    character.parent.mkdir(parents=True)
    character.write_text("{}", encoding="utf-8")
    refs["character"] = character

    config = paths.api_config()
    config.parent.mkdir(parents=True)
    config.write_text("llm: {}\n", encoding="utf-8")
    refs["config"] = config

    memory = paths.memory_dir / "qdrant" / "collection.bin"
    memory.parent.mkdir(parents=True)
    memory.write_bytes(b"data")
    refs["memory"] = memory

    note = paths.notes_dir / "diary.txt"
    note.parent.mkdir(parents=True)
    note.write_text("日记", encoding="utf-8")
    refs["note"] = note

    voice = base / "characters" / "sakura" / "voice" / "ref.ogg"
    voice.parent.mkdir(parents=True)
    voice.write_bytes(b"OggS")
    refs["voice"] = voice
    return refs


class TestDryRun:
    def test_dry_run_deletes_nothing(self) -> None:
        base = _make_base("dryrun")
        refs = _build_populated_install(base)
        lines: list[str] = []
        items = run_cleanup(base, apply=False, out=lines.append)
        assert items  # 有可清理项
        for path in refs.values():
            assert path.exists(), f"dry-run 不应删除 {path}"
        assert any("dry-run" in line for line in lines)

    def test_listing_includes_categories(self) -> None:
        base = _make_base("listing")
        _build_populated_install(base)
        items = run_cleanup(base, apply=False, out=lambda *_: None)
        categories = {item.category for item in items}
        assert categories == {"tts_cache", "expired_backup", "bundle_leftover", "pycache"}


class TestApply:
    def test_apply_removes_only_whitelist(self) -> None:
        base = _make_base("apply")
        refs = _build_populated_install(base)
        run_cleanup(base, apply=True, out=lambda *_: None)

        # 垃圾被清
        assert not refs["tts_wav"].exists()
        assert not refs["old_backup"].exists()
        assert not refs["bundle_tmp"].exists()
        assert not refs["pycache"].exists()
        # 未过期备份保留
        assert refs["fresh_backup"].exists()
        # 用户数据全部健在
        for key in ("history", "character", "config", "memory", "note", "voice"):
            assert refs[key].exists(), f"用户数据被误删：{key}"

    def test_user_data_never_in_cleanup_list(self) -> None:
        base = _make_base("protect")
        refs = _build_populated_install(base)
        items = run_cleanup(base, apply=False, out=lambda *_: None)
        protected = {refs[k] for k in ("history", "character", "config", "memory", "note", "voice")}
        listed = {item.path for item in items}
        for path in protected:
            for listed_path in listed:
                assert not str(path).startswith(str(listed_path)), (
                    f"保护数据 {path} 落入清理目录 {listed_path}"
                )

    def test_backup_retention_configurable(self) -> None:
        base = _make_base("retention")
        refs = _build_populated_install(base)
        # 保留期设为 90 天：60 天前的备份不应进入清理列表
        items = run_cleanup(base, apply=False, backup_retention_days=90, out=lambda *_: None)
        assert refs["old_backup"] not in {item.path for item in items}


class TestOrphanBytecode:
    def test_orphan_reported_not_deleted(self) -> None:
        base = _make_base("orphan")
        pycache = base / "app" / "legacy" / "__pycache__"
        pycache.mkdir(parents=True)
        orphan = pycache / "removed_module.cpython-313.pyc"
        orphan.write_bytes(b"pyc")
        # 同目录存在有源码的正常 pyc
        (base / "app" / "legacy" / "alive.py").write_text("", encoding="utf-8")
        (pycache / "alive.cpython-313.pyc").write_bytes(b"pyc")

        orphans = find_orphan_bytecode(base)
        assert orphan in orphans
        assert len(orphans) == 1

        lines: list[str] = []
        run_cleanup(base, apply=False, out=lines.append)
        assert any("orphan" in line for line in lines)
