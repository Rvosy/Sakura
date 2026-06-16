"""Sakura 旧插件 SDK 兼容入口。

新插件建议直接使用 ``app.plugins``。本包保留给已发布的旧 SDK 插件，
避免覆盖升级后因 ``sdk.*`` 导入缺失而加载失败。
"""

from sdk.plugin import PluginBase
from sdk.plugin_host_context import PluginContext, PluginHostContext
from sdk.register import PluginCapabilityRegistry

__all__ = [
    "PluginBase",
    "PluginCapabilityRegistry",
    "PluginContext",
    "PluginHostContext",
]
