from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import threading
import time

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
from app.terminal.approval import suppress_segment_tts, terminal_approval_payload
from app.terminal.models import (
    TerminalReadResult,
    TerminalSpawnResult,
    TerminalState,
)
from app.terminal.settings import TerminalSettings
from app.terminal.tauri_process import (
    TAURI_TERMINAL_BIN_ENV,
    TauriTerminalError,
    _terminal_result,
    resolve_tauri_terminal_binary,
)
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


def test_terminal_approval_payload_exposes_display_fields_without_continuation_context(
    tmp_path: Path,
) -> None:
    manager, _transport = _manager(tmp_path)
    registry = ToolRegistry()
    register_terminal_tools(registry, manager)
    pending = registry.prepare_or_execute(
        "terminal_exec",
        {"command": ["printf", "hello"], "cwd": str(tmp_path)},
    )

    assert isinstance(pending, PendingToolAction)
    payload = terminal_approval_payload(pending)

    assert payload == {
        "id": pending.id,
        "tool_name": "terminal_exec",
        "summary": "printf hello",
        "command": ["printf", "hello"],
        "cwd": str(tmp_path.resolve()),
        "risk_level": "low",
        "allowed_scopes": ["once", "process"],
    }
    assert "continuation_messages" not in payload


def test_suppress_segment_tts_preserves_visible_text() -> None:
    from app.llm.chat_reply import ChatSegment

    segments = [ChatSegment(ja="確認して。", zh="请确认", tone="请求", portrait="站立")]

    suppressed = suppress_segment_tts(segments)

    assert suppressed[0].text == "確認して。"
    assert suppressed[0].translation == "请确认"
    assert suppressed[0].suppress_tts


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
        b"\x1b]0;malicious title\x07"
        b"API_KEY=abc123\npassword: hunter2\nAuthorization: Bearer secret-token\n"
        + b"x" * 20_000
    )

    cleaned = sanitize_terminal_output(output)

    assert "\x1b" not in cleaned
    assert "malicious title" not in cleaned
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


def test_disabling_idle_terminal_closes_host_and_can_be_reenabled(tmp_path: Path) -> None:
    manager, transport = _manager(tmp_path)

    manager.update_settings(TerminalSettings(enabled=False, default_cwd=str(tmp_path)))
    manager.update_settings(TerminalSettings(enabled=True, default_cwd=str(tmp_path)))

    assert ("shutdown", 250) in transport.calls
    assert manager.enabled


def test_transport_failure_revokes_process_grant(tmp_path: Path) -> None:
    manager, _transport = _manager(tmp_path)
    result = manager.execute({"command": ["printf", "hello"]})
    manager.register_exec_approval(ApprovalScope.PROCESS, result)

    assert manager.has_process_grant("session-1")
    manager.transport_failed("host crashed")
    assert not manager.has_process_grant("session-1")
    assert manager.current_session_id == ""


def test_tauri_terminal_binary_resolver_requires_executable_on_posix(tmp_path: Path) -> None:
    binary = tmp_path / "sakura-terminal"
    binary.write_text("binary", encoding="utf-8")
    environment = {TAURI_TERMINAL_BIN_ENV: str(binary)}

    assert resolve_tauri_terminal_binary(
        tmp_path,
        environ=environment,
        platform="darwin",
    ) is None
    binary.chmod(0o700)
    assert resolve_tauri_terminal_binary(
        tmp_path,
        environ=environment,
        platform="darwin",
    ) == binary


def test_tauri_terminal_result_decodes_session_payload() -> None:
    result = _terminal_result(
        {
            "session_id": "session-1",
            "state": "running",
            "output_b64": "aGVsbG8=",
            "cursor": 5,
            "exit_code": None,
            "truncated": False,
        }
    )

    assert result["output"] == b"hello"
    assert result["state"] is TerminalState.RUNNING
    with pytest.raises(TauriTerminalError):
        _terminal_result({"session_id": "session-1", "state": "running", "output_b64": "!"})


def test_tauri_terminal_host_exits_when_parent_pipe_closes() -> None:
    source = (
        Path(__file__).parents[2]
        / "tools"
        / "terminal-tauri"
        / "src-tauri"
        / "src"
        / "lib.rs"
    ).read_text(encoding="utf-8")
    host_loop = source.split("fn host_loop", 1)[1].split("\nfn handle_host_request", 1)[0]

    assert "\n    app.exit(0);\n}" in host_loop


def test_tauri_terminal_requests_write_on_process_owner_thread(tmp_path: Path) -> None:
    qtwidgets = pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import QProcess

    from app.terminal.tauri_process import TERMINAL_REQUEST_MARKER, TauriTerminalProcess

    app = qtwidgets.QApplication.instance() or qtwidgets.QApplication([])
    transport = TauriTerminalProcess(base_dir=tmp_path)
    owner_thread_id = threading.get_ident()

    class FakeQProcess:
        def state(self):  # type: ignore[no-untyped-def]
            return QProcess.ProcessState.Running

        def write(self, payload: bytes) -> int:
            assert threading.get_ident() == owner_thread_id
            line = payload.decode("utf-8").strip()
            request = json.loads(line[len(TERMINAL_REQUEST_MARKER) :])
            transport._resolve_pending(
                request["id"],
                result={
                    "session_id": "session-1",
                    "state": "running",
                    "output_b64": "aGk=",
                    "cursor": 2,
                    "exit_code": None,
                    "truncated": False,
                },
            )
            return len(payload)

    transport._process = FakeQProcess()  # type: ignore[assignment]
    transport._ready = True
    completed: list[TerminalReadResult] = []
    worker = threading.Thread(
        target=lambda: completed.append(
            transport.read(session_id="session-1", cursor=0, max_bytes=16)
        )
    )
    worker.start()
    deadline = time.monotonic() + 1
    while worker.is_alive() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert completed[0].output == b"hi"


def test_tauri_terminal_parses_valid_approval_resolution_only(tmp_path: Path) -> None:
    from app.terminal.tauri_process import TauriTerminalProcess

    transport = TauriTerminalProcess(base_dir=tmp_path)
    resolved: list[tuple[str, str]] = []
    transport.approval_resolved.connect(
        lambda approval_id, decision: resolved.append((approval_id, decision))
    )

    transport._handle_host_event(
        json.dumps(
            {
                "type": "approval_resolved",
                "approval_id": "approval-1",
                "decision": "once",
            }
        )
    )
    transport._handle_host_event(
        json.dumps(
            {
                "type": "approval_resolved",
                "approval_id": "approval-1",
                "decision": "always",
            }
        )
    )
    transport._handle_host_event("not-json")

    assert resolved == [("approval-1", "once")]


def test_tauri_terminal_sends_approval_through_versioned_host_protocol(tmp_path: Path) -> None:
    from PySide6.QtCore import QProcess

    from app.terminal.tauri_process import (
        TERMINAL_PROTOCOL_VERSION,
        TERMINAL_REQUEST_MARKER,
        TauriTerminalProcess,
    )

    writes: list[dict[str, object]] = []

    class FakeQProcess:
        def state(self):  # type: ignore[no-untyped-def]
            return QProcess.ProcessState.Running

        def write(self, payload: bytes) -> int:
            line = payload.decode("utf-8").strip()
            writes.append(json.loads(line[len(TERMINAL_REQUEST_MARKER) :]))
            return len(payload)

    transport = TauriTerminalProcess(base_dir=tmp_path)
    transport._process = FakeQProcess()  # type: ignore[assignment]
    transport._ready = True

    transport._show_approval_on_ui(
        {
            "id": "approval-1",
            "tool_name": "terminal_exec",
            "summary": "printf hello",
            "command": ["printf", "hello"],
            "cwd": str(tmp_path),
            "risk_level": "low",
            "allowed_scopes": ["once", "process"],
        }
    )

    assert writes[0]["version"] == TERMINAL_PROTOCOL_VERSION == 2
    assert writes[0]["method"] == "show_approval"
    assert writes[0]["params"]["approval"]["id"] == "approval-1"  # type: ignore[index]


def test_tauri_terminal_shutdown_uses_one_total_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PySide6.QtCore import QProcess

    import app.terminal.tauri_process as tauri_process_module
    from app.terminal.tauri_process import TauriTerminalProcess

    clock = iter((10.0, 10.0, 10.1, 10.2))
    monkeypatch.setattr(tauri_process_module.time, "monotonic", lambda: next(clock))

    class FakeQProcess:
        def __init__(self) -> None:
            self.waits: list[int] = []
            self.terminated = False
            self.killed = False

        def state(self):  # type: ignore[no-untyped-def]
            return QProcess.ProcessState.Running

        def write(self, payload: bytes) -> int:
            return len(payload)

        def closeWriteChannel(self) -> None:
            return None

        def waitForFinished(self, timeout_ms: int) -> bool:
            self.waits.append(timeout_ms)
            return False

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

    transport = TauriTerminalProcess(base_dir=tmp_path)
    process = FakeQProcess()
    transport._process = process  # type: ignore[assignment]

    transport._shutdown_on_ui(250)

    assert len(process.waits) == 3
    assert 249 <= process.waits[0] <= 250
    assert 149 <= process.waits[1] <= 150
    assert 49 <= process.waits[2] <= 50
    assert process.terminated
    assert process.killed
    assert transport._process is None
    assert not transport._closing
