from __future__ import annotations

import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ArchiveLimits:
    max_members: int = 4096
    max_member_bytes: int = 2 * 1024 * 1024 * 1024
    max_total_bytes: int = 8 * 1024 * 1024 * 1024
    max_compression_ratio: float = 200.0
    disk_reserve_bytes: int = 512 * 1024 * 1024


DEFAULT_ARCHIVE_LIMITS = ArchiveLimits()


def validate_zip_resource_limits(
    zf: zipfile.ZipFile,
    *,
    destination: Path,
    label: str,
    limits: ArchiveLimits = DEFAULT_ARCHIVE_LIMITS,
) -> int:
    members = zf.infolist()
    if len(members) > limits.max_members:
        raise ValueError(f"{label}文件数量过多：{len(members)} > {limits.max_members}。")

    total_size = 0
    for info in members:
        if info.is_dir():
            continue
        if info.file_size < 0 or info.compress_size < 0:
            raise ValueError(f"{label}包含无效 ZIP 元数据：{info.filename}。")
        if info.file_size > limits.max_member_bytes:
            raise ValueError(f"{label}单个文件过大：{info.filename}。")
        total_size += info.file_size
        if total_size > limits.max_total_bytes:
            raise ValueError(f"{label}展开后总大小超过限制。")
        if info.file_size > 1024 * 1024:
            ratio = info.file_size / max(1, info.compress_size)
            if ratio > limits.max_compression_ratio:
                raise ValueError(f"{label}压缩比异常：{info.filename}。")

    disk_root = _existing_parent(Path(destination))
    try:
        free_bytes = shutil.disk_usage(disk_root).free
    except OSError as exc:
        raise ValueError(f"无法确认{label}目标磁盘剩余空间：{exc}") from exc
    required = total_size + limits.disk_reserve_bytes
    if free_bytes < required:
        raise ValueError(
            f"{label}目标磁盘空间不足：需要至少 {required} 字节，当前可用 {free_bytes} 字节。"
        )
    return total_size


def _existing_parent(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate
