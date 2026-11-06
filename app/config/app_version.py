"""读取发行目录中的应用版本。"""

from __future__ import annotations

from pathlib import Path


def read_app_version(base_dir: Path) -> str:
    """读取 VERSION 文件第一行；缺失或不可读返回空串。"""
    try:
        text = (Path(base_dir) / "VERSION").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""
    first_line = text.splitlines()[0].strip() if text.strip() else ""
    return first_line.lstrip("v")
