from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


DEFAULT_DISK_SPACE_RESERVE_BYTES = 512 * 1024 * 1024


def build_disk_space_check(
    path: Path,
    required_bytes: int,
    *,
    reserve_bytes: int = DEFAULT_DISK_SPACE_RESERVE_BYTES,
) -> dict[str, Any]:
    required = max(0, int(required_bytes or 0))
    reserve = max(0, int(reserve_bytes or 0))
    root = _nearest_existing_path(path)
    try:
        usage = shutil.disk_usage(root)
    except OSError as exc:
        return {
            "ok": True,
            "unknown": True,
            "path": str(path),
            "checked_path": str(root),
            "required_bytes": required,
            "reserve_bytes": reserve,
            "available_bytes": 0,
            "message": f"无法检查磁盘空间：{exc}",
        }
    needed = required + reserve if required > 0 else 0
    ok = needed <= 0 or usage.free >= needed
    return {
        "ok": ok,
        "unknown": False,
        "path": str(path),
        "checked_path": str(root),
        "required_bytes": required,
        "reserve_bytes": reserve,
        "available_bytes": usage.free,
        "needed_bytes": needed,
        "message": "" if ok else "可用磁盘空间不足。",
    }


def format_bytes(value: int) -> str:
    size = float(max(0, int(value or 0)))
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


def _nearest_existing_path(path: Path) -> Path:
    candidate = Path(path).expanduser()
    if candidate.exists():
        return candidate
    for parent in (candidate.parent, *candidate.parents):
        if parent.exists():
            return parent
    return Path.cwd()
