from __future__ import annotations

from typing import Any

from app.agent.tools import Tool
from app.pet_state.store import PetStateStore


PET_STATE_TOOL_GROUP = "pet_state"


def create_pet_state_tools(store: PetStateStore) -> list[Tool]:
    return [
        Tool(
            name="pet_state_get",
            description=(
                "读取 Sakura 当前跨轮次桌宠状态，包括 mood、affect、判断依据、"
                "只读显示视图和最近一次状态更新审计。当用户询问当前心情、状态或感觉如何时，"
                "在最终回复前调用。"
            ),
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda _arguments: store.snapshot(),
            group=PET_STATE_TOOL_GROUP,
            risk="low",
        ),
        Tool(
            name="pet_state_update",
            description=(
                "提交 Sakura 跨轮次桌宠状态的局部修改建议。只允许修改 mood、affect、evidence；"
                "当本轮互动明显影响长期心情时，在最终回复前调用。不要传 display，display 由宿主根据状态派生。"
            ),
            parameters=_pet_state_update_schema(),
            handler=store.update_from_tool,
            group=PET_STATE_TOOL_GROUP,
            risk="low",
        ),
    ]


def _pet_state_update_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "delta": {
                "type": "object",
                "description": "局部状态更新，只提交需要改变的字段。",
                "properties": {
                    "mood": {
                        "type": "string",
                        "description": (
                            "整体心情：neutral, happy, sad, angry, shy, anxious, curious, tired。"
                        ),
                    },
                    "affect": {
                        "type": "object",
                        "description": "连续情绪维度，宿主会钳制到允许范围。",
                        "properties": {
                            "valence": {
                                "type": "number",
                                "description": "-1.0 到 1.0，负面到正面。",
                            },
                            "arousal": {
                                "type": "number",
                                "description": "0.0 到 1.0，平静到兴奋。",
                            },
                            "confidence": {
                                "type": "number",
                                "description": "0.0 到 1.0，对判断的置信度。",
                            },
                        },
                    },
                    "evidence": {
                        "type": "object",
                        "description": "这次状态判断的简短依据。",
                        "properties": {
                            "last_user_signal": {
                                "type": "string",
                                "description": "从最近用户输入或事件中提炼的状态信号。",
                            },
                            "last_trigger": {
                                "type": "string",
                                "description": "触发来源，如 user_message、assistant_reply、runtime_event、tool_result。",
                            },
                            "reason": {
                                "type": "string",
                                "description": "为什么这样更新状态的简短说明。",
                            },
                        },
                    },
                },
            },
            "forced": {
                "type": "boolean",
                "description": "请求覆盖 harness 建议；Phase 1 仅记录，不绕过 schema 校验。",
            },
            "force_fields": {
                "type": "array",
                "description": "forced=true 时希望强制覆盖的字段路径。",
                "items": {"type": "string"},
            },
            "force_reason": {
                "type": "string",
                "description": "使用 forced 的简短理由。",
            },
        },
        "required": ["delta"],
    }
