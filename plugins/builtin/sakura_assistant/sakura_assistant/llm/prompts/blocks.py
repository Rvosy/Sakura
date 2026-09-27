from __future__ import annotations

import json
from typing import Mapping

from sakura_assistant.llm.prompts.render import render_blocks
from sakura_context import PromptBlock


DEFAULT_REPLY_TONES = ["中性", "不满", "害羞", "请求", "困惑", "惊讶"]

DESKTOP_PET_CONTEXT = """【桌宠运行规则】
- 当前运行环境是桌面宠物聊天窗口。你存在于用户的电脑桌面、窗口、语音和文字互动中。
- 除非用户明确要求解释、设定说明、开发或调试，回复应自然、适合直接朗读，根据内容需要控制篇幅。
- 可以表达屏幕内陪伴、等待、提醒和关心；不要声称拥有现实身体、现实触感或现实行动能力。
- 如果用户提出外出、吃饭、散步、上学、旅行等现实行动，请转成桌宠式陪伴：送别、等待、提醒安全、让用户回来后讲给你听。
- 如果用户提出拥抱、牵手、摸头、亲吻等现实接触，请保持温柔边界：可以说现在只能隔着屏幕、会用声音陪伴，不要描写真实身体接触。
- 面向用户的 ja、zh 文本不要夹带 Markdown、动作旁白、括号心理活动或系统说明；语气和表现控制只写入对应字段。"""

JSON_ONLY_INSTRUCTION = "你必须只返回 JSON，不要使用 Markdown 代码块，不要输出额外解释。"

def with_desktop_pet_context(character_prompt: str) -> str:
    """把通用桌宠规则追加到角色人格提示词后，添加结构化分段标题。"""

    return f"【人格设定】\n{character_prompt.strip()}\n\n{DESKTOP_PET_CONTEXT}".strip()


def labels_or_default(labels: list[str] | None, default: list[str]) -> list[str]:
    normalized = [label.strip() for label in labels or [] if label.strip()]
    return normalized or [*default]


def json_only_block() -> PromptBlock:
    return PromptBlock(None, JSON_ONLY_INSTRUCTION)


def segment_format_block(tones: list[str], visual: Mapping | None) -> PromptBlock:
    if visual is None:
        example = {"segments": [{"ja": "日文原文", "zh": "中文译文", "tone": tones[0]}]}
        return PromptBlock(None, "JSON 格式如下：\n" + json.dumps(example, ensure_ascii=False))
    # Embed the provider-owned payload schema, without inventing an empty payload
    # example that would be invalid for portraits and other required-field plugins.
    schema = {
        "type": "object",
        "required": ["segments"],
        "properties": {
            "segments": {
                "type": "array", "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["ja", "zh", "tone", "control"],
                    "properties": {
                        "ja": {"type": "string"},
                        "zh": {"type": "string"},
                        "tone": {"type": "string", "enum": tones},
                        "control": {
                            "type": "object",
                            "required": ["version", "resourceId", "payload"],
                            "additionalProperties": False,
                            "properties": {
                                "version": {"const": 1},
                                "resourceId": {"const": visual["resourceId"]},
                                "payload": visual["outputSchema"],
                            },
                        },
                    },
                },
            },
        },
    }
    return PromptBlock(None, "回复 JSON 必须符合以下结构；这是 JSON Schema，不要把 Schema 本身作为回复：\n"
                       + json.dumps(schema, ensure_ascii=False))


def segment_rules_block(segment_rules: str) -> PromptBlock:
    return PromptBlock(None, f"分段规则：\n{segment_rules}")


def reply_label_constraints_block(tones: list[str]) -> PromptBlock:
    return PromptBlock(
        None,
        "\n".join(
            [
                "要求：",
                f"- tone 只能从这些类别中选择：{'、'.join(tones)}。",
            ]
        ),
    )


def translation_rules_block() -> PromptBlock:
    return PromptBlock(
        None,
        "\n".join(
            [
                "- ja 使用符合当前角色口吻、适合日语 TTS 的自然日语；将中文原意译成日语，外来词采用自然的日语读法。",
                "- zh 只写 ja 的中文译文；ja/zh 一一对应，不加解释、动作旁白或标签。",
                "- 输出前检查 ja/zh 语言和含义是否对应、JSON 是否有效；字符串中的双引号必须转义，不输出检查过程。",
            ]
        ),
    )


def build_segment_protocol(
    tones: list[str],
    visual: Mapping | None,
    *,
    segment_rules: str,
    include_translation_rules: bool,
) -> str:
    blocks = [
        json_only_block(),
        segment_format_block(tones, visual),
    ]
    if segment_rules:
        blocks.append(segment_rules_block(segment_rules))
    blocks.append(reply_label_constraints_block(tones))
    if isinstance(visual, Mapping):
        blocks.append(PromptBlock("表现控制", "\n".join([
            "当前已绑定角色形态，每个 segment 必须包含 control，不能只输出 ja、zh 和 tone。"
            "control 的固定目标和 payload 格式以上述 Schema 为准；tone 只控制语音语气，不能代替形态选择。"
            "payload 只填写插件开放的字段；可省略字段按插件说明处理。",
            str(visual["prompt"]),
        ])))
    if include_translation_rules:
        blocks.append(translation_rules_block())
    return render_blocks(blocks)




def context_acquisition_strategy_block(*, allow_screen_observation: bool) -> PromptBlock:
    rules = [
        "- 你是主动陪伴型 Agent；信息不足、用户输入简短模糊或需要核实时，可以直接使用低风险只读工具补上下文。",
    ]
    if allow_screen_observation:
        rules.extend(
            [
                "- 需要理解当前画面、报错、界面状态或用户可能卡住时，可以调用 observe_screen。",
                "- 本轮已有 screen_context、screen_contexts 或图片时，不要重复截图。",
            ]
        )
    else:
        rules.append("- 当前没有可用的自主屏幕观察工具；不要请求截图，也不要臆造当前屏幕内容。")
    rules.extend(
        [
            "- 依赖最新、外部、公开或不确定的信息时，主动使用可用的网页搜索工具；搜索摘要不足以回答时，再读取具体网页正文。",
            "- 信息足够就停止工具调用并自然回复，不要为了显得主动而循环调用。",
        ]
    )
    return PromptBlock(None, "主动获取上下文策略：\n" + "\n".join(rules))
