"""app/config/models.py — 集中管理的配置数据模型。

将所有配置 dataclass 集中到此模块，便于：
- 统一管理默认值
- 配置迁移
- 测试验证
"""

from __future__ import annotations

from dataclasses import dataclass, field


MODEL_SLOT_CHAT = "chat"
MODEL_SLOT_VISION_CHAT = "vision_chat"
MODEL_SLOT_VISUAL_CONTEXT = "visual_context"
MODEL_SLOT_MEMORY_CURATION = "memory_curation"
MODEL_SLOT_THEME_AI = "theme_ai"

MODEL_SLOT_ORDER = (
    MODEL_SLOT_CHAT,
    MODEL_SLOT_VISION_CHAT,
    MODEL_SLOT_VISUAL_CONTEXT,
    MODEL_SLOT_MEMORY_CURATION,
    MODEL_SLOT_THEME_AI,
)

MODEL_SLOT_UI_ORDER = (
    MODEL_SLOT_CHAT,
    MODEL_SLOT_VISION_CHAT,
    MODEL_SLOT_MEMORY_CURATION,
)

MODEL_SLOT_LABELS = {
    MODEL_SLOT_CHAT: "聊天模型",
    MODEL_SLOT_VISION_CHAT: "视觉模型",
    MODEL_SLOT_VISUAL_CONTEXT: "视觉摘要模型",
    MODEL_SLOT_MEMORY_CURATION: "记忆整理模型",
    MODEL_SLOT_THEME_AI: "AI 配色模型",
}

MODEL_SLOT_DESCRIPTIONS = {
    MODEL_SLOT_CHAT: "全局默认的角色聊天模型，必填。",
    MODEL_SLOT_VISION_CHAT: "当聊天模型不支持图片，或想要自定义视觉模型时使用；留空则由聊天模型直接看原图。",
    MODEL_SLOT_VISUAL_CONTEXT: "用于把截图/屏幕转成可检索的视觉摘要；默认继承视觉模型。",
    MODEL_SLOT_MEMORY_CURATION: "用于自动整理长期记忆；留空则继承聊天模型。",
    MODEL_SLOT_THEME_AI: "用于根据角色立绘生成主题配色；留空则继承视觉模型。",
}

MODEL_SLOT_FALLBACKS = {
    MODEL_SLOT_VISION_CHAT: (MODEL_SLOT_CHAT,),
    MODEL_SLOT_VISUAL_CONTEXT: (MODEL_SLOT_VISION_CHAT, MODEL_SLOT_CHAT),
    MODEL_SLOT_MEMORY_CURATION: (MODEL_SLOT_CHAT,),
    MODEL_SLOT_THEME_AI: (MODEL_SLOT_VISION_CHAT, MODEL_SLOT_CHAT),
}


# ---- API 配置 ----

@dataclass(frozen=True)
class ApiSettings:
    """LLM API 连接配置。"""

    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4.1-mini"
    timeout_seconds: int = 60
    # 角色对话生成参数；None 表示沿用内置默认/不发送该参数，保持历史行为。
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None


@dataclass(frozen=True)
class ApiConfigProfile:
    """单条 API 供应商配置，包含 base_url、api_key、别名和该供应商模型列表。

    模型列表属于供应商，功能槽位只能选择某个供应商下已添加的模型。
    """

    id: str
    alias: str
    base_url: str
    api_key: str = ""
    models: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelSlotSelection:
    """某个功能槽位选中的供应商和模型；空值表示继承。"""

    profile_id: str = ""
    model: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.profile_id.strip() and self.model.strip())


@dataclass(frozen=True)
class ModelSelectionSettings:
    """各功能实际使用的模型配置。"""

    chat: ModelSlotSelection = field(default_factory=ModelSlotSelection)
    vision_chat: ModelSlotSelection | None = None
    visual_context: ModelSlotSelection | None = None
    memory_curation: ModelSlotSelection | None = None
    theme_ai: ModelSlotSelection | None = None

    def get(self, slot: str) -> ModelSlotSelection | None:
        if slot == MODEL_SLOT_CHAT:
            return self.chat
        if slot == MODEL_SLOT_VISION_CHAT:
            return self.vision_chat
        if slot == MODEL_SLOT_VISUAL_CONTEXT:
            return self.visual_context
        if slot == MODEL_SLOT_MEMORY_CURATION:
            return self.memory_curation
        if slot == MODEL_SLOT_THEME_AI:
            return self.theme_ai
        return None

    @property
    def vision_profile_id(self) -> str:
        selection = self.vision_chat or self.chat
        return selection.profile_id

    @property
    def vision_model(self) -> str:
        selection = self.vision_chat or self.chat
        return selection.model

    @property
    def text_enabled(self) -> bool:
        return self.vision_chat is not None

    @property
    def text_profile_id(self) -> str:
        return self.chat.profile_id

    @property
    def text_model(self) -> str:
        return self.chat.model


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
