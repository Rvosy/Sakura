from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.plugins.base import PluginContext


@dataclass(frozen=True)
class PluginHostContext:
    """旧版三参数 initialize 使用的宿主上下文。"""

    base_dir: Path
