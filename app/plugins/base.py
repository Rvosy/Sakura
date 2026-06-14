"""Sakura 原生插件基类与上下文。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.plugins.capabilities import PluginCapabilityRegistry
from app.plugins.models import PluginEvent


@dataclass(frozen=True)
class PluginContext:
    """插件初始化时可读取的 Sakura 宿主上下文。"""

    base_dir: Path
    plugin_root: Path
    data_dir: Path
    manifest: Any

    def log(self, message: str, data: dict[str, Any] | None = None) -> None:
        """写入 Sakura 调试日志。"""
        try:
            from app.core.debug_log import debug_log
        except Exception:
            return
        debug_log(
            f"Plugin:{self.manifest.plugin_id}",
            message,
            data or {},
        )


class PluginBase:
    """Sakura 插件基类。"""

    plugin_id = ""
    plugin_version = "0.0.0"

    def initialize(
        self,
        register: PluginCapabilityRegistry,
        context: PluginContext,
    ) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def on_app_start(self, event: PluginEvent) -> None:
        return None

    def on_user_message(self, event: PluginEvent) -> None:
        return None

    def on_ai_message(self, event: PluginEvent) -> None:
        return None

    def on_tts_start(self, event: PluginEvent) -> None:
        return None

    def on_tts_end(self, event: PluginEvent) -> None:
        return None

    def on_character_loaded(self, event: PluginEvent) -> None:
        return None
