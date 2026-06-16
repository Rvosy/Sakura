"""app/config/models.py — 集中管理的配置数据模型。

将所有配置 dataclass 集中到此模块，便于：
- 统一管理默认值
- 配置迁移
- 测试验证
"""

from __future__ import annotations

from dataclasses import dataclass


# ---- API 配置 ----

@dataclass(frozen=True)
class ApiSettings:
    """LLM API 连接配置。"""

    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4.1-mini"
    timeout_seconds: int = 60


# ---- 调试日志 ----

@dataclass(frozen=True)
class DebugLogSettings:
    """调试日志配置。"""

    enabled: bool = False
    body_enabled: bool = False
    file_enabled: bool = False
    # 开发者选项:舞台调试框(画窗口/布局/实际立绘三框 + DPR 数值,排查布局/HiDPI)。
    stage_debug_overlay: bool = False
    # 舞台碰撞遮罩(默认开):setMask 到内容矩形并集,立绘四周空白点击穿透,避免误拖/挡点击。
    stage_collision_mask: bool = True


# ---- TTS 配置 (存根，实际实现在 app/voice/tts_settings.py) ----
# GPTSoVITSTTSSettings 在 app/voice/tts_settings.py 中定义，
# 因其包含 validate() 等逻辑方法，不适合纯数据容器。


# ---- MCP 运行时 ----
# MCPRuntimeSettings 在 app/agent/mcp/settings.py 中定义


# ---- 主动屏幕感知 ----
# ScreenAwarenessSettings 在 app/agent/screen_awareness.py 中定义


# ---- 记忆整理 ----
# MemoryCurationSettings 在 app/agent/memory_curator.py 中定义
