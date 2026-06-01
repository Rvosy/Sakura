from __future__ import annotations

from app.llm.prompts.blocks import (
    AGENT_REPLY_FORMAT,
    DEFAULT_REPLY_PORTRAITS,
    DEFAULT_REPLY_TONES,
    SEGMENTED_REPLY_FORMAT,
    build_proactive_check_segment_rules,
    build_segment_protocol,
    context_acquisition_strategy_block,
    labels_or_default,
    proactive_reply_decision_flow_block,
    proactive_reply_examples_block,
    proactive_rules_block,
    proactive_scene_strategy_block,
    proactive_web_research_rules_block,
)
from app.llm.prompts.render import render_blocks
from app.llm.prompts.types import PromptBlock


def build_segmented_reply_instruction(
    reply_tones: list[str] | None,
    reply_portraits: list[str] | None = None,
    *,
    simple_segments: str = "2-3",
    default_segments: str = "3-4",
    include_translation_rules: bool = True,
    include_no_single_segment_rule: bool = False,
) -> str:
    tones = labels_or_default(reply_tones, DEFAULT_REPLY_TONES)
    portraits = labels_or_default(reply_portraits, DEFAULT_REPLY_PORTRAITS)
    rules = [
        f"- 尽量输出 {default_segments} 段文本，每段是一条可以单独显示和朗读的完整小消息，不要把一句话机械切碎。",
        "- 单段建议 35-90 个中文或日文字符；内容需要完整自然，宁可少分段也不要短到像碎片。",
        f"- 如果用户只问很简单的问题，可以只输出 {simple_segments} 段。",
        "- 需要对每段文本的语气进行标注，语气标签放在 tone 字段中。优先选择中性，除非文本明显带有其他语气；如果文本中同时包含多种语气，请选择最突出的一种。",
    ]
    if include_no_single_segment_rule:
        rules.extend(
            [
                "- 用户问题包含多个要点、步骤、原因或较长说明时，优先输出 3-4 段，让桌宠可以逐段显示和朗读。",
                "- 不要因为返回格式示例里只写了一条 segment，就把完整回复固定成一段。",
            ]
        )
    return build_segment_protocol(
        tones,
        portraits,
        format_text=SEGMENTED_REPLY_FORMAT,
        segment_rules="\n".join(rules),
        include_translation_rules=include_translation_rules,
    )


def build_agent_reply_protocol(
    reply_tones: list[str] | None,
    reply_portraits: list[str] | None = None,
) -> str:
    tones = labels_or_default(reply_tones, DEFAULT_REPLY_TONES)
    portraits = labels_or_default(reply_portraits, DEFAULT_REPLY_PORTRAITS)
    segment_rules = "\n".join(
        [
            "- 尽量输出 2-4 段文本，每段是一条可以单独显示和朗读的完整小消息，不要把一句话机械切碎。",
            "- 单段建议 35-90 个中文或日文字符；内容需要完整自然，宁可少分段也不要短到像碎片。",
            "- 如果用户只问很简单的问题，可以只输出 1-2 段。",
            "- 用户问题包含多个要点、步骤、原因或较长说明时，优先输出 3-4 段，让桌宠可以逐段显示和朗读。",
            "- 不要因为返回格式示例里只写了一条 segment，就把完整回复固定成一段。",
        ]
    )
    return build_segment_protocol(
        tones,
        portraits,
        format_text=AGENT_REPLY_FORMAT,
        segment_rules=segment_rules,
        include_translation_rules=True,
    )


def build_event_reply_protocol(
    reply_tones: list[str] | None,
    reply_portraits: list[str] | None = None,
    *,
    example_tone: str = "请求",
    segment_rules: str = "",
) -> str:
    tones = labels_or_default(reply_tones, DEFAULT_REPLY_TONES)
    portraits = labels_or_default(reply_portraits, DEFAULT_REPLY_PORTRAITS)
    format_text = (
        f'{{"segments":[{{"ja":"日文原文","zh":"中文译文","tone":"{example_tone}","portrait":"站立待机"}}]}}'
    )
    return build_segment_protocol(
        tones,
        portraits,
        format_text=format_text,
        segment_rules=segment_rules,
        include_translation_rules=True,
    )


def build_proactive_check_reply_protocol(
    reply_tones: list[str] | None,
    reply_portraits: list[str] | None = None,
) -> str:
    """构建主动屏幕检查事件专用回复协议。"""

    return build_event_reply_protocol(
        reply_tones,
        reply_portraits,
        example_tone="中性",
        segment_rules=build_proactive_check_segment_rules(),
    )


def build_context_acquisition_strategy(*, allow_screen_observation: bool) -> str:
    return context_acquisition_strategy_block(
        allow_screen_observation=allow_screen_observation
    ).body


def build_proactive_check_tool_system_prompt(
    character_prompt: str,
    reply_tones: list[str] | None,
    reply_portraits: list[str] | None,
    *,
    memory_summary: str,
    current_time: str,
    step_index: int,
    remaining_steps: int,
    max_tool_calls_per_step: int,
    max_tool_calls_per_turn: int,
    extra_instructions: str = "",
) -> str:
    """构建主动屏幕检查 tool-loop 使用的系统提示词。"""

    reply_protocol = build_proactive_check_reply_protocol(reply_tones, reply_portraits)
    return render_blocks(
        [
            PromptBlock(None, character_prompt.strip()),
            PromptBlock(
                None,
                "\n\n".join(
                    [
                        "你现在正在处理【主动检查事件 / 主动屏幕检查事件】。这不是用户直接发来的请求，而是系统定时触发的低打扰搭话。",
                        "请用角色语气自然搭话、提问或提醒用户。",
                        "请把 screen_contexts/visual_contexts 当作当前画面，把 recent_conversation 当作最近完整对话历史；必须结合两者判断用户正在延续什么任务、发生了什么变化、哪些话题已经聊过，再自然接话。",
                    ]
                ),
            ),
            PromptBlock(
                "核心目标",
                "\n".join(
                    [
                        "- 结合图片和最近对话历史理解用户这段时间在做什么，而不是逐张描述截图。",
                        "- recent_conversation 包含用户和 Sakura 的最近对话；它用于判断上下文、进展、已给建议和已重复话题，不只是用来避免 Sakura 自己复读。",
                        "- 优先使用 visual_contexts 中的 summary、visible_texts、notable_elements。",
                        "- 最终回复必须至少点到一个具体可见对象，除非视觉上下文为空或明确不可识别。",
                        "- 如果只能部分识别，也要先说出能确认的部分，再轻轻询问。",
                        "- 不要机械套用休息、喝水、深呼吸、累不累等通用关怀。",
                    ]
                ),
            ),
            proactive_reply_decision_flow_block(),
            proactive_scene_strategy_block(),
            proactive_web_research_rules_block(),
            proactive_rules_block(include_tool_rules=True),
            proactive_reply_examples_block(),
            PromptBlock(None, reply_protocol),
            PromptBlock(None, extra_instructions.strip()),
            PromptBlock(None, f"长期记忆摘要：\n{memory_summary}"),
            PromptBlock(None, f"当前本地时间：\n{current_time}"),
            PromptBlock(
                None,
                "\n".join(
                    [
                        "当前 Agent 循环：",
                        f"- 这是第 {step_index + 1} 步，之后最多还可以继续 {remaining_steps} 步。",
                        "- 如果信息足够或已经完成，不要再发起 tool_calls。",
                        f"- 每步最多请求 {max_tool_calls_per_step} 个工具，整轮最多 {max_tool_calls_per_turn} 个工具。",
                        "",
                        "- 你可以使用只读或低风险工具补充上下文（后台 Web 搜索、当前时间、搜索记忆、列出待办和笔记、查看已有提醒）。",
                        "- 如果事件已有 screen_contexts（多张截图），不要再请求 observe_screen。",
                        "- 不要循环调用工具；工具结果足够后直接给最终回复。",
                        "- 最终回复只说给用户听的自然搭话、提问或轻提醒，不要提及内部事件或工具协议。",
                    ]
                ),
            ),
        ]
    )


def build_event_system_prompt(
    character_prompt: str,
    reply_tones: list[str] | None,
    reply_portraits: list[str] | None,
    *,
    event_type: str = "reminder_due",
) -> str:
    """构建主动事件直接回复路径使用的系统提示词。"""

    blocks: list[PromptBlock] = [
        PromptBlock(None, character_prompt.strip()),
        PromptBlock(None, "你正在处理 Sakura 桌宠的主动事件。请用角色语气自然搭话、提问用户。"),
    ]
    if event_type == "proactive_check":
        blocks.extend(
            [
                PromptBlock(
                    None,
                    build_proactive_check_reply_protocol(reply_tones, reply_portraits),
                ),
                PromptBlock(None, "- 不要提及内部事件类型、JSON 或工具实现。"),
                proactive_reply_decision_flow_block(),
                proactive_scene_strategy_block(),
                proactive_rules_block(),
                proactive_reply_examples_block(),
            ]
        )
    else:
        blocks.extend(
            [
                PromptBlock(
                    None,
                    build_event_reply_protocol(
                        reply_tones,
                        reply_portraits,
                        example_tone="请求",
                    ),
                ),
                PromptBlock(None, "- 不要提及内部事件类型、JSON 或工具实现。"),
            ]
        )
    return render_blocks(blocks)


def build_proactive_rules(*, include_tool_rules: bool = False) -> str:
    return proactive_rules_block(include_tool_rules=include_tool_rules).body


def build_proactive_tool_loop_rules() -> str:
    return render_blocks(
        [
            PromptBlock(None, "- 这是主动检查事件，不是用户直接发来的请求；整体保持低打扰。"),
            PromptBlock(None, "- 请用角色语气自然搭话、提问或提醒用户。"),
            proactive_reply_decision_flow_block(),
            proactive_scene_strategy_block(),
            proactive_web_research_rules_block(),
            proactive_rules_block(include_tool_rules=True),
            proactive_reply_examples_block(),
        ]
    )


def build_proactive_reply_decision_flow() -> str:
    """构建主动感知回复前的稳定判断链。"""

    return proactive_reply_decision_flow_block().body


def build_proactive_scene_strategy_rules() -> str:
    """构建不同屏幕场景对应的主动搭话策略。"""

    return proactive_scene_strategy_block().body


def build_proactive_web_research_rules() -> str:
    """构建主动感知后台 Web 搜索规则。"""

    return proactive_web_research_rules_block().body


def build_proactive_reply_examples() -> str:
    """构建主动感知好坏例子，减少泛化关怀和过度吃醋。"""

    return proactive_reply_examples_block().body
