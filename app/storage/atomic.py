"""app/storage/atomic.py — 原子文件写入。

先写同目录临时文件、fsync 后用 os.replace 替换目标，避免写一半
（断电/磁盘满/进程被杀）留下损坏的半成品配置。os.replace 在同一
卷上是原子操作（Windows / POSIX 均成立），临时文件与目标同目录
即可保证同卷。
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

from app.core.debug_log import debug_log

# 备份文件后缀；与 .gitignore 中的 *.bak 约定一致，不会进入版本库
BACKUP_SUFFIX = ".bak"


def atomic_write_text(
    path: Path,
    text: str,
    *,
    encoding: str = "utf-8",
    backup: bool = False,
) -> None:
    """原子写文本文件。

    backup=True 且目标已存在时，先把旧内容复制为同目录 <名字>.bak
    （滚动覆盖，始终保留上一版本）。备份失败只记日志不阻断写入——
    备份是增强保护，不能反过来让正常保存失败。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if backup and path.exists():
        backup_path = path.with_name(path.name + BACKUP_SUFFIX)
        try:
            backup_path.write_bytes(path.read_bytes())
        except OSError as exc:
            debug_log(
                "Storage",
                "写入前备份失败，继续保存",
                {"path": str(path), "backup": str(backup_path), "error": str(exc)},
            )

    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        # newline 使用默认平台翻译，与原先 Path.write_text 的行为保持一致
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        replace_with_retry(Path(tmp_name), path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# Windows 上文件/目录刚创建完就改名，可能撞上杀毒或索引器的瞬时句柄：
# WinError 5（拒绝访问）/ WinError 32（共享冲突）。短退避重试即可恢复。
_RETRYABLE_WINERRORS = {5, 32}
_RENAME_RETRY_ATTEMPTS = 5
_RENAME_RETRY_INITIAL_DELAY_SECONDS = 0.05


def rename_with_retry(source: Path, target: Path) -> None:
    """改名文件/目录；对 Windows 瞬时锁定做指数退避重试，其余错误原样抛出。"""
    source = Path(source)
    for attempt in range(_RENAME_RETRY_ATTEMPTS):
        try:
            source.rename(target)
            return
        except OSError as exc:
            winerror = getattr(exc, "winerror", None)
            if winerror not in _RETRYABLE_WINERRORS or attempt == _RENAME_RETRY_ATTEMPTS - 1:
                raise
            delay = _RENAME_RETRY_INITIAL_DELAY_SECONDS * (2**attempt)
            debug_log(
                "Storage",
                "改名被瞬时锁定，准备重试",
                {
                    "source": str(source),
                    "target": str(target),
                    "winerror": winerror,
                    "attempt": attempt + 1,
                    "delay_seconds": delay,
                },
            )
            time.sleep(delay)


def replace_with_retry(source: Path, target: Path) -> None:
    """原子替换文件；对 Windows 瞬时锁定做指数退避重试。"""
    source = Path(source)
    target = Path(target)
    for attempt in range(_RENAME_RETRY_ATTEMPTS):
        try:
            os.replace(source, target)
            return
        except OSError as exc:
            winerror = getattr(exc, "winerror", None)
            if winerror not in _RETRYABLE_WINERRORS or attempt == _RENAME_RETRY_ATTEMPTS - 1:
                raise
            delay = _RENAME_RETRY_INITIAL_DELAY_SECONDS * (2**attempt)
            debug_log(
                "Storage",
                "替换文件被瞬时锁定，准备重试",
                {
                    "source": str(source),
                    "target": str(target),
                    "winerror": winerror,
                    "attempt": attempt + 1,
                    "delay_seconds": delay,
                },
            )
            time.sleep(delay)
