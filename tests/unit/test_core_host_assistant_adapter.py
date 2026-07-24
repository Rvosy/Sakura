from __future__ import annotations

import hashlib
import http.client
import shutil
import socket
import ssl
import urllib.request
from pathlib import Path
from threading import Event, Thread
from typing import Callable

import pytest

from app.agent.runtime import AgentRuntime
from app.agent.tools import ToolRegistry
from app.config import character_loader
from app.core.cancellation import OperationCancelled
from app.core.chat_pipeline import ChatPipeline
from app.core_host import assistant_adapter as adapter_module
from app.core_host.assistant_adapter import (
    AssistantAdapter,
    DisabledMemory,
    project_current_character_summary,
)
from app.llm.api_client import OpenAICompatibleClient


FIXTURE_ROOT = (
    Path(__file__).parents[1] / "fixtures" / "runtime_v2" / "wp_3_01" / "ready"
)


def _fresh_root(tmp_path: Path) -> Path:
    root = tmp_path / "app-root"
    shutil.copytree(FIXTURE_ROOT, root)
    return root


def _file_snapshot(root: Path) -> dict[str, tuple[str, int]]:
    return {
        path.relative_to(root).as_posix(): (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_mtime_ns,
        )
        for path in root.rglob("*")
        if path.is_file()
    }


def _forbidden_call(name: str) -> Callable[..., None]:
    def fail(*_args: object, **_kwargs: object) -> None:
        pytest.fail(f"startup attempted forbidden call: {name}")

    return fail


def _install_startup_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    for owner, name in (
        (socket, "getaddrinfo"),
        (socket, "create_connection"),
        (socket.socket, "connect"),
        (urllib.request, "urlopen"),
        (ssl, "create_default_context"),
        (http.client.HTTPConnection, "connect"),
        (http.client.HTTPSConnection, "connect"),
    ):
        monkeypatch.setattr(owner, name, _forbidden_call(f"{owner}.{name}"))

    for name in (
        "test_connection",
        "list_models",
        "chat",
        "complete_raw",
        "complete_with_tools",
        "_post_chat_completions_with_compatibility_fallbacks",
        "_post_chat_completions",
        "_send_with_retries",
    ):
        monkeypatch.setattr(
            OpenAICompatibleClient,
            name,
            _forbidden_call(f"OpenAICompatibleClient.{name}"),
        )

    for name in (
        "run_user_message",
        "run_confirmed_action",
        "run_cancelled_action",
        "run_event",
    ):
        monkeypatch.setattr(ChatPipeline, name, _forbidden_call(f"ChatPipeline.{name}"))


def _write_corrupt_package(root: Path, package_id: str = "broken") -> Path:
    package_dir = root / "characters" / package_id
    package_dir.mkdir()
    manifest = package_dir / "character.json"
    manifest.write_text('{"id": "broken", "display_name": "PRIVATE_PROMPT"}', encoding="utf-8")
    return manifest


def test_initialize_builds_exact_real_session_without_calls_or_fixture_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _fresh_root(tmp_path)
    before = _file_snapshot(root)
    _install_startup_guards(monkeypatch)

    result = AssistantAdapter(root).initialize(Event())

    assert (result.state, result.code, result.retryable) == ("ready", "READY", False)
    assert result.session is not None
    assert isinstance(result.session.provider, OpenAICompatibleClient)
    assert isinstance(result.session.runtime, AgentRuntime)
    assert isinstance(result.session.runtime.tools, ToolRegistry)
    assert result.session.runtime.tools.all() == []
    assert isinstance(result.session.runtime.memory, DisabledMemory)
    assert bool(result.session.runtime.memory) is True
    assert isinstance(result.session.pipeline, ChatPipeline)
    assert result.session.pipeline.agent_runtime is result.session.runtime
    assert result.session.runtime.memory_recall.memory is result.session.runtime.memory
    assert result.session.runtime.character_id == result.session.character.id
    assert result.session.runtime.character_name == result.session.character.display_name
    assert _file_snapshot(root) == before


def test_public_character_projector_has_exact_five_keys_and_copies_lists(
    tmp_path: Path,
) -> None:
    result = AssistantAdapter(_fresh_root(tmp_path)).initialize(Event())
    assert result.session is not None
    profile = result.session.character

    summary = project_current_character_summary(profile)

    assert summary == {
        "id": "sakura",
        "displayName": "Sakura Fixture",
        "initialMessage": "Fixture greeting.",
        "replyTones": ["neutral"],
        "portraitChoices": ["neutral"],
    }
    assert set(summary) == {
        "id",
        "displayName",
        "initialMessage",
        "replyTones",
        "portraitChoices",
    }
    assert summary["replyTones"] is not profile.reply_tones
    first_reply_tones = list(summary["replyTones"])
    profile.reply_tones.append("later")
    assert summary["replyTones"] == first_reply_tones


def test_invalid_current_uses_sakura_fallback_with_frozen_precedence(tmp_path: Path) -> None:
    root = _fresh_root(tmp_path)
    (root / "data" / "config" / "characters.yaml").write_text(
        "current_character_id: missing\n",
        encoding="utf-8",
    )

    result = AssistantAdapter(root).initialize(Event())

    assert (result.state, result.code, result.retryable) == (
        "degraded",
        "CHARACTER_FALLBACK_APPLIED",
        False,
    )
    assert result.session is not None
    assert result.session.character.id == "sakura"
    assert result.current_character_summary == project_current_character_summary(
        result.session.character
    )


def test_optional_corrupt_package_is_skipped_only_when_current_remains_valid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _fresh_root(tmp_path)
    _write_corrupt_package(root)
    legacy_events: list[object] = []
    monkeypatch.setattr(character_loader, "log_event", lambda *args: legacy_events.append(args))

    result = AssistantAdapter(root).initialize(Event())

    assert (result.state, result.code, result.retryable) == (
        "degraded",
        "OPTIONAL_CHARACTER_SKIPPED",
        False,
    )
    assert result.session is not None
    assert result.session.character.id == "sakura"
    assert legacy_events == []
    stderr = capsys.readouterr().err
    assert stderr == "A character package was skipped during initialization.\n"
    assert str(root) not in stderr
    assert "PRIVATE_PROMPT" not in stderr


def test_invalid_current_beats_different_optional_corruption_and_builds_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _fresh_root(tmp_path)
    (root / "data" / "config" / "characters.yaml").write_text(
        "current_character_id: missing\n",
        encoding="utf-8",
    )
    _write_corrupt_package(root)
    real_pipeline = adapter_module.ChatPipeline
    constructions: list[AgentRuntime] = []

    def build_pipeline(runtime: AgentRuntime) -> ChatPipeline:
        constructions.append(runtime)
        return real_pipeline(runtime)

    monkeypatch.setattr(adapter_module, "ChatPipeline", build_pipeline)

    result = AssistantAdapter(root).initialize(Event())

    assert (result.state, result.code) == ("degraded", "CHARACTER_FALLBACK_APPLIED")
    assert result.current_character_summary is not None
    assert result.current_character_summary["id"] == "sakura"
    assert result.session is not None
    assert len(constructions) == 1


def test_no_valid_character_is_setup_required_without_session(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _fresh_root(tmp_path)
    (root / "characters" / "sakura" / "character.json").write_text(
        "not-json",
        encoding="utf-8",
    )

    result = AssistantAdapter(root).initialize(Event())

    assert (result.state, result.code, result.retryable) == (
        "setup_required",
        "CHARACTER_SETUP_REQUIRED",
        False,
    )
    assert result.session is None
    assert result.current_character_summary is None
    stderr = capsys.readouterr().err
    assert stderr == "A character package was skipped during initialization.\n"
    assert str(root) not in stderr


def test_core_config_problem_is_forwarded_without_constructing_session(tmp_path: Path) -> None:
    root = _fresh_root(tmp_path)
    (root / "data" / "config" / "system_config.yaml").unlink()

    result = AssistantAdapter(root).initialize(Event())

    assert (result.state, result.code, result.retryable) == (
        "setup_required",
        "CORE_CONFIG_SETUP_REQUIRED",
        False,
    )
    assert result.message == "Core configuration setup is required."
    assert result.current_character_summary is None
    assert result.session is None


class _Tracked:
    def __init__(self, name: str, closed: list[str], *, fail_close: bool = False) -> None:
        self.name = name
        self.closed = closed
        self.fail_close = fail_close

    def close(self) -> None:
        self.closed.append(self.name)
        if self.fail_close:
            raise RuntimeError("PRIVATE_CLOSE_ERROR")


def _install_tracked_factories(
    monkeypatch: pytest.MonkeyPatch,
    closed: list[str],
    *, fail_at: str | None = None,
    fail_close_at: str | None = None,
) -> None:
    def factory(name: str):  # type: ignore[no-untyped-def]
        def build(*_args: object, **_kwargs: object) -> _Tracked:
            if fail_at == name:
                raise RuntimeError("PRIVATE_CONSTRUCTION_ERROR")
            return _Tracked(name, closed, fail_close=fail_close_at == name)

        return build

    for attribute, name in (
        ("OpenAICompatibleClient", "provider"),
        ("ToolRegistry", "tools"),
        ("DisabledMemory", "memory"),
        ("AgentRuntime", "runtime"),
        ("ChatPipeline", "pipeline"),
    ):
        monkeypatch.setattr(adapter_module, attribute, factory(name))


def test_mid_construction_failure_closes_successful_boundaries_in_reverse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[str] = []
    _install_tracked_factories(monkeypatch, closed, fail_at="pipeline")

    result = AssistantAdapter(_fresh_root(tmp_path)).initialize(Event())

    assert (result.state, result.code, result.retryable) == (
        "failed",
        "ASSISTANT_INITIALIZATION_FAILED",
        False,
    )
    assert result.session is None
    assert result.current_character_summary is None
    assert closed == ["runtime", "memory", "tools", "provider"]
    assert "PRIVATE_CONSTRUCTION_ERROR" not in result.message


def test_close_is_locked_idempotent_reverse_order_and_continues_after_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    closed: list[str] = []
    _install_tracked_factories(monkeypatch, closed, fail_close_at="runtime")
    adapter = AssistantAdapter(_fresh_root(tmp_path))
    result = adapter.initialize(Event())
    assert result.session is not None

    threads = [Thread(target=adapter.close) for _index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    adapter.close()

    assert closed == ["pipeline", "runtime", "memory", "tools", "provider"]
    stderr = capsys.readouterr().err
    assert stderr == "An Assistant resource failed to close cleanly.\n"
    assert "PRIVATE_CLOSE_ERROR" not in stderr


def test_cancellation_before_read_and_after_constructed_boundary_cleans_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancelled = Event()
    cancelled.set()
    with pytest.raises(OperationCancelled):
        AssistantAdapter(_fresh_root(tmp_path)).initialize(cancelled)

    root = _fresh_root(tmp_path / "second")
    closed: list[str] = []

    class CancellingProvider(_Tracked):
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            super().__init__("provider", closed)
            cancelled.set()

    monkeypatch.setattr(adapter_module, "OpenAICompatibleClient", CancellingProvider)
    cancelled.clear()
    with pytest.raises(OperationCancelled):
        AssistantAdapter(root).initialize(cancelled)

    assert closed == ["provider"]


def test_disabled_memory_contract() -> None:
    memory = DisabledMemory()

    assert memory
    assert memory.search_memory({"query": "anything"}) == {
        "status": "disabled",
        "memories": [],
    }
    assert memory.search_memory({}, wait=True) == {"status": "disabled", "memories": []}
    assert memory.summary() == ""
    assert memory.close() is None
