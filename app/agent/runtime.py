from __future__ import annotations

import json
from typing import Any

from app.agent.actions import AgentAction, AgentResult
from app.agent.memory import MemoryStore
from app.agent.tool_registry import ToolExecutionResult, ToolRegistry
from app.api_client import OpenAICompatibleClient
from app.chat_reply import ChatReply, parse_chat_reply


MAX_TOOL_CALLS_PER_TURN = 3


class AgentRuntime:
    """封装聊天决策链路，为后续工具调用和长期记忆留下扩展点。"""

    def __init__(
        self,
        api_client: OpenAICompatibleClient,
        system_prompt: str,
        reply_tones: list[str] | None = None,
        tools: ToolRegistry | None = None,
        memory: MemoryStore | None = None,
    ) -> None:
        self.api_client = api_client
        self.system_prompt = system_prompt
        self.reply_tones = [*reply_tones] if reply_tones is not None else []
        self.tools = tools or ToolRegistry()
        self.memory = memory or MemoryStore()

    def update_character(self, system_prompt: str, reply_tones: list[str] | None = None) -> None:
        """角色切换后同步系统提示词和可用语气列表。"""
        self.system_prompt = system_prompt
        self.reply_tones = [*reply_tones] if reply_tones is not None else []

    def handle_user_message(self, messages: list[dict[str, str]]) -> AgentResult:
        first_content = self.api_client.complete_raw(
            self._build_tool_planning_prompt(),
            messages,
            temperature=0.8,
        )
        agent_data = _load_json_object(first_content)
        if agent_data is None:
            return AgentResult(reply=parse_chat_reply(first_content))

        tool_calls = _parse_tool_calls(agent_data.get("tool_calls"))
        if not tool_calls:
            return AgentResult(reply=_parse_agent_reply(agent_data, first_content))

        execution_results = [
            self.tools.execute(call["name"], call["arguments"])
            for call in tool_calls[:MAX_TOOL_CALLS_PER_TURN]
        ]
        if len(tool_calls) > MAX_TOOL_CALLS_PER_TURN:
            execution_results.append(
                ToolExecutionResult(
                    tool_name="runtime",
                    success=False,
                    content="",
                    error=f"单轮最多执行 {MAX_TOOL_CALLS_PER_TURN} 个工具调用，后续调用已跳过。",
                )
            )

        final_reply = self.api_client.chat(
            self._build_final_reply_prompt(),
            [
                *messages,
                {"role": "assistant", "content": first_content},
                {
                    "role": "user",
                    "content": _format_tool_results_for_model(execution_results),
                },
            ],
            self.reply_tones,
        )
        return AgentResult(
            reply=final_reply,
            actions=[
                AgentAction(
                    type="tool_call",
                    payload=result.to_dict(),
                )
                for result in execution_results
            ],
        )

    def _build_tool_planning_prompt(self) -> str:
        tool_descriptions = json.dumps(
            self.tools.describe_tools(),
            ensure_ascii=False,
            indent=2,
        )
        tones = "、".join(tone for tone in self.reply_tones if tone.strip()) or "中性"
        return f"""
{self.system_prompt.strip()}

你现在可以作为桌面陪伴型 Agent 判断是否需要调用内部工具。
你必须只返回 JSON，不要使用 Markdown 代码块，不要输出额外解释。
如果需要工具，返回 reply 和 tool_calls；如果不需要工具，tool_calls 返回空数组或省略。

可用工具：
{tool_descriptions}

返回格式：
{{
  "reply": {{
    "segments": [
      {{"ja": "日文原文", "zh": "中文译文", "tone": "中性"}}
    ]
  }},
  "tool_calls": [
    {{"name": "工具名", "arguments": {{}}}}
  ]
}}

要求：
- tone 只能从这些类别中选择：{tones}。
- ja 中只写夜乃桜要说出口的日文原文，必须是日语，适合直接交给日语 TTS 朗读。
- zh 中只写 ja 对应的自然中文译文，必须是中文。
- 如果工具可以帮助完成用户请求，优先用 tool_calls 表达要执行的动作。
- 不要臆造工具名；只能使用上面列出的工具。
""".strip()

    def _build_final_reply_prompt(self) -> str:
        return f"""
{self.system_prompt.strip()}

你会收到上一轮工具调用结果。请基于这些结果给用户最终回复。
不要再次请求工具，不要提及内部 JSON、工具协议或实现细节。
""".strip()


def _load_json_object(content: str) -> dict[str, Any] | None:
    text = _strip_code_fence(content.strip())
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _strip_code_fence(content: str) -> str:
    if not content.startswith("```"):
        return content
    lines = content.splitlines()
    if len(lines) >= 3 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return content


def _parse_tool_calls(raw_tool_calls: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_tool_calls, list):
        return []

    tool_calls: list[dict[str, Any]] = []
    for item in raw_tool_calls:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        arguments = item.get("arguments", {})
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(arguments, dict):
            arguments = {}
        tool_calls.append({"name": name.strip(), "arguments": arguments})
    return tool_calls


def _parse_agent_reply(agent_data: dict[str, Any], fallback_content: str) -> ChatReply:
    reply_data = agent_data.get("reply")
    if isinstance(reply_data, dict):
        return parse_chat_reply(json.dumps(reply_data, ensure_ascii=False))
    return parse_chat_reply(fallback_content)


def _format_tool_results_for_model(results: list[ToolExecutionResult]) -> str:
    return (
        "工具执行结果如下，请据此给用户最终回复：\n"
        + json.dumps(
            [result.to_dict() for result in results],
            ensure_ascii=False,
            indent=2,
        )
    )
