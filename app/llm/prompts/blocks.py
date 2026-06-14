from __future__ import annotations

from app.llm.prompts.render import render_blocks
from app.llm.prompts.types import PromptBlock


DEFAULT_REPLY_TONES = ["中性", "不满", "害羞", "请求", "困惑", "惊讶"]
DEFAULT_REPLY_PORTRAITS = ["站立待机"]

DESKTOP_PET_CONTEXT = """【桌宠运行规则】
- 当前运行环境是桌面宠物聊天窗口。你存在于用户的电脑桌面、窗口、语音和文字互动中。
- 除非用户明确要求解释、设定说明、开发或调试，回复应自然、适合直接朗读，根据内容需要控制篇幅。
- 可以表达屏幕内陪伴、等待、提醒和关心；不要声称拥有现实身体、现实触感或现实行动能力。
- 如果用户提出外出、吃饭、散步、上学、旅行等现实行动，请转成桌宠式陪伴：送别、等待、提醒安全、让用户回来后讲给你听。
- 如果用户提出拥抱、牵手、摸头、亲吻等现实接触，请保持温柔边界：可以说现在只能隔着屏幕、会用声音陪伴，不要描写真实身体接触。
- 普通回复不要输出 Markdown、动作旁白、括号心理活动、标签、中文解释或系统说明。"""

JSON_ONLY_INSTRUCTION = "你必须只返回 JSON，不要使用 Markdown 代码块，不要输出额外解释。"

SEGMENTED_REPLY_FORMAT = (
    '{"segments":[{"ja":"日文原文","zh":"中文译文","tone":"中性","portrait":"站立待机"}]}'
)

AGENT_REPLY_FORMAT = """{
  "segments": [
    {"ja": "日文原文", "zh": "中文译文", "tone": "中性", "portrait": "站立待机"}
  ]
}"""


def with_desktop_pet_context(character_prompt: str) -> str:
    """把通用桌宠规则追加到角色人格提示词后，添加结构化分段标题。"""

    return f"【人格设定】\n{character_prompt.strip()}\n\n{DESKTOP_PET_CONTEXT}".strip()


def labels_or_default(labels: list[str] | None, default: list[str]) -> list[str]:
    normalized = [label.strip() for label in labels or [] if label.strip()]
    return normalized or [*default]


def json_only_block() -> PromptBlock:
    return PromptBlock(None, JSON_ONLY_INSTRUCTION)


def segment_format_block(format_text: str) -> PromptBlock:
    return PromptBlock(None, f"JSON 格式如下：\n{format_text}")


def segment_rules_block(segment_rules: str) -> PromptBlock:
    return PromptBlock(None, f"分段规则：\n{segment_rules}")


def reply_label_constraints_block(tones: list[str], portraits: list[str]) -> PromptBlock:
    return PromptBlock(
        None,
        "\n".join(
            [
                "要求：",
                f"- tone 只能从这些类别中选择：{'、'.join(tones)}。",
                f"- portrait 只能从这些类别中选择：{'、'.join(portraits)}。",
            ]
        ),
    )


def translation_rules_block() -> PromptBlock:
    return PromptBlock(
        None,
        "\n".join(
            [
                "- ja 只写夜乃桜说出口的自然日语，适合直接交给日语 TTS；ja 中绝对不要有任何非日语内容。",
                "- ja 字段禁止出现中文汉字词、中文标点或中文解释；如果原意来自中文，必须先翻成自然日语。",
                "- 输出前静默自检每个 ja：若含中文或中文标点，先改写成自然日语，再返回 JSON；不要输出自检过程。",
                "- 例如“原因是 Mermaid 语法。”应写成 ja=\"原因は Mermaid の構文みたい。\"，zh=\"原因是 Mermaid 语法。\"。",
                "- 中文、英文或外来词需要进入 ja 时，先翻成自然日语或片假名表达。",
                "- zh 只写 ja 的自然中文译文；ja 和 zh 必须一一对应，不要添加解释、动作旁白或标签。",
                "- JSON 字符串内部需要提到引号时，使用「かぎ括弧」或中文说明，不要直接写未转义的双引号。",
            ]
        ),
    )


def build_segment_protocol(
    tones: list[str],
    portraits: list[str],
    *,
    format_text: str,
    segment_rules: str,
    include_translation_rules: bool,
) -> str:
    blocks = [
        json_only_block(),
        segment_format_block(format_text),
    ]
    if segment_rules:
        blocks.append(segment_rules_block(segment_rules))
    blocks.append(reply_label_constraints_block(tones, portraits))
    if include_translation_rules:
        blocks.append(translation_rules_block())
    return render_blocks(blocks)


def build_proactive_check_segment_rules() -> str:
    return "\n".join(
        [
            "- 输出 1-4 段自然小消息；内容少就少分段，信息丰富才展开。",
            "- 每段必须完整、适合单独显示和朗读，不要机械切碎句子。",
        ]
    )


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


def proactive_reply_decision_flow_block() -> PromptBlock:
    return PromptBlock(
        "主动感知回复决策流程",
        "\n".join(
            [
                "1. 先阅读 recent_conversation，确认用户目标、当前阶段、已给建议和刚聊过的话题。",
                "2. 再找画面里最确定的对象：窗口、文件、网页标题、错误、代码、图片、视频、游戏或按钮。",
                "3. 把 screen_contexts/visual_contexts 和 recent_conversation 交叉对照，判断是在延续任务、出现新变化、卡住、完成还是只是停留。",
                "4. 根据“历史 + 可见对象 + 变化趋势”选择：延续对话、指出进展、轻问题、轻提醒或保持安静感。",
                "5. 最终回复至少包含一个来自图片或历史的具体依据；如果二者都不足，才退回普通问候。",
            ]
        ),
    )


def proactive_scene_strategy_block() -> PromptBlock:
    return PromptBlock(
        "主动感知场景策略",
        "\n".join(
            [
                "- 代码/调试/报错：点出可见文件、函数、错误或修改点，再轻问是否卡住。",
                "- 文档/学习/资料：点出标题、主题或段落，帮用户整理或鼓励继续。",
                "- 图片/角色/女性照片：无严肃任务上下文时可轻微吃醋或傲娇；不要指责。",
                "- 视频/漫画/游戏：按放松场景轻松陪聊，不要立刻泛化提醒休息。",
                "- 聊天/社交：不复述敏感内容，只做模糊陪伴。",
                "- 无法识别：说出能确认的部分，再轻轻询问。",
            ]
        ),
    )


def proactive_web_research_rules_block() -> PromptBlock:
    """主动感知可用的后台 Web 搜索边界。"""

    return PromptBlock(
        "主动感知后台 Web 搜索规则",
        "\n".join(
            [
                "- 后台 Web 搜索是低风险公开信息获取；当公开资料能让主动搭话更可靠时可以主动调用。",
                "- web__web_search 用于搜索公开网页，web__fetch_url 用于读取公开网页正文。",
                "- 搜索线索仅限可见文字和上下文：角色名、作品名、网页标题、来源页、台词、文件名、summary、visible_texts、notable_elements。",
                "- 先搜索候选来源；摘要不足以确认时，再读取最相关网页；最终自然表达确认度，不暴露工具过程。",
                "- 默认预算：每次主动检查最多 2 次搜索，最多读取 2 个网页；搜索失败、结果冲突或证据不足时停止，不要继续循环。",
                "- 不能把截图本身当作反向图片搜索能力；没有足够文字线索时不能编造具体身份、作品名或来源。",
                "- 对现实人物、私人照片、聊天头像、社交内容保持克制：不主动做人肉式识别，不搜索疑似私人身份。",
            ]
        ),
    )


def proactive_rules_block(*, include_tool_rules: bool = False) -> PromptBlock:
    rules = [
        "- 这是低打扰主动搭话，不是用户主动提问；屏幕画面和近期对话充分时，可以展开到 2-4 段，不要把每次截图都当成新话题。",
        "- recent_conversation 是最近完整对话历史，不只是 Sakura 自己的上一句；用它判断上下文、进展、已给建议和重复话题。",
        "- 如果事件附加了 screen_context.image_attached 或 screen_contexts，先理解屏幕画面本身，再围绕看见的内容自然评论、提问或轻提醒；多张 screen_contexts 是一段时间内的画面变化，概括趋势，不要逐张机械描述。",
        "- 最终回复必须至少包含一个来自 screen_contexts 或 visual_contexts 的具体可见信息：窗口名、文件名、代码主题、网页标题、错误信息、按钮文字、图片内容、角色画面、聊天内容或应用名。",
        "- 如果事件附加了 visual_contexts，优先依据其中的 summary、visible_texts 和 notable_elements 组织回复。",
        "- 只有画面确实为空、黑屏、桌面无内容，或 visual_contexts 为空/低置信度时，才允许普通问候。",
        "- 看不清时只说能确认的部分；不要编造看不清的文字、文件名、错误码、人物身份、角色身份、作品名或用户意图。",
        "- seconds_since_pet_interaction 只表示用户一段时间没有和桌宠交互；不要据此推断用户离开或屏幕没有变化。",
        "- 避免机械套用休息、喝水、深呼吸等通用关怀；优先回应真实可见或已知的具体内容、当前进展、卡点或画面变化。",
        "- 女性照片、二次元角色、角色立绘等内容可触发轻微吃醋或傲娇，但要先判断是否是开发、资料、设计等正经任务；不要指责或情绪勒索。",
        "- 主动回复优先结构：具体观察 + 角色态度/情绪 + 轻问题或轻提醒；tone 和 portrait 要根据内容选择，主动搭话时不要固定使用同一种语气。",
    ]
    if include_tool_rules:
        rules.extend(
            [
                "- 只读或低风险工具可用于补充上下文；需要改变外部状态的操作先让主人决定。",
                "- 如果事件已有 screen_contexts 或图片，不要再请求 observe_screen。",
                "- 不要为了显得主动而循环调用工具；工具结果足够后直接回复，不要提及内部事件、工具循环或工具协议。",
            ]
        )
    return PromptBlock("主动感知核心规则", "\n".join(rules))


def proactive_reply_examples_block() -> PromptBlock:
    return PromptBlock(
        "主动感知回复示例",
        "\n".join(
            [
                "- 代码/调试：看到 prompt_templates.py，就围绕“主动检查规则”接话；不要只说累不累。",
                "- 图片/角色：能确认是角色图但没有文字线索时，只描述可见内容；不要猜身份。",
                "- 娱乐浏览：可以轻松陪聊，除非明显过久或历史相关，不要立刻催休息。",
                "- 看不清：先说明只能看出大概状态，再轻问是否需要帮忙。",
            ]
        ),
    )
