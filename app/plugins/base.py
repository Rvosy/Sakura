"""Sakura 原生插件基类与上下文。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.plugins.capabilities import PluginCapabilityRegistry
from app.plugins.models import PluginEvent

# 插件默认配置文件名，安装目录与用户数据目录共用。
PLUGIN_CONFIG_FILENAME = "config.json"


@dataclass(frozen=True)
class PluginContext:
    """插件初始化时可读取的 Sakura 宿主上下文。"""

    base_dir: Path
    plugin_root: Path
    data_dir: Path
    manifest: Any
    # 新增字段均带默认值，避免破坏旧的构造调用与三参数 SDK 路径。
    events: Any = None  # ScopedEventBus；测试或旧路径可能为 None
    services: Any = None  # PluginServices；测试或旧路径可能为 None

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

    def get_config(self) -> dict[str, Any]:
        """读取插件配置。

        读取优先级：``data/plugins/<id>/config.json``（用户覆盖）优先于
        ``plugins/<id>/config.json``（安装目录默认）。实现为：默认配置打底，
        用户配置浅覆盖同名键。任一文件缺失视为 ``{}``；JSON 解析失败写日志后
        按 ``{}`` 处理，不抛异常。
        """
        default_config = self._read_config_json(self.plugin_root / PLUGIN_CONFIG_FILENAME)
        user_config = self._read_config_json(self.data_dir / PLUGIN_CONFIG_FILENAME)
        merged = dict(default_config)
        merged.update(user_config)
        return merged

    def save_config(self, config: dict[str, Any]) -> None:
        """保存插件配置。

        只写入用户数据目录 ``data/plugins/<id>/config.json``，
        不修改安装目录下的默认配置。
        """
        target = self.data_dir / PLUGIN_CONFIG_FILENAME
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_data_path(self, relative_path: str) -> Path:
        """获取插件私有数据目录下的安全路径。

        防止路径穿越：拒绝绝对路径，且解析后的目标必须仍位于 ``data_dir`` 之内，
        否则抛出 ``ValueError``。
        """
        candidate = Path(relative_path)
        if candidate.is_absolute():
            raise ValueError(f"插件数据路径不能为绝对路径：{relative_path}")
        data_root = self.data_dir.resolve()
        target = (self.data_dir / candidate).resolve()
        if target != data_root and data_root not in target.parents:
            raise ValueError(f"插件数据路径越界：{relative_path}")
        return target

    def _read_config_json(self, path: Path) -> dict[str, Any]:
        """读取 JSON 配置文件；不存在返回空字典，格式错误写日志后返回空字典。"""
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.log("插件配置读取失败，已按空配置处理", {"path": str(path), "error": str(exc)})
            return {}
        return data if isinstance(data, dict) else {}


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
