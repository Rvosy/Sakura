from __future__ import annotations

from app.agent.session_state_context import (
    SESSION_DIGEST_INJECT_MAX_RECENT_MESSAGES,
    SESSION_STATE_TOKEN_BUDGET,
    build_session_state_fragment,
)
from app.storage.chat_history import ChatHistoryEntry
from app.storage.history_digest import (
    MAX_DIGEST_MESSAGES,
    clean_recent_dialogue,
)


def _entry(role: str, content: str, *, translation: str = "") -> ChatHistoryEntry:
    return ChatHistoryEntry(
        created_at="2026-06-20T12:00:00+08:00",
        role=role,
        content=content,
        translation=translation,
    )


class TestCleanRecentDialogue:
    def test_keeps_only_user_and_assistant_text(self) -> None:
        entries = [
            _entry("system", "ignored"),
            _entry("user", "帮我导出 CSV"),
            _entry("assistant", "好的，已经导出。"),
        ]
        lines = clean_recent_dialogue(entries)
        assert [(line.role, line.content) for line in lines] == [
            ("user", "帮我导出 CSV"),
            ("assistant", "好的，已经导出。"),
        ]

    def test_strips_visual_markers(self) -> None:
        entries = [
            _entry(
                "user",
                "看看这个 [Sakura 已附加手动框选截图，视觉记录 visual_id=abc123]",
            ),
        ]
        lines = clean_recent_dialogue(entries)
        assert lines
        assert "visual_id" not in lines[0].content
        assert "看看这个" in lines[0].content

    def test_prefers_assistant_translation(self) -> None:
        entries = [_entry("assistant", "原文", translation="译文内容")]
        lines = clean_recent_dialogue(entries)
        assert lines[0].content == "译文内容"

    def test_drops_low_signal_user_and_process_assistant(self) -> None:
        entries = [
            _entry("user", "嗯"),
            _entry("assistant", "稍等，处理中"),
            _entry("user", "把方案改成 B"),
        ]
        lines = clean_recent_dialogue(entries)
        assert [line.content for line in lines] == ["把方案改成 B"]

    def test_limits_to_recent_tail(self) -> None:
        entries = [_entry("user", f"消息{i}") for i in range(MAX_DIGEST_MESSAGES + 5)]
        lines = clean_recent_dialogue(entries)
        assert len(lines) == MAX_DIGEST_MESSAGES
        assert lines[-1].content == f"消息{MAX_DIGEST_MESSAGES + 4}"

    def test_empty_history(self) -> None:
        assert clean_recent_dialogue([]) == []


class TestBuildSessionStateFragment:
    def test_renders_recent_dialogue_as_untrusted_fact(self) -> None:
        entries = [
            _entry("user", "继续执行计划"),
            _entry("assistant", "好的，记下下一步。"),
        ]
        fragment = build_session_state_fragment(entries, recent_message_count=1)
        assert fragment is not None
        assert fragment.source == "session_state"
        assert fragment.trust == "untrusted"
        assert "最近会话状态" in fragment.content
        assert "继续执行计划" in fragment.content
        assert "用户：" in fragment.content
        assert "Sakura：" in fragment.content

    def test_skips_when_live_window_deep(self) -> None:
        entries = [_entry("user", "继续执行计划")]
        fragment = build_session_state_fragment(
            entries,
            recent_message_count=SESSION_DIGEST_INJECT_MAX_RECENT_MESSAGES,
        )
        assert fragment is None

    def test_none_when_no_history(self) -> None:
        assert build_session_state_fragment([], recent_message_count=0) is None

    def test_excludes_current_input_from_history(self) -> None:
        entries = [_entry("user", "上次任务"), _entry("user", "本轮问题")]
        fragment = build_session_state_fragment(entries, current_input="本轮问题")
        assert fragment is not None
        assert "上次任务" in fragment.content
        assert "本轮问题" not in fragment.content

    def test_token_budget_keeps_newest_messages(self) -> None:
        entries = [_entry("user", f"消息{i}：" + "中" * 220) for i in range(MAX_DIGEST_MESSAGES)]
        fragment = build_session_state_fragment(entries)
        assert fragment is not None
        assert fragment.token_budget == SESSION_STATE_TOKEN_BUDGET
        assert "消息0：" not in fragment.content
        assert f"消息{MAX_DIGEST_MESSAGES - 1}：" in fragment.content
