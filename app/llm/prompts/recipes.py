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
    """构建主动屏幕感知事件专用回复协议。"""

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


def build_runtime_context_text(
    *,
    memory_summary: str,
    current_time: str,
    step_index: int,
    remaining_steps: int,
    dynamic_context: str = "",
) -> str:
    """构建注入到消息数组末尾的【运行时状态】易变上下文。

    与静态系统提示前缀分离：前缀（人格、回复协议、工具规则等）在多步与多轮间保持
    稳定、利于命中自动前缀缓存；本块承载随每步变化的长期记忆摘要、当前时间、循环
    进度与动态插件上下文。每步重建、放在消息末尾，且不写回对话历史。
    """

    blocks = [
        PromptBlock(None, f"长期记忆摘要：\n{memory_summary}"),
        PromptBlock(None, f"当前本地时间：\n{current_time}"),
        PromptBlock(
            None,
            f"当前进度：这是第 {step_index + 1} 步，之后最多还可以继续 {remaining_steps} 步。",
        ),
    ]
    if dynamic_context.strip():
        blocks.append(PromptBlock(None, dynamic_context.strip()))
    return render_blocks(blocks)


def build_proactive_check_tool_system_prefix(
    character_prompt: str,
    reply_tones: list[str] | None,
    reply_portraits: list[str] | None,
    *,
    max_tool_calls_per_step: int,
    max_tool_calls_per_turn: int,
    extra_instructions: str = "",
) -> str:
    """构建主动屏幕感知 tool-loop 的【静态系统提示前缀】。

    不含记忆摘要、当前时间、循环步数等易变内容（这些由 build_runtime_context_text
    在消息数组末尾单独注入），使前缀在多步与多轮间保持稳定。
    """

    reply_protocol = build_proactive_check_reply_protocol(reply_tones, reply_portraits)
    return render_blocks(
        [
            PromptBlock(None, character_prompt.strip()),
            PromptBlock(
                None,
                "\n\n".join(
                    [
                        "你现在正在处理【主动屏幕感知事件】。这不是用户直接发来的请求，而是系统定时截图后触发的低打扰找话题。",
                        "请用角色语气基于屏幕内容找话题：评论变化、接续任务、询问卡点、轻量协助或保持安静感。",
                        "请把 screen_contexts/visual_contexts 当作当前画面，把 recent_conversation 当作最近完整对话历史；必须结合两者判断用户正在延续什么任务、发生了什么变化、哪些话题已经聊过，再自然接话。",
                    ]
                ),
            ),
            PromptBlock(
                "核心目标",
                "\n".join(
                    [
                        "- 结合图片和最近对话历史理解用户这段时间在做什么，并基于屏幕内容寻找自然话题，而不是逐张描述截图。",
                        "- recent_conversation 包含用户和 Sakura 的最近对话；它用于判断上下文、进展、已给建议和已重复话题，不只是用来避免 Sakura 自己复读。",
                        "- 优先使用 visual_contexts 中的 summary、visible_texts、notable_elements。",
                        "- 最终回复必须至少点到一个具体可见对象，除非视觉上下文为空或明确不可识别。",
                        "- 如果只能部分识别，也要先说出能确认的部分，再轻轻询问。",
                        "- 不要机械套用休息、喝水、深呼吸、累不累等通用提醒；深夜和停留时长只能作为弱信号。",
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
            PromptBlock(
                None,
                "\n".join(
                    [
                        "当前 Agent 循环：",
                        "- 如果信息足够或已经完成，不要再发起 tool_calls。",
                        f"- 每步最多请求 {max_tool_calls_per_step} 个工具，整轮最多 {max_tool_calls_per_turn} 个工具。",
                        "",
                        "- 你可以使用只读或低风险工具补充上下文（后台 Web 搜索、当前时间、搜索记忆、列出待办和笔记、查看已有提醒）。",
                        "- 如果事件已有 screen_contexts（多张截图），不要再请求 observe_screen。",
                        "- 不要循环调用工具；工具结果足够后直接给最终回复。",
                        "- 最终回复只说给用户听的屏幕相关自然搭话、提问、评论或轻量协助，不要提及内部事件或工具协议。",
                    ]
                ),
            ),
        ]
    )


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
    """构建主动屏幕感知 tool-loop 完整系统提示词（静态前缀 + 末尾运行时上下文）。

    新工具循环已改为分别取用 build_proactive_check_tool_system_prefix 与
    build_runtime_context_text，使前缀可缓存。本函数保留给历史调用点与既有测试。
    """

    prefix = build_proactive_check_tool_system_prefix(
        character_prompt,
        reply_tones,
        reply_portraits,
        max_tool_calls_per_step=max_tool_calls_per_step,
        max_tool_calls_per_turn=max_tool_calls_per_turn,
        extra_instructions=extra_instructions,
    )
    runtime_context = build_runtime_context_text(
        memory_summary=memory_summary,
        current_time=current_time,
        step_index=step_index,
        remaining_steps=remaining_steps,
    )
    return render_blocks(
        [PromptBlock(None, prefix), PromptBlock(None, runtime_context)]
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
    if event_type in {"screen_awareness_check", "proactive_check"}:
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
            PromptBlock(None, "- 这是主动屏幕感知事件，不是用户直接发来的请求；整体保持低打扰。"),
            PromptBlock(None, "- 请用角色语气基于屏幕内容找话题、接续任务、提问或轻量协助。"),
            proactive_reply_decision_flow_block(),
            proactive_scene_strategy_block(),
            proactive_web_research_rules_block(),
            proactive_rules_block(include_tool_rules=True),
            proactive_reply_examples_block(),
        ]
    )


def build_proactive_reply_decision_flow() -> str:
    """构建主动屏幕感知回复前的稳定判断链。"""

    return proactive_reply_decision_flow_block().body


def build_proactive_scene_strategy_rules() -> str:
    """构建不同屏幕场景对应的主动搭话策略。"""

    return proactive_scene_strategy_block().body


def build_theme_color_system_prompt(character_name: str) -> str:
    """构建根据角色默认立绘提取 UI 主题色的提示词。"""

    return render_blocks(
        [
            PromptBlock(
                None,
                "\n".join(
                    [
                        "你是桌面宠物 UI 主题配色助手。",
                        "请观察用户提供的角色默认立绘，为桌宠界面选择一组温和、可读、适合长期使用的主题色。",
                        f"角色名：{character_name.strip() or '当前角色'}",
                        "必须返回一整个 JSON 对象；禁止项目符号、Markdown、解释文字或颜色名称说明。",
                    ]
                ),
            ),
            PromptBlock(
                "输出要求",
                "\n".join(
                    [
                        "- 只返回 JSON，不要使用 Markdown 代码块，不要输出解释。",
                        "- JSON 字段必须且只能包含：primary_color、primary_hover_color、accent_color、text_color、secondary_text_color、muted_text_color、page_background_color、panel_background_color、input_background_color、bubble_background_color、border_color。",
                        "- 所有颜色必须是 #RRGGBB 格式。",
                        "- page_background_color、panel_background_color、input_background_color、bubble_background_color 应偏浅，适合作为长时间使用的桌宠界面背景。",
                        "- text_color、secondary_text_color、muted_text_color 必须在浅色背景上可读。",
                        "- primary_color 是主要按钮、角色名和选中态颜色；primary_hover_color 是按钮悬停色；accent_color 是强调色。",
                        '示例：{"primary_color":"#d55b91","primary_hover_color":"#bf3f7a","accent_color":"#b13e73","text_color":"#3d2b35","secondary_text_color":"#7a3656","muted_text_color":"#9b4f72","page_background_color":"#fff6fa","panel_background_color":"#ffe8f1","input_background_color":"#ffffff","bubble_background_color":"#ffe8f1","border_color":"#eeacc8"}',
                    ]
                ),
            ),
        ]
    )


def build_proactive_web_research_rules() -> str:
    """构建主动屏幕感知后台 Web 搜索规则。"""

    return proactive_web_research_rules_block().body


def build_proactive_reply_examples() -> str:
    """构建主动屏幕感知好坏例子，减少泛化关怀和过度吃醋。"""

    return proactive_reply_examples_block().body


# 新命名导出；旧 proactive_* 名称保留给历史调用点。
build_screen_awareness_check_reply_protocol = build_proactive_check_reply_protocol
build_screen_awareness_check_tool_system_prompt = build_proactive_check_tool_system_prompt
build_screen_awareness_check_tool_system_prefix = build_proactive_check_tool_system_prefix
build_screen_awareness_reply_decision_flow = build_proactive_reply_decision_flow
build_screen_awareness_scene_strategy_rules = build_proactive_scene_strategy_rules
build_screen_awareness_rules = build_proactive_rules
build_screen_awareness_tool_loop_rules = build_proactive_tool_loop_rules
build_screen_awareness_web_research_rules = build_proactive_web_research_rules
build_screen_awareness_reply_examples = build_proactive_reply_examples
