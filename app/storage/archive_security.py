from __future__ import annotations

import shutil
import zipfile
from pathlib import Path


def validate_zip_resource_limits(
    zf: zipfile.ZipFile,
    *,
    destination: Path,
    label: str,
) -> int:
    total_size = sum(info.file_size for info in zf.infolist() if not info.is_dir())

    disk_root = _existing_parent(Path(destination))
    try:
        free_bytes = shutil.disk_usage(disk_root).free
    except OSError as exc:
        raise ValueError(f"无法确认{label}目标磁盘剩余空间：{exc}") from exc
    required = total_size + 512 * 1024 * 1024
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
