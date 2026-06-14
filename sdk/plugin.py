from __future__ import annotations

from pathlib import Path

from app.plugins.base import PluginBase as _HostPluginBase
from app.plugins.base import PluginContext
from app.plugins.capabilities import PluginCapabilityRegistry
from sdk.plugin_host_context import PluginHostContext


class PluginBase(_HostPluginBase):
    """旧 SDK 插件基类。

    继承宿主当前的 PluginBase，让旧插件仍能通过宿主的 isinstance 校验。
    """


LegacyInitializeArgs = tuple[PluginCapabilityRegistry, Path, PluginHostContext]
