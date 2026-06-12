"""tools/cleanup.py — Sakura 安全清理工具。

独立 CLI，默认 dry-run（只列出将删除的内容，不执行）：

    python tools/cleanup.py            # 预览
    python tools/cleanup.py --apply    # 实际清理

清理范围（白名单制，逐项列出后才删）：
1. TTS 音频缓存残留          data/cache/tts/*
2. 过期迁移备份              data/migration_backup/*（默认 30 天前，--backup-days 可调）
3. TTS 整合包安装半成品      data/tts_bundles/tmp/、.migrating/
4. Python 字节码缓存         app/ sdk/ plugins/ 下的 __pycache__（可再生）

附加报告（只报告、绝不删除）：
- 孤儿字节码：存在 .pyc 但对应 .py 已不存在 —— 旧版本覆盖升级残留的直接证据

绝不触碰：角色卡（characters/）、聊天记录、语音包、长期记忆、笔记、
用户配置（data/config/）、插件源码。
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.storage.paths import StoragePaths  # noqa: E402

DEFAULT_BACKUP_RETENTION_DAYS = 30
_PYCACHE_SCAN_ROOTS = ("app", "sdk", "plugins")


@dataclass(frozen=True)
class CleanupItem:
    category: str
    path: Path
    size_bytes: int


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for child in path.rglob("*"):
            if child.is_file():
                try:
                    total += child.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def collect_tts_cache(paths: StoragePaths) -> list[CleanupItem]:
    cache_dir = paths.tts_cache_dir
    if not cache_dir.is_dir():
        return []
    return [
        CleanupItem("tts_cache", entry, _file_size(entry))
        for entry in sorted(cache_dir.iterdir())
        if entry.is_file()
    ]


def collect_expired_backups(paths: StoragePaths, retention_days: int) -> list[CleanupItem]:
    backup_dir = paths.migration_backup_dir
    if not backup_dir.is_dir():
        return []
    cutoff = time.time() - retention_days * 86400
    items: list[CleanupItem] = []
    for entry in sorted(backup_dir.iterdir()):
        try:
            if entry.stat().st_mtime >= cutoff:
                continue
        except OSError:
            continue
        size = _dir_size(entry) if entry.is_dir() else _file_size(entry)
        items.append(CleanupItem("expired_backup", entry, size))
    return items


def collect_bundle_leftovers(base_dir: Path, paths: StoragePaths) -> list[CleanupItem]:
    """TTS 整合包安装/迁移的半成品目录（中断后残留，重装/重迁会重建）。"""
    items: list[CleanupItem] = []
    for candidate in (
        paths.tts_bundles_dir / "tmp",
        base_dir / ".migrating",
        base_dir / "tts" / ".migrating",
    ):
        if candidate.is_dir():
            items.append(CleanupItem("bundle_leftover", candidate, _dir_size(candidate)))
    return items


def collect_pycache(base_dir: Path) -> list[CleanupItem]:
    items: list[CleanupItem] = []
    for root_name in _PYCACHE_SCAN_ROOTS:
        root = base_dir / root_name
        if not root.is_dir():
            continue
        for cache_dir in sorted(root.rglob("__pycache__")):
            if cache_dir.is_dir():
                items.append(CleanupItem("pycache", cache_dir, _dir_size(cache_dir)))
    # 根目录自身的 __pycache__（main.py 的字节码）
    root_cache = base_dir / "__pycache__"
    if root_cache.is_dir():
        items.append(CleanupItem("pycache", root_cache, _dir_size(root_cache)))
    return items


def find_orphan_bytecode(base_dir: Path) -> list[Path]:
    """报告 .pyc 对应 .py 已消失的孤儿——旧版本覆盖升级残留的证据，只报告不删。"""
    orphans: list[Path] = []
    for root_name in _PYCACHE_SCAN_ROOTS:
        root = base_dir / root_name
        if not root.is_dir():
            continue
        for pyc in root.rglob("__pycache__/*.pyc"):
            module_name = pyc.name.split(".", 1)[0]
            source = pyc.parent.parent / f"{module_name}.py"
            if not source.exists():
                orphans.append(pyc)
    return sorted(orphans)


def run_cleanup(
    base_dir: Path,
    *,
    apply: bool,
    backup_retention_days: int = DEFAULT_BACKUP_RETENTION_DAYS,
    out=print,
) -> list[CleanupItem]:
    """执行（或预览）清理；返回纳入清理范围的条目列表。"""
    paths = StoragePaths(base_dir)
    items: list[CleanupItem] = [
        *collect_tts_cache(paths),
        *collect_expired_backups(paths, backup_retention_days),
        *collect_bundle_leftovers(base_dir, paths),
        *collect_pycache(base_dir),
    ]

    mode = "清理" if apply else "预览（dry-run，未删除任何文件；加 --apply 执行）"
    out(f"== Sakura 清理工具 · {mode} ==")
    out(f"目标目录：{base_dir}")
    if not items:
        out("没有可清理的内容。")
    total = 0
    for item in items:
        total += item.size_bytes
        out(f"  [{item.category}] {item.path}  ({item.size_bytes / 1024:.1f} KB)")
    out(f"合计 {len(items)} 项，约 {total / (1024 * 1024):.2f} MB")

    orphans = find_orphan_bytecode(base_dir)
    if orphans:
        out("")
        out("检测到孤儿字节码（对应源码已不存在，疑似旧版本覆盖升级残留，仅报告）：")
        for pyc in orphans:
            out(f"  [orphan] {pyc}")
        out("如确认升级完成且功能正常，可手动删除上述 __pycache__ 目录。")

    if not apply:
        return items

    for item in items:
        try:
            if item.path.is_dir():
                shutil.rmtree(item.path)
            else:
                item.path.unlink()
            out(f"已删除：{item.path}")
        except OSError as exc:
            out(f"删除失败（已跳过）：{item.path} — {exc}")
    return items


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 GBK，中文输出需要显式切 UTF-8
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, OSError):
        pass
    parser = argparse.ArgumentParser(description="Sakura 安全清理工具（默认 dry-run）")
    parser.add_argument("--apply", action="store_true", help="实际执行删除（默认只预览）")
    parser.add_argument(
        "--backup-days",
        type=int,
        default=DEFAULT_BACKUP_RETENTION_DAYS,
        help=f"迁移备份保留天数（默认 {DEFAULT_BACKUP_RETENTION_DAYS}）",
    )
    parser.add_argument("--base-dir", type=Path, default=REPO_ROOT, help="Sakura 安装目录")
    args = parser.parse_args(argv)
    run_cleanup(
        args.base_dir,
        apply=args.apply,
        backup_retention_days=args.backup_days,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
