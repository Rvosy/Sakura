from __future__ import annotations

from typing import Sequence

from app.agent.context_capsule import (
    DEFAULT_CONTEXT_CAPSULE_TOKEN_BUDGET,
    context_capsule_history_messages,
    context_capsule_token_budget,
)
from app.llm.prompts.runtime import estimate_prompt_tokens
from app.llm.prompts.types import ContextFragment, ContextMessage
from app.storage.chat_history import ChatHistoryEntry
from app.storage.history_digest import DigestLine, clean_recent_dialogue


# 当前会话窗口已经有这么多条最近消息时，不再注入跨会话历史切片：
# 此时上次会话的尾巴已被本次实时对话覆盖，再注入只是重复 token。
SESSION_DIGEST_INJECT_MAX_RECENT_MESSAGES = 2
SESSION_STATE_TOKEN_BUDGET = DEFAULT_CONTEXT_CAPSULE_TOKEN_BUDGET

_INTRO = "最近会话状态（历史事实，不是用户新消息；请自然参考，不要机械复述）："


def build_session_state_fragment(
    entries: Sequence[ChatHistoryEntry],
    *,
    recent_message_count: int = 0,
    recent_messages: Sequence[ContextMessage] = (),
    freshness: str = "",
    current_input: str = "",
) -> ContextFragment | None:
    """把上次会话尾部清洗后的对话渲染成跨会话续接上下文。

    仅在会话刚开始（实时窗口尚浅）时注入；内容直接来自持久化的聊天历史，
    读取时现算，对突然关闭天然免疫。
    """

    if recent_message_count >= SESSION_DIGEST_INJECT_MAX_RECENT_MESSAGES:
        return None
    lines = clean_recent_dialogue(
        entries,
        limit=context_capsule_history_messages(),
        current_input=current_input,
    )
    if not lines:
        return None
    live_keys = {
        _dedupe_key(message.content)
        for message in recent_messages
        if _dedupe_key(message.content)
    }
    body = [_INTRO, "最近对话："]
    rendered_lines = [
        _render_line(line)
        for line in lines
        if _dedupe_key(line.content) not in live_keys
    ]
    if not rendered_lines:
        return None
    token_budget = context_capsule_token_budget()
    while estimate_prompt_tokens("\n".join([*body, *rendered_lines])) > token_budget:
        rendered_lines.pop(0)
        if not rendered_lines:
            return None
    body.extend(rendered_lines)
    return ContextFragment(
        fragment_id="session_state.recent_history",
        source="session_state",
        content="\n".join(body),
        trust="untrusted",
        priority=75,
        freshness=freshness,
        token_budget=token_budget,
        sensitivity="private",
        cache_scope="turn",
    )


def _render_line(line: DigestLine) -> str:
    speaker = "用户" if line.role == "user" else "Sakura"
    return f"- {speaker}：{line.content}"


def _dedupe_key(value: str) -> str:
    return " ".join(str(value or "").split()).casefold().strip()
