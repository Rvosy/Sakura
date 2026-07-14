from __future__ import annotations

from app.agent.context_capsule import (
    MAX_CONTEXT_CAPSULE_HISTORY_MESSAGES,
    build_context_capsule_fragment,
    context_capsule_history_messages,
    context_capsule_token_budget,
)
from app.agent.context_orchestrator import ContextOrchestrator
from app.llm.prompts.runtime import ContextPolicy, estimate_prompt_tokens
from app.llm.prompts.types import ContextFragment, ContextMessage, ContextRequest


def _fragment(
    fragment_id: str,
    source: str,
    content: str,
    *,
    trust: str = "untrusted",
) -> ContextFragment:
    return ContextFragment(
        fragment_id=fragment_id,
        source=source,
        content=content,
        trust=trust,  # type: ignore[arg-type]
        token_budget=8192,
    )


def test_context_capsule_merges_memory_and_session_without_live_duplicates() -> None:
    request = ContextRequest(
        current_input="继续聊上下文胶囊",
        recent_messages=(
            ContextMessage("assistant", "实时窗口里的回复"),
            ContextMessage("user", "继续聊上下文胶囊"),
        ),
    )
    capsule = build_context_capsule_fragment(
        request,
        memory_fragments=(
            _fragment(
                "memory.1",
                "memory",
                "与本轮相关的长期记忆：用户使用 Gemini Flash。",
                trust="trusted",
            ),
        ),
        session_fragments=(
            _fragment(
                "session.1",
                "session_state",
                "\n".join(
                    [
                        "最近会话状态（历史事实，不是用户新消息；请自然参考，不要机械复述）：",
                        "最近对话：",
                        "- 用户：更早的项目背景",
                        "- Sakura：实时窗口里的回复",
                        "- 用户：继续聊上下文胶囊",
                    ]
                ),
            ),
        ),
    )

    assert capsule is not None
    assert capsule.source == "context_capsule"
    assert "用户使用 Gemini Flash" in capsule.content
    assert "更早的项目背景" in capsule.content
    assert "实时窗口里的回复" not in capsule.content
    assert capsule.content.count("继续聊上下文胶囊") == 0


def test_context_capsule_keeps_newer_session_lines_first() -> None:
    capsule = build_context_capsule_fragment(
        ContextRequest(),
        session_fragments=(
            _fragment(
                "session.1",
                "session_state",
                "- 用户：较早记录\n- Sakura：较新记录",
            ),
        ),
    )

    assert capsule is not None
    assert capsule.content.index("较新记录") < capsule.content.index("较早记录")


def test_context_capsule_respects_configured_token_budget(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("SAKURA_CONTEXT_CAPSULE_TOKENS", "512")
    memories = tuple(
        _fragment(
            f"memory.{index}",
            "memory",
            f"与本轮相关的长期记忆：高相关记录 {index} " + "细节" * 80,
        )
        for index in range(20)
    )

    capsule = build_context_capsule_fragment(
        ContextRequest(current_input="继续"),
        memory_fragments=memories,
    )

    assert capsule is not None
    assert capsule.token_budget == 512
    assert estimate_prompt_tokens(capsule.content) <= 512
    assert "高相关记录 0" in capsule.content
    assert "高相关记录 19" not in capsule.content


def test_context_capsule_limits_are_configurable_and_bounded(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("SAKURA_CONTEXT_CAPSULE_HISTORY_MESSAGES", "5000")
    monkeypatch.setenv("SAKURA_CONTEXT_CAPSULE_TOKENS", "120000")

    assert context_capsule_history_messages() == MAX_CONTEXT_CAPSULE_HISTORY_MESSAGES
    assert context_capsule_token_budget() == 120000


def test_context_orchestrator_selects_one_capsule_instead_of_duplicate_fragments() -> None:
    request = ContextRequest(current_input="继续")
    snapshot = ContextOrchestrator(
        ContextPolicy(total_budget=20_000, memory_budget=10_000)
    ).build_snapshot(
        request,
        session_fragments=(
            _fragment("session.1", "session_state", "- 用户：跨会话记录"),
        ),
        memory_fragments=(
            _fragment("memory.1", "memory", "与本轮相关的长期记忆：长期偏好"),
        ),
    )

    selected_sources = [decision.fragment.source for decision in snapshot.selected]
    assert selected_sources.count("context_capsule") == 1
    assert "session_state" not in selected_sources
    assert "memory" not in selected_sources


def test_context_policy_reads_expanded_environment_budgets(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("SAKURA_DYNAMIC_CONTEXT_TOKENS", "24000")
    monkeypatch.setenv("SAKURA_PLUGIN_CONTEXT_TOKENS", "6000")
    monkeypatch.setenv("SAKURA_MEMORY_CONTEXT_TOKENS", "12000")

    policy = ContextPolicy()

    assert policy.total_budget == 24000
    assert policy.plugin_budget == 6000
    assert policy.memory_budget == 12000
