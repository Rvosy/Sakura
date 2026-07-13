from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.agent.actions import ApprovalScope, PendingToolAction
from app.agent.tools import ToolRegistry
from app.terminal.manager import (
    TerminalBusyError,
    TerminalError,
    TerminalManager,
    normalize_terminal_command,
    sanitize_terminal_output,
)
from app.terminal.models import (
    TerminalReadResult,
    TerminalSpawnResult,
    TerminalState,
)
from app.terminal.settings import TerminalSettings
from app.terminal.tools import register_terminal_tools


@dataclass
class FakeTerminalTransport:
    state: TerminalState = TerminalState.RUNNING
    session_id: str = "session-1"
    output: bytes = b"hello\n"
    calls: list[tuple[str, object]] = field(default_factory=list)
    fail_read: bool = False

    def spawn(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(("spawn", kwargs))
        return TerminalSpawnResult(
            self.session_id,
            self.state,
            self.output,
            cursor=len(self.output),
            exit_code=0 if self.state is TerminalState.EXITED else None,
        )

    def read(self, *, session_id: str, cursor: int, max_bytes: int) -> TerminalReadResult:
        self.calls.append(("read", (session_id, cursor, max_bytes)))
        if self.fail_read:
            raise RuntimeError("transport crashed")
        return TerminalReadResult(
            session_id,
            self.state,
            self.output,
            cursor=len(self.output),
        )

    def write(self, *, session_id: str, data: bytes) -> TerminalReadResult:
        self.calls.append(("write", (session_id, data)))
        return TerminalReadResult(session_id, self.state, b"", cursor=len(self.output))

    def stop(self, *, session_id: str) -> TerminalReadResult:
        self.calls.append(("stop", session_id))
        self.state = TerminalState.STOPPED
        return TerminalReadResult(session_id, self.state, b"", cursor=len(self.output))

    def show(self) -> None:
        self.calls.append(("show", None))

    def shutdown(self, timeout_ms: int) -> None:
        self.calls.append(("shutdown", timeout_ms))


def _manager(tmp_path: Path) -> tuple[TerminalManager, FakeTerminalTransport]:
    manager = TerminalManager(TerminalSettings(enabled=True, default_cwd=str(tmp_path)))
    transport = FakeTerminalTransport()
    manager.bind_transport(transport)
    return manager, transport


def test_terminal_settings_default_off_and_invalid_cwd_falls_back(tmp_path: Path) -> None:
    settings = TerminalSettings(enabled=True, default_cwd=str(tmp_path / "missing")).normalized(home=tmp_path)

    assert settings.enabled
    assert settings.default_cwd == str(tmp_path.resolve())
    assert not TerminalSettings().enabled


def test_terminal_command_requires_explicit_argv() -> None:
    with pytest.raises(TerminalError, match="argv"):
        normalize_terminal_command("echo hello")
    with pytest.raises(TerminalError, match="NUL"):
        normalize_terminal_command(["echo", "bad\x00arg"])


def test_disabled_terminal_tools_are_not_visible_or_discoverable(tmp_path: Path) -> None:
    manager = TerminalManager(TerminalSettings(enabled=False, default_cwd=str(tmp_path)))
    registry = ToolRegistry()
    register_terminal_tools(registry, manager)

    assert not {
        item["function"]["name"]
        for item in registry.describe_openai_tools(
            allowed_capabilities=registry.enabled_capabilities,
            active_groups=registry.default_active_groups(),
        )
    } & {"terminal_exec", "terminal_read", "terminal_write", "terminal_stop"}
    assert registry.search_tools({"keyword": "terminal"}) == []
    assert "terminal" not in {item["group"] for item in registry.list_tool_groups({})}


def test_terminal_exec_always_waits_for_confirmation_with_free_access(tmp_path: Path) -> None:
    manager, _transport = _manager(tmp_path)
    registry = ToolRegistry()
    register_terminal_tools(registry, manager)
    registry.set_free_access_enabled(True)

    pending = registry.prepare_or_execute(
        "terminal_exec",
        {"command": ["printf", "hello"], "cwd": str(tmp_path)},
    )

    assert isinstance(pending, PendingToolAction)
    assert pending.risk_level == "low"
    assert pending.allows_scope(ApprovalScope.PROCESS)
    assert pending.summary == "printf hello"
    assert pending.to_log_dict()["arguments"] == {"redacted": True}


def test_unknown_and_shell_commands_do_not_offer_process_scope(tmp_path: Path) -> None:
    manager, _transport = _manager(tmp_path)
    registry = ToolRegistry()
    register_terminal_tools(registry, manager)

    unknown = registry.prepare_or_execute("terminal_exec", {"command": ["my-tool"]})
    shell = registry.prepare_or_execute("terminal_exec", {"command": ["zsh", "-lc", "pwd"]})

    assert isinstance(unknown, PendingToolAction)
    assert unknown.risk_level == "medium"
    assert unknown.allowed_approval_scopes == (ApprovalScope.ONCE,)
    assert isinstance(shell, PendingToolAction)
    assert shell.risk_level == "high"
    assert shell.allowed_approval_scopes == (ApprovalScope.ONCE,)


def test_process_approval_allows_only_same_session_write(tmp_path: Path) -> None:
    manager, transport = _manager(tmp_path)
    registry = ToolRegistry()
    register_terminal_tools(registry, manager)
    pending = registry.prepare_or_execute("terminal_exec", {"command": ["printf", "hello"]})
    assert isinstance(pending, PendingToolAction)

    result = registry.execute_confirmed(pending, ApprovalScope.PROCESS)

    assert result.success
    assert result.log_content is False
    assert result.to_log_dict()["content"]["redacted"] is True
    assert manager.has_process_grant("session-1")
    write = registry.prepare_or_execute(
        "terminal_write",
        {"session_id": "session-1", "data": "next", "append_newline": True},
    )
    assert not isinstance(write, PendingToolAction)
    assert write.success
    assert ("write", ("session-1", b"next\n")) in transport.calls

    other = registry.prepare_or_execute(
        "terminal_write",
        {"session_id": "session-2", "data": "next"},
    )
    assert isinstance(other, PendingToolAction)


def test_stopped_or_crashed_session_revokes_process_grant(tmp_path: Path) -> None:
    manager, transport = _manager(tmp_path)
    first = manager.execute({"command": ["printf", "hello"]})
    manager.register_exec_approval(ApprovalScope.PROCESS, first)
    assert manager.has_process_grant("session-1")

    manager.stop_session({"session_id": "session-1"})
    assert not manager.has_process_grant("session-1")

    transport.state = TerminalState.RUNNING
    manager.execute({"command": ["printf", "again"]})
    manager.register_exec_approval(
        ApprovalScope.PROCESS,
        {"session_id": "session-1"},
    )
    transport.fail_read = True
    with pytest.raises(RuntimeError, match="crashed"):
        manager.read({"session_id": "session-1"})
    assert manager.current_session_id == ""
    assert not manager.has_process_grant("session-1")


def test_terminal_output_is_bounded_cleaned_and_redacted() -> None:
    output = (
        b"\x1b[31mred\x1b[0m\n"
        b"API_KEY=abc123\npassword: hunter2\nAuthorization: Bearer secret-token\n"
        + b"x" * 20_000
    )

    cleaned = sanitize_terminal_output(output)

    assert "\x1b" not in cleaned
    assert "abc123" not in cleaned
    assert "hunter2" not in cleaned
    assert "secret-token" not in cleaned
    assert "[REDACTED]" in cleaned
    assert len(cleaned) <= 6000


def test_disabling_terminal_with_running_session_is_rejected(tmp_path: Path) -> None:
    manager, _transport = _manager(tmp_path)
    manager.execute({"command": ["printf", "hello"]})

    with pytest.raises(TerminalBusyError, match="先停止"):
        manager.update_settings(TerminalSettings(enabled=False, default_cwd=str(tmp_path)))
