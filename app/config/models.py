"""app/config/models.py — 集中管理的配置数据模型。

将所有配置 dataclass 集中到此模块，便于：
- 统一管理默认值
- 配置迁移
- 测试验证
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config.defaults import (
    DEFAULT_BASE_URL,
    DEFAULT_DEBUG_BODY_ENABLED,
    DEFAULT_DEBUG_ENABLED,
    DEFAULT_DEBUG_FILE_ENABLED,
    DEFAULT_MODEL,
)


# ---- API 配置 ----

@dataclass(frozen=True)
class ApiSettings:
    """LLM API 连接配置。"""

    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""
    model: str = DEFAULT_MODEL
    timeout_seconds: int = 60


# ---- 调试日志 ----

@dataclass(frozen=True)
class DebugLogSettings:
    """调试日志配置。"""

    enabled: bool = DEFAULT_DEBUG_ENABLED
    body_enabled: bool = DEFAULT_DEBUG_BODY_ENABLED
    file_enabled: bool = DEFAULT_DEBUG_FILE_ENABLED


# ---- TTS 配置 (存根，实际实现在 app/voice/tts.py) ----
# GPTSoVITSTTSSettings 仍在 app/voice/tts.py 中定义，
# 因其包含 validate() 等逻辑方法，不适合纯数据容器。


# ---- MCP 运行时 ----
# MCPRuntimeSettings 在 app/agent/mcp/settings.py 中定义


# ---- 主动关怀 ----
# ProactiveCareSettings 在 app/agent/proactive_care.py 中定义


# ---- 记忆整理 ----
# MemoryCurationSettings 在 app/agent/memory_curator.py 中定义
