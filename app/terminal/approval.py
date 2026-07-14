from __future__ import annotations

from typing import Any

from app.agent.actions import PendingToolAction
from app.llm.chat_reply import ChatSegment


TERMINAL_APPROVAL_TOOL_NAMES = frozenset({"terminal_exec", "terminal_write"})


def is_terminal_approval(action: PendingToolAction | None) -> bool:
    return action is not None and action.tool_name in TERMINAL_APPROVAL_TOOL_NAMES


def terminal_approval_payload(action: PendingToolAction) -> dict[str, Any]:
    """Build the bounded display-only payload consumed by the Tauri approval UI."""
    raw_command = action.arguments.get("command")
    command = (
        [item for item in raw_command if isinstance(item, str)]
        if isinstance(raw_command, list)
        else []
    )
    return {
        "id": action.id,
        "tool_name": action.tool_name,
        "summary": action.summary or action.tool_name,
        "command": command,
        "cwd": action.working_directory,
        "risk_level": action.risk_level,
        "allowed_scopes": [scope.value for scope in action.allowed_approval_scopes],
    }


def suppress_segment_tts(segments: list[ChatSegment]) -> list[ChatSegment]:
    return [
        ChatSegment(
            text=segment.text,
            tone=segment.tone,
            translation=segment.translation,
            portrait=segment.portrait,
            suppress_tts=True,
        )
        for segment in segments
    ]
