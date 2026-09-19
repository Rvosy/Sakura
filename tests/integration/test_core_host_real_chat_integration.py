from __future__ import annotations

import io
import json
import os
import queue
import secrets
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import BinaryIO

import pytest

from app.core_host.protocol import encode_frame, read_frame
from app.legacy_import.history import import_history
from app.storage.timeline import TimelineKind, TimelineStore
from app.core_host.real_chat import RealChatBoundary, RealChatRejection
from app.plugin_sdk.sakura_assistant_contract import ChatReply, ChatSegment


REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPO_ROOT / "tests/fixtures/runtime_v2/wp_3_01/ready"
GENERATION_ID = "00000000-0000-4000-8000-000000003002"
GENERATION_CREDENTIAL = "32" * 16
CAPABILITIES = [
    "system.hello",
    "system.health",
    "system.shutdown",
    "core.initialize",
    "core.snapshot",
]


class _AssistantDouble:
    def commit_result(self, commit):
        return commit()

    def release(self, _operation_id, *, status):
        pass


class _SessionDouble(SimpleNamespace):
    visual_binding = None

    def descriptor(self):
        return {"character": {"id": self.character.id}, "loopSettings": {}}


class _ProviderHandler(BaseHTTPRequestHandler):
    requests: list[dict[str, object]] = []
    outcome = "complete"
    release = threading.Event()
    received = threading.Event()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        type(self).requests.append(body)
        type(self).received.set()
        if type(self).outcome == "compatibility" and len(type(self).requests) == 1:
            assert "response_format" in body
            response = b'{"error":{"message":"response_format unsupported"}}'
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
            return
        if type(self).outcome.startswith("http-"):
            status = int(type(self).outcome.removeprefix("http-"))
            response = json.dumps(
                {
                    "error": {
                        "message": "Rate limit exceeded for requested model",
                        "code": "rate_limit",
                        "type": "requests",
                        "private": "PRIVATE_PROVIDER_FAILURE",
                    },
                    "api_key": "sk-private-fixture",
                }
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
            return
        if type(self).outcome == "invalid-json":
            response = b"not-json"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
            return
        content = (
            "{"
            if type(self).outcome == "invalid-content"
            else json.dumps(
                {
                    "segments": [
                        {
                            "ja": "おかえり。",
                            "zh": "欢迎回来。",
                            "tone": "中性",
                            "portrait": "neutral",
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )
        response = json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": content,
                        }
                    }
                ]
            },
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        if type(self).outcome == "blocked-read":
            type(self).release.wait(5)
        try:
            self.wfile.write(response)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, _format: str, *_args: object) -> None:
        return None


def _request(request_id: str, name: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "protocolMajor": 2,
        "protocolMinor": 2,
        "kind": "request",
        "generationId": GENERATION_ID,
        "generationCredential": GENERATION_CREDENTIAL,
        "id": request_id,
        "name": name,
        "payload": payload,
        "deadlineMs": 10_000,
        "priority": "control" if name != "chat.send" else "interactive",
    }


def _activated_timeline(path: Path) -> TimelineStore:
    store = TimelineStore(path)
    store.initialize()
    return store


def _hello(optional_capabilities: list[str] | None = None) -> dict[str, object]:
    return _request(
        "hello",
        "system.hello",
        {
            "protocol": {"major": 2, "minMinor": 2, "maxMinor": 2},
            "requiredCapabilities": CAPABILITIES,
            "optionalCapabilities": optional_capabilities or ["transport.concurrent-router"],
        },
    )


@pytest.fixture(autouse=True)
def _isolated_assistant_distribution(tmp_path, assistant_dependencies, monkeypatch):
    """Run Core with independent Assistant and remote model provider plugins."""
    start_host = _start_host

    def start(app_root, *, distribution_root=None):
        distribution = distribution_root or tmp_path / "host-distribution"
        for name in ("sakura_assistant", "sakura_portrait", "sakura_model_openai_compatible"):
            destination = distribution / "plugins/builtin" / name
            if not destination.exists():
                shutil.copytree(
                    REPO_ROOT / "plugins/builtin" / name,
                    destination,
                    ignore=shutil.ignore_patterns("__pycache__"),
                )
        dependencies = distribution / "plugins/dependencies/sakura.model.openai_compatible"
        if not dependencies.exists():
            shutil.copytree(assistant_dependencies, dependencies, copy_function=os.link)
            (dependencies / ".sakura-dependencies.json").write_text(
                json.dumps({"schemaVersion": 1, "kind": "requirements.txt",
                            "python": f"{sys.version_info.major}.{sys.version_info.minor}"}),
                encoding="utf-8",
            )
        return start_host(app_root, distribution_root=distribution)

    monkeypatch.setattr(sys.modules[__name__], "_start_host", start)


@pytest.fixture(autouse=True)
def _provider_cleanup_on_fixture_failure(monkeypatch):
    """A failed distribution setup must not leave its HTTP server thread alive."""
    start_provider = _start_provider
    providers = []
    def start(outcome):
        provider = start_provider(outcome)
        providers.append(provider)
        return provider
    monkeypatch.setattr(sys.modules[__name__], "_start_provider", start)
    yield
    for server, worker in providers:
        if worker.is_alive():
            _stop_provider(server, worker)


def _start_host(
    app_root: Path,
    *,
    distribution_root: Path = REPO_ROOT,
) -> subprocess.Popen[bytes]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "app.core_host",
            "--distribution-root",
            str(distribution_root),
            "--user-root",
            str(app_root),
            "--generation-id",
            GENERATION_ID,
        ],
        cwd=REPO_ROOT,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=flags,
    )
    process._stderr_lines = []
    process._stderr_thread = threading.Thread(
        target=lambda: process._stderr_lines.extend(iter(process.stderr.readline, b"")),
        name="real-chat-integration-diagnostics", daemon=True,
    )
    process._stderr_thread.start()
    assert process.stdin is not None
    process.stdin.write(bytes.fromhex(GENERATION_CREDENTIAL))
    process.stdin.flush()
    return process


def _stderr_text(process):
    if process.poll() is not None:
        process._stderr_thread.join(2)
        assert not process._stderr_thread.is_alive()
    return b"".join(process._stderr_lines).decode("utf-8", errors="replace")


def _send(process: subprocess.Popen[bytes], message: dict[str, object]) -> None:
    assert process.stdin is not None
    process.stdin.write(encode_frame(message))
    process.stdin.flush()


def _read(process: subprocess.Popen[bytes], timeout: float = 10) -> dict[str, object]:
    assert process.stdout is not None
    result: queue.Queue[object] = queue.Queue(maxsize=1)

    def read() -> None:
        try:
            result.put(read_frame(process.stdout))
        except BaseException as error:  # noqa: BLE001
            result.put(error)

    worker = threading.Thread(target=read, name="real-chat-integration-reader")
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        process.kill()
        process.wait(timeout=5)
        worker.join(5)
        raise TimeoutError("Core Host frame exceeded the integration deadline")
    value = result.get_nowait()
    if isinstance(value, BaseException):
        raise value
    if value is None:
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        if process.poll() is not None and process.stderr is not None:
            raise AssertionError(
                f"Core Host exited {process.returncode}: "
                + _stderr_text(process)
            )
    assert isinstance(value, dict)
    return value


def _exchange(process: subprocess.Popen[bytes], message: dict[str, object]) -> dict[str, object]:
    _send(process, message)
    response = _read(process)
    assert response["id"] == message["id"]
    return response


def _wait_ready(
    process: subprocess.Popen[bytes],
    optional_capabilities: list[str] | None = None,
) -> None:
    _exchange(process, _hello(optional_capabilities))
    _exchange(process, _request("initialize", "core.initialize", {}))
    deadline = time.monotonic() + 10
    index = 0
    while time.monotonic() < deadline:
        snapshot = _exchange(
            process,
            _request(f"snapshot-{index}", "core.snapshot", {}),
        )["payload"]
        if snapshot["readiness"] in {"ready", "degraded"}:
            return
        index += 1
        time.sleep(0.01)
    raise TimeoutError(f"Assistant did not become ready: {snapshot!r}\n{_stderr_text(process)}")


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None and process.stdin is not None and not process.stdin.closed:
        process.stdin.close()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
        raise AssertionError("real chat Core Host required forced cleanup")
    finally:
        process._stderr_thread.join(2)
        assert not process._stderr_thread.is_alive()
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def _configure_app_root(tmp_path: Path, port: int) -> Path:
    app_root = tmp_path / "app-root"
    shutil.copytree(SOURCE_ROOT, app_root)
    (app_root / "config/api.yaml").write_text(
        "\n".join(
            [
                "api_profiles:",
                "  - id: fixture",
                "    alias: Fixture Provider",
                f"    base_url: http://127.0.0.1:{port}/v1",
                "    api_key: LOCAL_TEST_KEY",
                "    models:",
                "      - name: fixture-model",
                "model_slots:",
                "  chat:",
                "    profile_id: fixture",
                "    model: fixture-model",
                "config_version: 1",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return app_root


def _start_provider(outcome: str) -> tuple[ThreadingHTTPServer, threading.Thread]:
    _ProviderHandler.requests = []
    _ProviderHandler.received.clear()
    _ProviderHandler.outcome = outcome
    _ProviderHandler.release = threading.Event()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ProviderHandler)
    server.daemon_threads = True
    provider_thread = threading.Thread(
        target=server.serve_forever,
        name="wp-3-02-local-provider",
    )
    provider_thread.start()
    return server, provider_thread


def _stop_provider(server: ThreadingHTTPServer, thread: threading.Thread) -> None:
    _ProviderHandler.release.set()
    server.shutdown()
    server.server_close()
    thread.join(5)
    assert not thread.is_alive()


def test_chat_never_applies_or_retries_a_previous_settings_save(tmp_path: Path) -> None:
    from app.plugin_sdk.sakura_assistant_contract import RuntimeLoopSettings
    from app.core_host.tool_settings import ToolSettingsBoundary, ToolSettingsError

    old_provider = {"serviceKey": "fixture.model", "profileId": "fixture", "modelId": "old"}
    new_provider = {**old_provider, "modelId": "new"}
    provider = SimpleNamespace(settings=old_provider)
    provider.update_settings = lambda value: setattr(provider, "settings", value)
    runtime = SimpleNamespace(runtime_loop_settings=RuntimeLoopSettings())
    old_limits = runtime.runtime_loop_settings
    new_limits = RuntimeLoopSettings(6, 2, 10)
    observed = []
    calls: list[str] = []

    class Assistant(_AssistantDouble):
        def run_turn(self, _messages, **_kwargs):  # type: ignore[no-untyped-def]
            observed.append((provider.settings, runtime.runtime_loop_settings))
            return SimpleNamespace(reply=ChatReply([]), actions=[])

    session = _SessionDouble(
        character=SimpleNamespace(id="sakura"), runtime=runtime, assistant=Assistant()
    )
    boundary = RealChatBoundary(
        GENERATION_ID,
        GENERATION_CREDENTIAL,
        tmp_path,
        session_provider=lambda: session,
        timeline_store=_activated_timeline(tmp_path / "timeline.sqlite3"),
    )

    def apply_tools(limits) -> None:
        calls.append("tools")
        runtime.runtime_loop_settings = limits

    tools = ToolSettingsBoundary(
        GENERATION_ID,
        GENERATION_CREDENTIAL,
        tmp_path,
        runtime_apply=lambda limits: boundary.apply_runtime_update(
            lambda: apply_tools(limits)
        ),
    )
    first = _request("first", "chat.send", {"message": "first", "operationId": "first"})
    next_send = _request("next", "chat.send", {"message": "next", "operationId": "next"})
    try:
        boundary.reserve_send(first)
        with pytest.raises(RealChatRejection, match="RUNTIME_UPDATE_BUSY"):
            boundary.apply_runtime_update(lambda: provider.update_settings(new_provider))
        with pytest.raises(ToolSettingsError, match="尚未应用") as saved:
            tools.save({"runtimeLimits": {
                "maxAgentStepsPerTurn": new_limits.max_agent_steps_per_turn,
                "maxToolCallsPerStep": new_limits.max_tool_calls_per_step,
                "maxToolCallsPerTurn": new_limits.max_tool_calls_per_turn,
            }})
        assert saved.value.code == "CONFIG_APPLY_FAILED"
        assert tools.snapshot()["runtimeLimits"]["maxAgentStepsPerTurn"] == 6
        boundary.handle_send(first)
        assert observed == [(old_provider, old_limits)]
        boundary.reserve_send(next_send)
        boundary.handle_send(next_send)
        assert observed[-1] == (old_provider, old_limits)
        assert calls == []

        def fail():
            calls.append("failed")
            raise OSError("fixture apply failure")

        with pytest.raises(OSError, match="fixture apply failure"):
            boundary.apply_runtime_update(fail)
        boundary.reserve_send(next_send)
        boundary.handle_send(next_send)
        assert calls == ["failed"]
        boundary.apply_runtime_update(lambda: provider.update_settings(new_provider))
        tools.save({"runtimeLimits": {
            "maxAgentStepsPerTurn": 6, "maxToolCallsPerStep": 2, "maxToolCallsPerTurn": 10,
        }})
        boundary.reserve_send(next_send)
        boundary.handle_send(next_send)
        assert observed[-1] == (new_provider, new_limits)
        assert calls == ["failed", "tools"]
    finally:
        boundary.close()


@pytest.mark.parametrize("cancelled", [False, True])
def test_start_send_acknowledges_before_slow_pipeline_terminal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancelled: bool) -> None:
    terminal_logs = []
    monkeypatch.setattr("app.core.runtime_log.log_event", lambda *args, **kwargs: terminal_logs.append((args, kwargs)))
    pipeline_started = threading.Event()
    release_pipeline = threading.Event()
    events: list[dict[str, object]] = []

    class Assistant(_AssistantDouble):
        def run_turn(self, _messages, **_kwargs):  # type: ignore[no-untyped-def]
            pipeline_started.set()
            assert release_pipeline.wait(2)
            return SimpleNamespace(
                reply=ChatReply([ChatSegment("reply", "回复", "中性", "neutral")]),
                actions=[],
            )

    session = _SessionDouble(
        character=SimpleNamespace(id="sakura", display_name="Sakura"),
        runtime=SimpleNamespace(finish_trace_operation=lambda *_args, **_kwargs: True),
        assistant=Assistant(),
        tool_actions=None,
        memory_boundary=None,
    )
    boundary = RealChatBoundary(
        GENERATION_ID,
        GENERATION_CREDENTIAL,
        tmp_path,
        session_provider=lambda: session,
        timeline_store=_activated_timeline(tmp_path / "timeline.sqlite3"),
        event_publisher=events.append,
    )
    request = _request(
        "slow-accepted",
        "chat.send",
        {"message": "look at this image", "operationId": "slow-accepted"},
    )
    boundary.reserve_send(request)

    accepted = boundary.start_send(request)

    assert accepted["payload"] == {"accepted": True, "operationId": "slow-accepted"}
    assert pipeline_started.wait(1)
    assert [event["name"] for event in events] == ["chat.started"]
    if cancelled:
        assert boundary.handle_cancel(_request("cancel-slow", "chat.cancel", {"operationId": "slow-accepted"}))["payload"]["accepted"]
    release_pipeline.set()
    deadline = time.monotonic() + 2
    while len(events) < 2 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert [event["name"] for event in events] == ["chat.started", "chat.cancelled" if cancelled else "chat.completed"]
    finished = [(args, kw) for args, kw in terminal_logs if kw.get("event") == "chat.finished"]
    assert len(finished) == 1
    assert finished[0][0][2]["outcome"] == ("cancelled" if cancelled else "success")
    assert finished[0][0][2]["operation_id"] == "slow-accepted"
    assert finished[0][0][2]["elapsed_ms"] >= 0
    assert "reason_code" not in finished[0][0][2]
    assert "stage" not in finished[0][0][2]
    assert finished[0][1]["severity"] == "info"
    boundary.close()


@pytest.mark.parametrize(
    ("failure", "cancelled", "reason_code", "stage"),
    [
        ("provider", False, "PROVIDER_REQUEST_FAILED", "assistant"),
        ("reply", False, "INVALID_CHAT_REPLY", "reply_processing"),
        ("provider", True, None, None),
    ],
)
def test_chat_finished_bridge_records_only_the_resolved_terminal_failure(
    tmp_path: Path, failure: str, cancelled: bool, reason_code: str | None, stage: str | None,
) -> None:
    from app.core_host.runtime_logging import CORE_BRIDGE_PREFIX, install_runtime_logging
    from app.plugin_sdk.sakura_model import ApiRequestError

    operation_id = "terminal-diagnostic"
    events = []

    class Assistant(_AssistantDouble):
        def run_turn(self, _messages, **_kwargs):  # type: ignore[no-untyped-def]
            if cancelled:
                assert boundary.handle_cancel(
                    _request("cancel", "chat.cancel", {"operationId": operation_id})
                )["payload"]["accepted"]
            if failure == "provider":
                raise ApiRequestError("API HTTP 400: private provider response")
            return SimpleNamespace(reply=SimpleNamespace(), actions=[])

    session = _SessionDouble(
        character=SimpleNamespace(id="sakura"), assistant=Assistant(),
    )
    boundary = RealChatBoundary(
        GENERATION_ID, GENERATION_CREDENTIAL, tmp_path, session_provider=lambda: session,
        timeline_store=_activated_timeline(tmp_path / "timeline.sqlite3"), event_publisher=events.append,
    )
    request = _request(operation_id, "chat.send", {"message": "private user message", "operationId": operation_id})
    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    try:
        boundary.reserve_send(request)
        boundary.handle_send(request)
    finally:
        boundary.close()
        bridge.close()

    records = [
        json.loads(line.removeprefix(CORE_BRIDGE_PREFIX))
        for line in stream.getvalue().splitlines() if line.startswith(CORE_BRIDGE_PREFIX)
    ]
    finished = [record for record in records if record["event"] == "chat.finished"]
    assert len(finished) == 1
    assert finished[0]["operation_id"] == operation_id
    assert finished[0]["severity"] == "info"
    attributes = finished[0]["attributes"]
    assert attributes["elapsed_ms"] >= 0
    assert attributes["outcome"] == ("cancelled" if cancelled else "failed")
    assert events[-1]["name"] == ("chat.cancelled" if cancelled else "chat.failed")
    if cancelled:
        assert "reason_code" not in attributes
        assert "stage" not in attributes
        assert "error" not in events[-1]["payload"]
    else:
        assert attributes["reason_code"] == events[-1]["payload"]["error"]["code"] == reason_code
        assert attributes["stage"] == stage
    assert "private" not in json.dumps(finished)


def test_started_worker_failure_logs_and_releases_chat_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_finished = threading.Event()
    background_errors = []
    logs = []
    operation_id = "worker-failed"
    session = _SessionDouble(
        character=SimpleNamespace(id="sakura"),
        assistant=SimpleNamespace(run_turn=lambda *_args, **_kwargs: SimpleNamespace(
            reply=ChatReply([ChatSegment("reply")]), actions=[],
        ), commit_result=lambda commit: commit()),
    )
    boundary = RealChatBoundary(
        GENERATION_ID, GENERATION_CREDENTIAL, tmp_path, session_provider=lambda: session,
        timeline_store=_activated_timeline(tmp_path / "timeline.sqlite3"),
    )
    drop_execution = boundary._drop_execution

    def capture_log(_channel, _message, attributes, **kwargs):  # type: ignore[no-untyped-def]
        if kwargs.get("event") == "chat.finished":
            raise OSError("fixture terminal logging failure")
        logs.append((attributes, kwargs))

    def observe_release(identity):  # type: ignore[no-untyped-def]
        drop_execution(identity)
        worker_finished.set()

    def capture_background_error(args):  # type: ignore[no-untyped-def]
        background_errors.append(args.exc_value)
        worker_finished.set()

    monkeypatch.setattr("app.core.runtime_log.log_event", capture_log)
    monkeypatch.setattr("app.core.runtime_log.external_runtime_sink_active", lambda: True)
    monkeypatch.setattr(boundary, "_drop_execution", observe_release)
    monkeypatch.setattr(threading, "excepthook", capture_background_error)
    request = _request(operation_id, "chat.send", {"message": "hi", "operationId": operation_id})
    next_request = _request("worker-next", "chat.send", {"message": "next", "operationId": "worker-next"})
    try:
        boundary.reserve_send(request)
        assert boundary.start_send(request)["payload"]["accepted"]
        assert worker_finished.wait(2)
        assert background_errors == []
        failures = [attributes for attributes, kwargs in logs if kwargs.get("event") == "chat.request.failed"]
        assert len(failures) == 1
        assert failures[0]["reason_code"] == "CHAT_EXECUTION_FAILED"
        assert failures[0]["operation_id"] == operation_id
        assert failures[0]["stage"] == "worker"
        boundary.reserve_send(next_request)
    finally:
        drop_execution(operation_id)
        boundary.abandon_send(next_request)
        boundary.close()


def test_completed_history_emits_cursor_only_chat_fact(tmp_path: Path) -> None:
    plugin_events: list[tuple[str, dict[str, object]]] = []

    class Worker:
        def emit_event(self, name, payload):  # type: ignore[no-untyped-def]
            plugin_events.append((name, payload))

    class Assistant(_AssistantDouble):
        def run_turn(self, _messages, **_kwargs):  # type: ignore[no-untyped-def]
            return SimpleNamespace(
                reply=ChatReply(
                    [
                        ChatSegment(
                            text="おかえり。",
                            translation="欢迎回来。",
                            tone="中性",
                            portrait="neutral",
                        )
                    ]
                ),
                actions=[],
            )

    runtime = SimpleNamespace(finish_trace_operation=lambda *_args, **_kwargs: True)
    session = _SessionDouble(
        character=SimpleNamespace(id="sakura", display_name="Sakura"),
        runtime=runtime,
        assistant=Assistant(),
        tool_actions=None,
        memory_boundary=None,
    )
    timeline = TimelineStore(tmp_path / "timeline.sqlite3")
    timeline.initialize()
    worker = Worker()
    boundary = RealChatBoundary(
        GENERATION_ID,
        GENERATION_CREDENTIAL,
        tmp_path,
        session_provider=lambda: session,
        plugin_application_provider=lambda: worker,
        timeline_store=timeline,
    )
    request = _request(
        "completed-fact",
        "chat.send",
        {"message": "ただいま", "operationId": "completed-fact"},
    )
    boundary.reserve_send(request)
    boundary.handle_send(request)

    stored = timeline.read_all("sakura")
    assert [entry.kind for entry in stored] == [TimelineKind.HUMAN, TimelineKind.ASSISTANT]
    assert stored[0].payload["text"] == request["payload"]["message"]
    assert len(stored[1].payload["segments"]) == 1
    assert plugin_events[-1] == (
        "sakura.host.chat.completed",
        {
            "characterId": "sakura",
            "turnId": stored[1].turn_id,
            "cursor": timeline.latest_cursor("sakura"),
        },
    )
    assert [name for name, _payload in plugin_events] == [
        "message.user",
        "message.ai",
        "sakura.host.chat.completed",
    ]
    boundary.close()


def test_completed_terminal_claim_rejects_late_cancel_before_plugin_delivery(
    tmp_path: Path,
) -> None:
    delivery_started = threading.Event()
    release_delivery = threading.Event()
    published: list[str] = []

    class Worker:
        def emit_event(self, name, _payload):  # type: ignore[no-untyped-def]
            if name == "sakura.host.chat.completed":
                delivery_started.set()
                assert release_delivery.wait(2)

    class Assistant(_AssistantDouble):
        def run_turn(self, _messages, **_kwargs):  # type: ignore[no-untyped-def]
            return SimpleNamespace(
                reply=ChatReply([ChatSegment("reply", "回复", "中性", "neutral")]),
                actions=[],
            )

    session = _SessionDouble(
        character=SimpleNamespace(id="sakura", display_name="Sakura"),
        runtime=SimpleNamespace(finish_trace_operation=lambda *_args, **_kwargs: True),
        assistant=Assistant(),
        tool_actions=None,
        memory_boundary=None,
    )
    timeline = TimelineStore(tmp_path / "timeline.sqlite3")
    timeline.initialize()
    worker = Worker()
    boundary = RealChatBoundary(
        GENERATION_ID,
        GENERATION_CREDENTIAL,
        tmp_path,
        session_provider=lambda: session,
        plugin_application_provider=lambda: worker,
        timeline_store=timeline,
        event_publisher=lambda frame: published.append(str(frame["name"])),
    )
    send = _request("terminal-claim", "chat.send", {"message": "hello", "operationId": "terminal-claim"})
    boundary.reserve_send(send)
    thread = threading.Thread(target=boundary.handle_send, args=(send,))
    thread.start()
    assert delivery_started.wait(2)
    cancelled = boundary.handle_cancel(
        _request("cancel-after-claim", "chat.cancel", {"operationId": "terminal-claim"})
    )
    assert cancelled["payload"]["accepted"] is False
    with pytest.raises(RealChatRejection, match="CHAT_EXECUTION_LIMIT_EXCEEDED"):
        boundary.reserve_send(
            _request("before-delivery", "chat.send", {"message": "next", "operationId": "before-delivery"})
        )
    release_delivery.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert published[-1] == "chat.completed"
    boundary.close()


@pytest.mark.parametrize("write_fails", [False, True])
def test_next_chat_waits_for_terminal_publication_and_execution_release(
    tmp_path: Path,
    write_fails: bool,
) -> None:
    terminal_published = threading.Event()
    acknowledge_terminal = threading.Event()
    next_attempted = threading.Event()
    next_finished = threading.Event()
    send_errors: list[BaseException] = []
    next_errors: list[BaseException] = []
    session = _SessionDouble(
        character=SimpleNamespace(id="sakura"),
        assistant=SimpleNamespace(
            commit_result=lambda commit: commit(),
            run_turn=lambda *_args, **_kwargs: SimpleNamespace(
                reply=ChatReply([]), actions=[]
            )
        ),
    )

    def publish(frame):  # type: ignore[no-untyped-def]
        if frame["name"] != "chat.completed":
            return
        # Synchronous observers may query the boundary while it publishes.
        assert boundary.snapshot_fields("ready", None)["activeInteractionSummary"] is not None
        assert boundary.handle_cancel(
            _request("late-cancel", "chat.cancel", {"operationId": "first"})
        )["payload"]["accepted"] is False
        terminal_published.set()
        assert acknowledge_terminal.wait(2)
        if write_fails:
            raise OSError("fixture terminal write failed")

    boundary = RealChatBoundary(
        GENERATION_ID,
        GENERATION_CREDENTIAL,
        tmp_path,
        session_provider=lambda: session,
        timeline_store=_activated_timeline(tmp_path / "timeline.sqlite3"),
        event_publisher=publish,
    )
    first = _request("first", "chat.send", {"message": "first", "operationId": "first"})
    next_send = _request("next", "chat.send", {"message": "next", "operationId": "next"})

    def send_first() -> None:
        try:
            boundary.handle_send(first)
        except BaseException as error:
            send_errors.append(error)

    def reserve_next() -> None:
        next_attempted.set()
        try:
            boundary.reserve_send(next_send)
        except BaseException as error:
            next_errors.append(error)
        finally:
            next_finished.set()

    boundary.reserve_send(first)
    sender = threading.Thread(target=send_first, daemon=True)
    successor = threading.Thread(target=reserve_next, daemon=True)
    sender.start()
    try:
        assert terminal_published.wait(1)
        successor.start()
        assert next_attempted.wait(1)
        assert not next_finished.wait(0.1)
    finally:
        acknowledge_terminal.set()
        sender.join(2)
        if successor.ident is not None:
            successor.join(2)
        if not sender.is_alive() and not successor.is_alive():
            boundary.abandon_send(next_send)
            boundary.close()

    assert not sender.is_alive() and not successor.is_alive()
    assert next_errors == []
    assert [str(error) for error in send_errors] == (
        ["fixture terminal write failed"] if write_fails else []
    )
    assert boundary.snapshot_fields("ready", None)["activeInteractionSummary"] is None


def test_assistant_history_failure_does_not_emit_completed_chat_fact(tmp_path: Path) -> None:
    plugin_events: list[str] = []

    class Worker:
        def emit_event(self, name, _payload):  # type: ignore[no-untyped-def]
            plugin_events.append(name)

    class Assistant(_AssistantDouble):
        def run_turn(self, _messages, **_kwargs):  # type: ignore[no-untyped-def]
            return SimpleNamespace(
                reply=ChatReply([ChatSegment("reply", "回复", "中性", "neutral")]),
                actions=[],
            )

    class FailingTimeline(TimelineStore):
        def append(self, entry):  # type: ignore[no-untyped-def]
            if entry.kind is TimelineKind.ASSISTANT:
                raise OSError("disk full")
            return super().append(entry)

    session = _SessionDouble(
        character=SimpleNamespace(id="sakura", display_name="Sakura"),
        runtime=SimpleNamespace(finish_trace_operation=lambda *_args, **_kwargs: True),
        assistant=Assistant(),
        tool_actions=None,
        memory_boundary=None,
    )
    failing_timeline = FailingTimeline(tmp_path / "timeline.sqlite3")
    failing_timeline.initialize()
    worker = Worker()
    boundary = RealChatBoundary(
        GENERATION_ID,
        GENERATION_CREDENTIAL,
        tmp_path,
        session_provider=lambda: session,
        plugin_application_provider=lambda: worker,
        timeline_store=failing_timeline,
    )
    request = _request(
        "failed-history-fact",
        "chat.send",
        {"message": "hello", "operationId": "failed-history-fact"},
    )
    boundary.reserve_send(request)
    boundary.handle_send(request)

    assert plugin_events == ["message.user", "message.ai"]
    boundary.close()


def test_manual_screen_attachment_is_one_shot_multimodal_and_history_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core_host.screen_capture import generation_resource_root

    image = (
        b"\xff\xd8\xff\xc0\x00\x11\x08\x00\x02\x00\x03"
        b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
        b"\xff\xda\x00\x0c\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00\x00\xff\xd9"
    )
    token = "a" * 32
    root = generation_resource_root(GENERATION_ID, temp_root=tmp_path)
    root.mkdir(parents=True)
    resource_path = root / f"{token}.jpg"
    resource_path.write_bytes(image)
    monkeypatch.setattr("app.core_host.screen_capture.tempfile.gettempdir", lambda: str(tmp_path))

    pipeline_calls: list[tuple[list[dict[str, object]], dict[str, object]]] = []

    class Assistant(_AssistantDouble):
        def run_turn(self, messages, **kwargs):  # type: ignore[no-untyped-def]
            pipeline_calls.append((messages, kwargs))
            return SimpleNamespace(
                reply=ChatReply(
                    [
                        ChatSegment(
                            text="看到了。",
                            translation="看到了。",
                            tone="中性",
                            portrait="neutral",
                        )
                    ]
                ),
                actions=[],
            )

    runtime = SimpleNamespace(finish_trace_operation=lambda *_args, **_kwargs: True)
    session = _SessionDouble(
        character=SimpleNamespace(id="sakura", display_name="Sakura"),
        runtime=runtime,
        assistant=Assistant(),
        tool_actions=None,
        memory_boundary=None,
    )
    boundary = RealChatBoundary(
        GENERATION_ID,
        GENERATION_CREDENTIAL,
        tmp_path,
        session_provider=lambda: session,
        timeline_store=_activated_timeline(tmp_path / "timeline.sqlite3"),
    )
    attach = boundary.handle_screen_attach(
        _request(
            "attach-screen",
            "screen.attach",
            {"sessionId": boundary.handle_screen_session(_request("screen-session", "screen.session", {}))["payload"]["sessionId"],
                "resource": {
                    "generationId": GENERATION_ID,
                    "resourceToken": token,
                    "mimeType": "image/jpeg",
                    "width": 3,
                    "height": 2,
                    "byteLength": len(image),
                    "capturedAt": "2026-08-18T01:02:03Z",
                    "screenName": "fixture monitor",
                }
            },
        )
    )
    attachment_id = attach["payload"]["attachmentId"]
    assert not resource_path.exists()
    second_image = image[:9] + b"\x00\x04" + image[11:]
    second_token = "b" * 32
    second_path = root / f"{second_token}.jpg"
    second_path.write_bytes(second_image)
    second_attach = boundary.handle_screen_attach(
        _request(
            "attach-screen-2",
            "screen.attach",
            {"sessionId": boundary.handle_screen_session(_request("screen-session", "screen.session", {}))["payload"]["sessionId"],
                "resource": {
                    "generationId": GENERATION_ID,
                    "resourceToken": second_token,
                    "mimeType": "image/jpeg",
                    "width": 4,
                    "height": 2,
                    "byteLength": len(second_image),
                    "capturedAt": "2026-08-18T01:02:04Z",
                    "screenName": "second monitor",
                }
            },
        )
    )
    assert second_attach["payload"]["attachmentId"] == attachment_id
    assert second_attach["payload"]["itemId"] != attach["payload"]["itemId"]
    assert second_attach["payload"]["count"] == 2
    assert not second_path.exists()

    send = _request(
        "screen-chat",
        "chat.send",
        {
            "message": "看看这里",
            "operationId": "screen-chat",
            "attachmentId": attachment_id,
        },
    )
    boundary.reserve_send(send)
    boundary.handle_send(send)

    descriptor, _kwargs = pipeline_calls[0]
    attachment = descriptor["attachment"]
    assert attachment["source"] == "manual"
    observations = attachment["observations"]
    assert len(observations) == 2
    assert all(item["data_url"].startswith("data:image/jpeg;base64,") for item in observations)
    assert observations[0]["data_url"] != observations[1]["data_url"]
    assert descriptor["message"] == send["payload"]["message"]
    stored = TimelineStore(tmp_path / "timeline.sqlite3").read_all("sakura")
    assert [entry.kind for entry in stored] == [
        TimelineKind.HUMAN,
        TimelineKind.OBSERVATION,
        TimelineKind.ASSISTANT,
    ]
    assert stored[0].payload["text"] == send["payload"]["message"]
    assert stored[1].origin == "manual_screen"
    assert stored[1].payload["visual"]["imageCount"] == 2
    assert "base64" not in json.dumps(stored[1].payload, ensure_ascii=False)

    with pytest.raises(RealChatRejection, match="SCREEN_ATTACHMENT_NOT_FOUND"):
        boundary.reserve_send(
            _request(
                "screen-chat-reuse",
                "chat.send",
                {
                    "message": "再看一次",
                    "operationId": "screen-chat-reuse",
                    "attachmentId": attachment_id,
                },
            )
        )
    boundary.close()


def test_manual_screen_attachment_items_can_be_removed_and_are_capped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core_host.screen_capture import generation_resource_root

    image = (
        b"\xff\xd8\xff\xc0\x00\x11\x08\x00\x02\x00\x03"
        b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
        b"\xff\xda\x00\x0c\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00\x00\xff\xd9"
    )
    root = generation_resource_root(GENERATION_ID, temp_root=tmp_path)
    root.mkdir(parents=True)
    monkeypatch.setattr("app.core_host.screen_capture.tempfile.gettempdir", lambda: str(tmp_path))
    boundary = RealChatBoundary(
        GENERATION_ID,
        GENERATION_CREDENTIAL,
        tmp_path,
        session_provider=lambda: object(),
        timeline_store=_activated_timeline(tmp_path / "timeline.sqlite3"),
    )

    def attach(index: int) -> tuple[dict[str, object], Path]:
        token = f"{index:032x}"
        path = root / f"{token}.jpg"
        path.write_bytes(image)
        result = boundary.handle_screen_attach(
            _request(
                f"attach-{index}",
                "screen.attach",
                {"sessionId": boundary.handle_screen_session(_request("screen-session", "screen.session", {}))["payload"]["sessionId"],
                    "resource": {
                        "generationId": GENERATION_ID,
                        "resourceToken": token,
                        "mimeType": "image/jpeg",
                        "width": 3,
                        "height": 2,
                        "byteLength": len(image),
                        "capturedAt": f"2026-08-18T01:02:{index:02d}Z",
                        "screenName": "fixture monitor",
                    }
                },
            )
        )
        return result, path

    attached = [attach(index)[0] for index in range(1, 7)]
    attachment_id = attached[0]["payload"]["attachmentId"]
    assert {item["payload"]["attachmentId"] for item in attached} == {attachment_id}
    assert [item["payload"]["count"] for item in attached] == [1, 2, 3, 4, 5, 6]
    item_ids = [item["payload"]["itemId"] for item in attached]
    assert len(set(item_ids)) == 6

    removed = boundary.handle_screen_remove(
        _request(
            "remove-third",
            "screen.remove",
            {"attachmentId": attachment_id, "itemId": item_ids[2]},
        )
    )
    assert removed["payload"] == {
        "accepted": True,
        "attachmentId": attachment_id,
        "itemId": item_ids[2],
        "count": 5,
    }
    replacement, _ = attach(7)
    assert replacement["payload"]["attachmentId"] == attachment_id
    assert replacement["payload"]["count"] == 6

    token = f"{8:032x}"
    rejected_path = root / f"{token}.jpg"
    rejected_path.write_bytes(image)
    with pytest.raises(LookupError, match="manual screen attachment limit exceeded"):
        boundary.handle_screen_attach(
            _request(
                "attach-over-limit",
                "screen.attach",
                {"sessionId": boundary.handle_screen_session(_request("screen-session", "screen.session", {}))["payload"]["sessionId"],
                    "resource": {
                        "generationId": GENERATION_ID,
                        "resourceToken": token,
                        "mimeType": "image/jpeg",
                        "width": 3,
                        "height": 2,
                        "byteLength": len(image),
                        "capturedAt": "2026-08-18T01:02:08Z",
                        "screenName": "fixture monitor",
                    }
                },
            )
        )
    assert not rejected_path.exists()
    remaining_item_ids = [*item_ids[:2], *item_ids[3:], replacement["payload"]["itemId"]]
    for expected_count, item_id in zip(range(5, -1, -1), remaining_item_ids, strict=True):
        result = boundary.handle_screen_remove(
            _request(
                f"remove-{item_id}",
                "screen.remove",
                {"attachmentId": attachment_id, "itemId": item_id},
            )
        )
        assert result["payload"]["accepted"] is True
        assert result["payload"]["count"] == expected_count
    stale = boundary.handle_screen_remove(
        _request(
            "remove-stale",
            "screen.remove",
            {"attachmentId": attachment_id, "itemId": remaining_item_ids[-1]},
        )
    )
    assert stale["payload"]["accepted"] is False
    assert stale["payload"]["count"] == 0
    boundary.close()


def test_timeline_deleted_during_runtime_fails_without_recreating_or_writing_jsonl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminal_logs = []
    monkeypatch.setattr("app.core.runtime_log.log_event", lambda *args, **kwargs: terminal_logs.append((args, kwargs)))
    pipeline_calls = 0
    events: list[dict[str, object]] = []

    class Assistant(_AssistantDouble):
        def run_turn(self, _messages, **_kwargs):  # type: ignore[no-untyped-def]
            nonlocal pipeline_calls
            pipeline_calls += 1
            return SimpleNamespace(reply=ChatReply([ChatSegment("reply")]), actions=[])

    session = _SessionDouble(
        character=SimpleNamespace(id="sakura", display_name="Sakura"),
        runtime=SimpleNamespace(finish_trace_operation=lambda *_args, **_kwargs: True),
        assistant=Assistant(),
        tool_actions=None,
        memory_boundary=None,
    )
    timeline = _activated_timeline(tmp_path / "timeline.sqlite3")
    boundary = RealChatBoundary(
        GENERATION_ID,
        GENERATION_CREDENTIAL,
        tmp_path,
        session_provider=lambda: session,
        timeline_store=timeline,
        event_publisher=events.append,
    )
    timeline.path.unlink()
    request = _request(
        "timeline-deleted",
        "chat.send",
        {"message": "hello", "operationId": "timeline-deleted"},
    )
    boundary.reserve_send(request)
    boundary.handle_send(request)

    assert [event["name"] for event in events] == ["chat.started", "chat.failed"]
    finished = [(args, kw) for args, kw in terminal_logs if kw.get("event") == "chat.finished"]
    assert len(finished) == 1
    assert finished[0][0][2]["outcome"] == "failed"
    assert finished[0][0][2]["operation_id"] == "timeline-deleted"
    assert finished[0][0][2]["reason_code"] == "TIMELINE_READ_FAILED"
    assert finished[0][0][2]["stage"] == "timeline_read"
    assert events[-1]["payload"]["error"]["code"] == "TIMELINE_READ_FAILED"  # type: ignore[index]
    assert pipeline_calls == 0
    assert not timeline.path.exists()
    assert not (tmp_path / "data" / "chat_history" / "sakura.jsonl").exists()
    boundary.close()


def test_plugin_completion_failure_does_not_block_committed_chat(tmp_path: Path) -> None:
    published: list[str] = []

    class Worker:
        def emit_event(self, _name, _payload):  # type: ignore[no-untyped-def]
            raise RuntimeError("memory plugin unavailable")

    class Assistant(_AssistantDouble):
        def run_turn(self, _messages, **_kwargs):  # type: ignore[no-untyped-def]
            return SimpleNamespace(reply=ChatReply([ChatSegment("reply")]), actions=[])

    session = _SessionDouble(
        character=SimpleNamespace(id="sakura", display_name="Sakura"),
        runtime=SimpleNamespace(finish_trace_operation=lambda *_args, **_kwargs: True),
        assistant=Assistant(),
        tool_actions=None,
        memory_boundary=None,
    )
    timeline = TimelineStore(tmp_path / "timeline.sqlite3")
    timeline.initialize()
    worker = Worker()
    boundary = RealChatBoundary(
        GENERATION_ID,
        GENERATION_CREDENTIAL,
        tmp_path,
        session_provider=lambda: session,
        plugin_application_provider=lambda: worker,
        timeline_store=timeline,
        event_publisher=lambda frame: published.append(str(frame["name"])),
    )
    request = _request(
        "plugin-failure",
        "chat.send",
        {"message": "hello", "operationId": "plugin-failure"},
    )
    boundary.reserve_send(request)
    boundary.handle_send(request)

    assert published[-1] == "chat.completed"
    assert [entry.kind for entry in timeline.read_all("sakura")] == [
        TimelineKind.HUMAN,
        TimelineKind.ASSISTANT,
    ]
    boundary.close()


@pytest.mark.parametrize("empty_reply", [False, True])
def test_plugin_image_batch_is_multimodal_and_history_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    empty_reply: bool,
) -> None:
    from app.core_host.screen_capture import generation_resource_root

    images = [
        (
            b"\xff\xd8\xff\xc0\x00\x11\x08\x00\x02\x00\x03"
            b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
            b"\xff\xda\x00\x0c\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00"
            + bytes([index])
            + b"\xff\xd9"
        )
        for index in (1, 2)
    ]
    root = generation_resource_root(GENERATION_ID, temp_root=tmp_path)
    root.mkdir(parents=True)
    resources = []
    for index, image in enumerate(images):
        token = f"{index + 1:032x}"
        (root / f"{token}.jpg").write_bytes(image)
        resources.append(
            {
                "generationId": GENERATION_ID,
                "resourceToken": token,
                "mimeType": "image/jpeg",
                "width": 3,
                "height": 2,
                "byteLength": len(image),
                "capturedAt": f"2026-08-18T01:02:0{index + 3}Z",
                "screenName": "fixture monitor",
            }
        )
    monkeypatch.setattr("app.core_host.screen_capture.tempfile.gettempdir", lambda: str(tmp_path))
    pipeline_calls: list[tuple[list[dict[str, object]], dict[str, object]]] = []

    class Assistant(_AssistantDouble):
        def run_turn(self, messages, **kwargs):  # type: ignore[no-untyped-def]
            pipeline_calls.append((messages, kwargs))
            return SimpleNamespace(
                reply=ChatReply([] if empty_reply else [ChatSegment(text="继续吧。", translation="继续吧。")]),
                actions=[],
                visual_observation={
                    "summary": "用户正在修复 Context 测试。",
                    "visible_texts": ["FAILED"],
                    "uncertain_texts": [],
                    "notable_elements": ["测试终端"],
                    "confidence": 0.9,
                },
            )

    session = _SessionDouble(
        character=SimpleNamespace(id="sakura", display_name="Sakura"),
        runtime=SimpleNamespace(finish_trace_operation=lambda *_args, **_kwargs: True),
        assistant=Assistant(),
        tool_actions=None,
        memory_boundary=None,
    )
    timeline = TimelineStore(tmp_path / "timeline.sqlite3")
    timeline.initialize()
    events = []
    boundary = RealChatBoundary(
        GENERATION_ID,
        GENERATION_CREDENTIAL,
        tmp_path,
        session_provider=lambda: session,
        timeline_store=timeline,
        event_publisher=events.append,
    )
    from app.core_host.screen_capture import consume_screen_resource
    observations = tuple(consume_screen_resource(resource, generation_id=GENERATION_ID) for resource in resources)
    assert not any(root.glob("*.jpg"))
    operation_id = boundary.reserve_plugin_message("fixture.screen", boundary.current_host_state()["sessionId"],
                                                  "查看这组屏幕图片。", observations)
    boundary.run_reserved_plugin_message(operation_id, lambda name, payload: events.append({"name": name, "payload": payload}))
    assert events[-1]["name"] == "chat.completed"

    descriptor, _kwargs = pipeline_calls[0]
    assert descriptor["message"] == "查看这组屏幕图片。"
    assert "查看这组屏幕图片。" not in str(timeline.read_recent("sakura", 10))
    assert descriptor["event"] is None
    attachment = descriptor["attachment"]
    assert attachment["source"] == "plugin"
    assert len(attachment["observations"]) == 2
    assert attachment["observations"][0]["data_url"] != attachment["observations"][1]["data_url"]
    stored = TimelineStore(tmp_path / "timeline.sqlite3").read_all("sakura")
    assert [entry.kind for entry in stored] == ([TimelineKind.OBSERVATION] if empty_reply else [
        TimelineKind.OBSERVATION,
        TimelineKind.OBSERVATION,
        TimelineKind.ASSISTANT,
    ])
    assert stored[0].origin == "host"
    assert stored[0].payload["sourcePluginId"] == "fixture.screen"
    assert stored[0].payload["visual"]["imageCount"] == 2
    if not empty_reply:
        assert stored[1].payload["visual"]["analysisStatus"] == "succeeded"
        assert "用户正在修复 Context 测试" in stored[1].payload["text"]
    serialized = json.dumps(stored[0].payload, ensure_ascii=False)
    assert all(term not in serialized for term in ("base64", "resourceToken", str(root)))

    history_cursor = timeline.latest_cursor("sakura")
    follow_up = _request(
        "screen-awareness-follow-up",
        "chat.send",
        {
            "message": "刚才进展怎么样？",
            "operationId": "screen-awareness-follow-up",
        },
    )
    boundary.reserve_send(follow_up)
    boundary.handle_send(follow_up)

    follow_up_descriptor, _follow_up_kwargs = pipeline_calls[1]
    assert follow_up_descriptor["attachment"] is None
    assert follow_up_descriptor["message"] == "刚才进展怎么样？"
    assert follow_up_descriptor["historyCursor"] == history_cursor
    boundary.close()


def test_real_core_negotiates_attaches_and_sends_screen_resource(tmp_path: Path) -> None:
    from app.core_host.screen_capture import generation_resource_root

    server, provider_thread = _start_provider("blocked-read")
    app_root = _configure_app_root(tmp_path, server.server_address[1])
    process = _start_host(app_root)
    token = secrets.token_hex(16)
    image = (
        b"\xff\xd8\xff\xc0\x00\x11\x08\x00\x02\x00\x03"
        b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
        b"\xff\xda\x00\x0c\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00\x00\xff\xd9"
    )
    resource_root = generation_resource_root(GENERATION_ID)
    resource_root.mkdir(parents=True, exist_ok=True)
    resource_path = resource_root / f"{token}.jpg"
    resource_path.write_bytes(image)
    second_token = secrets.token_hex(16)
    second_resource_path = resource_root / f"{second_token}.jpg"
    second_resource_path.write_bytes(image)
    try:
        _wait_ready(
            process,
            ["transport.concurrent-router", "assistant.screen-capture-v2"],
        )
        attach = _exchange(
            process,
            _request(
                "attach-real-screen",
                "screen.attach",
                {"sessionId": _exchange(process, _request("screen-session", "screen.session", {}))["payload"]["sessionId"],
                    "resource": {
                        "generationId": GENERATION_ID,
                        "resourceToken": token,
                        "mimeType": "image/jpeg",
                        "width": 3,
                        "height": 2,
                        "byteLength": len(image),
                        "capturedAt": "2026-08-18T01:02:03Z",
                        "screenName": "fixture monitor",
                    }
                },
            ),
        )
        attachment_id = attach["payload"]["attachmentId"]
        assert not resource_path.exists()
        second_attach = _exchange(
            process,
            _request(
                "attach-real-screen-2",
                "screen.attach",
                {"sessionId": _exchange(process, _request("screen-session", "screen.session", {}))["payload"]["sessionId"],
                    "resource": {
                        "generationId": GENERATION_ID,
                        "resourceToken": second_token,
                        "mimeType": "image/jpeg",
                        "width": 3,
                        "height": 2,
                        "byteLength": len(image),
                        "capturedAt": "2026-08-18T01:02:04Z",
                        "screenName": "fixture monitor",
                    }
                },
            ),
        )
        assert second_attach["payload"]["attachmentId"] == attachment_id
        assert second_attach["payload"]["count"] == 2
        removed = _exchange(
            process,
            _request(
                "remove-real-screen",
                "screen.remove",
                {
                    "attachmentId": attachment_id,
                    "itemId": attach["payload"]["itemId"],
                },
            ),
        )
        assert removed["payload"]["accepted"] is True
        assert removed["payload"]["count"] == 1

        _send(
            process,
            _request(
                "chat-real-screen",
                "chat.send",
                {
                    "message": "看看这里",
                    "operationId": "chat-real-screen",
                    "attachmentId": attachment_id,
                },
            ),
        )
        accepted_frames = [_read(process), _read(process)]
        assert [frame.get("name", "response") for frame in accepted_frames] == [
            "chat.started",
            "chat.send",
        ]
        assert accepted_frames[1]["payload"] == {
            "accepted": True,
            "operationId": "chat-real-screen",
        }
        _ProviderHandler.release.set()
        terminal = _read(process)
        assert terminal["name"] == "chat.completed"
        provider_request = json.dumps(_ProviderHandler.requests[-1], ensure_ascii=False)
        assert provider_request.count("data:image/jpeg;base64,") == 1
        history = TimelineStore(app_root / "data/chat_history/timeline.sqlite3").read_all("sakura")
        assert [entry.kind for entry in history[-3:]] == [
            TimelineKind.HUMAN,
            TimelineKind.OBSERVATION,
            TimelineKind.ASSISTANT,
        ]
        assert "base64" not in json.dumps(history[-2].payload, ensure_ascii=False)
        _exchange(process, _request("shutdown-screen", "system.shutdown", {}))
    finally:
        resource_path.unlink(missing_ok=True)
        second_resource_path.unlink(missing_ok=True)
        try:
            resource_root.rmdir()
        except OSError:
            pass
        _stop(process)
        _stop_provider(server, provider_thread)


@pytest.mark.parametrize("authenticated", [True, False])
def test_real_core_local_provider_completed_projection_and_history(tmp_path: Path, authenticated: bool) -> None:
    server, provider_thread = _start_provider("complete")
    port = server.server_address[1]
    app_root = _configure_app_root(tmp_path, port)
    if not authenticated:
        api_path = app_root / "config/api.yaml"
        api_path.write_text(api_path.read_text(encoding="utf-8").replace("api_key: LOCAL_TEST_KEY", "api_key: ''"), encoding="utf-8")
    process = _start_host(app_root)
    try:
        _wait_ready(process)
        _send(
            process,
            _request(
                "chat-local",
                "chat.send",
                {"message": "ただいま", "operationId": "chat-local"},
            ),
        )
        frames = [_read(process), _read(process), _read(process)]
        if any(frame.get("name") == "chat.failed" for frame in frames):
            _exchange(process, _request("debug-shutdown", "system.shutdown", {}))
            process.wait(timeout=5)
            assert process.stderr is not None
            raise AssertionError(_stderr_text(process))
        names = [frame.get("name", "response") for frame in frames]
        assert names[0] == "chat.started"
        assert set(names[1:]) == {"chat.send", "chat.completed"}
        terminal = next(frame["payload"] for frame in frames if frame.get("name") == "chat.completed")
        assert terminal == {
            "operationId": "chat-local",
            "reply": {
                "segments": [
                    {
                        "text": "おかえり。",
                        "translation": "欢迎回来。",
                        "tone": "中性",
                        "portrait": "neutral",
                        "suppressTts": False,
                    }
                ]
            },
            "historyStatus": "saved",
        }
        accepted = next(frame["payload"] for frame in frames if frame.get("name") == "chat.send")
        assert accepted == {
            "accepted": True,
            "operationId": "chat-local",
        }
        assert _ProviderHandler.requests
        serialized_request = json.dumps(_ProviderHandler.requests, ensure_ascii=False)
        assert "LOCAL_TEST_KEY" not in serialized_request

        history = TimelineStore(app_root / "data/chat_history/timeline.sqlite3").read_all("sakura")
        assert [entry.kind for entry in history] == [TimelineKind.HUMAN, TimelineKind.ASSISTANT]
        assert history[0].payload["text"] == "ただいま"
        assert history[1].payload["segments"][0]["text"] == "おかえり。"
        history_page = _exchange(
            process,
            _request(
                "history-page",
                "ui.history.page",
                {
                    "expectedCharacterId": "sakura",
                    "beforeCursor": None,
                    "limit": 50,
                },
            ),
        )
        assert history_page["ok"] is True
        assert history_page["payload"] == {
            "schemaVersion": 1,
            "coreGenerationId": GENERATION_ID,
            "characterId": "sakura",
            "totalCount": 2,
            "entries": [
                {
                    "entryId": history[0].entry_id,
                    "turnId": history[0].turn_id,
                    "kind": "human",
                    "origin": "chat",
                    "createdAt": history[0].created_at,
                    "payload": {"text": "ただいま"},
                },
                {
                    "entryId": history[1].entry_id,
                    "turnId": history[1].turn_id,
                    "kind": "assistant",
                    "origin": "chat",
                    "createdAt": history[1].created_at,
                    "payload": {
                        "segments": [
                            {"text": "おかえり。", "translation": "欢迎回来。"}
                        ]
                    },
                },
            ],
            "beforeCursor": None,
            "hasMore": False,
        }
        shutdown = _exchange(process, _request("shutdown", "system.shutdown", {}))
        assert shutdown["payload"] == {"accepted": True}
        assert process.wait(timeout=5) == 0
        assert process.stderr is not None
        stderr = _stderr_text(process)
        assert "LOCAL_TEST_KEY" not in stderr
    finally:
        _stop(process)
        _stop_provider(server, provider_thread)


def test_real_next_turn_reads_imported_legacy_timeline(tmp_path: Path) -> None:
    server, provider_thread = _start_provider("complete")
    port = server.server_address[1]
    app_root = _configure_app_root(tmp_path, port)
    legacy = tmp_path / "legacy"
    history_root = legacy / "data" / "chat_history"
    history_root.mkdir(parents=True)
    records = [
        {
            "created_at": "2026-01-01T00:00:00+08:00",
            "role": "user",
            "content": "LEGACY_CONTEXT_USER_8F21",
        },
        {
            "created_at": "2026-01-01T00:00:01+08:00",
            "role": "assistant",
            "content": "LEGACY_CONTEXT_REPLY_4A17",
            "translation": "",
            "tone": "中性",
            "portrait": "neutral",
        },
    ]
    (history_root / "sakura.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    import_history(legacy, app_root, character_ids=("sakura",))

    process = _start_host(app_root)
    try:
        _wait_ready(process)
        _send(
            process,
            _request(
                "chat-after-import",
                "chat.send",
                {"message": "continue", "operationId": "chat-after-import"},
            ),
        )
        frames = [_read(process), _read(process), _read(process)]
        assert not any(frame.get("name") == "chat.failed" for frame in frames)
        serialized_request = json.dumps(_ProviderHandler.requests, ensure_ascii=False)
        assert "LEGACY_CONTEXT_USER_8F21" in serialized_request
        assert "LEGACY_CONTEXT_REPLY_4A17" in serialized_request
        entries = TimelineStore(app_root / "data/chat_history/timeline.sqlite3").read_all("sakura")
        assert [entry.kind for entry in entries] == [
            TimelineKind.HUMAN,
            TimelineKind.ASSISTANT,
            TimelineKind.HUMAN,
            TimelineKind.ASSISTANT,
        ]
        _exchange(process, _request("shutdown-after-import", "system.shutdown", {}))
    finally:
        _stop(process)
        _stop_provider(server, provider_thread)


def test_invalid_provider_json_fails_once_without_poisoning_core(tmp_path: Path) -> None:
    server, provider_thread = _start_provider("invalid-json")
    app_root = _configure_app_root(tmp_path, server.server_address[1])
    process = _start_host(app_root)
    try:
        _wait_ready(process)
        _send(
            process,
            _request(
                "chat-invalid-json",
                "chat.send",
                {"message": "hello", "operationId": "chat-invalid-json"},
            ),
        )
        frames = [_read(process), _read(process), _read(process)]
        names = [frame.get("name") for frame in frames]
        assert names[0] == "chat.started"
        assert set(names[1:]) == {"chat.send", "chat.failed"}
        failure = next(frame["payload"] for frame in frames if frame.get("name") == "chat.failed")
        assert failure == {
            "operationId": "chat-invalid-json",
            "error": {
                "code": "PROVIDER_RESPONSE_INVALID",
                "message": "模型服务响应格式无效：回复结构不符合协议。",
                "retryable": False,
                "details": {},
            },
            "historyStatus": "saved",
        }
        health = _exchange(process, _request("health", "system.health", {}))
        assert health["payload"]["status"] == "healthy"
        snapshot = _exchange(process, _request("snapshot-after-failure", "core.snapshot", {}))
        assert snapshot["payload"]["readiness"] == "ready"
        assert snapshot["payload"]["activeInteractionSummary"] is None
        history = TimelineStore(app_root / "data/chat_history/timeline.sqlite3").read_all("sakura")
        assert [entry.kind for entry in history] == [TimelineKind.HUMAN]
        assert history[0].payload == {"text": "hello"}
        _exchange(process, _request("shutdown", "system.shutdown", {}))
        assert process.wait(timeout=5) == 0
    finally:
        _stop(process)
        _stop_provider(server, provider_thread)


def test_cancel_interrupts_blocked_provider_read_with_one_terminal(tmp_path: Path) -> None:
    server, provider_thread = _start_provider("blocked-read")
    app_root = _configure_app_root(tmp_path, server.server_address[1])
    process = _start_host(app_root)
    try:
        _wait_ready(process)
        _send(
            process,
            _request(
                "chat-cancel",
                "chat.send",
                {"message": "wait", "operationId": "chat-cancel"},
            ),
        )
        started = _read(process)
        assert started["name"] == "chat.started"
        assert _ProviderHandler.received.wait(3), _stderr_text(process)

        _send(
            process,
            _request(
                "cancel",
                "chat.cancel",
                {"operationId": "chat-cancel"},
            ),
        )
        cancel_started = time.monotonic()
        frames = [_read(process), _read(process), _read(process)]
        assert time.monotonic() - cancel_started < 1.0
        names = [frame.get("name") for frame in frames]
        assert names.count("chat.cancelled") == 1
        assert "chat.completed" not in names
        assert "chat.failed" not in names
        cancel_response = next(frame for frame in frames if frame.get("id") == "cancel")
        send_response = next(
            frame
            for frame in frames
            if frame.get("id") == "chat-cancel" and frame.get("kind") == "response"
        )
        terminal = next(frame for frame in frames if frame.get("name") == "chat.cancelled")
        assert cancel_response["payload"] == {
            "accepted": True,
            "operationId": "chat-cancel",
        }
        assert send_response["payload"] == {
            "accepted": True,
            "operationId": "chat-cancel",
        }
        assert terminal["payload"] == {
            "operationId": "chat-cancel",
            "historyStatus": "saved",
        }
        health = _exchange(process, _request("health-after-cancel", "system.health", {}))
        assert health["payload"]["status"] == "healthy"
        _exchange(process, _request("shutdown", "system.shutdown", {}))
        assert process.wait(timeout=5) == 0
    finally:
        _stop(process)
        _stop_provider(server, provider_thread)


def test_invalid_structured_reply_is_failed_not_legacy_fallback(tmp_path: Path) -> None:
    server, provider_thread = _start_provider("invalid-content")
    app_root = _configure_app_root(tmp_path, server.server_address[1])
    process = _start_host(app_root)
    try:
        _wait_ready(process)
        _send(
            process,
            _request(
                "chat-invalid-content",
                "chat.send",
                {"message": "bad content", "operationId": "chat-invalid-content"},
            ),
        )
        frames = [_read(process), _read(process), _read(process)]
        names = [frame.get("name") for frame in frames]
        assert names[0] == "chat.started"
        assert set(names[1:]) == {"chat.send", "chat.failed"}
        failure = next(frame["payload"] for frame in frames if frame.get("name") == "chat.failed")
        assert failure["error"] == {
            "code": "PROVIDER_RESPONSE_INVALID",
            "message": "模型服务响应格式无效：回复结构不符合协议。",
            "retryable": False,
            "details": {},
        }
        assert len(_ProviderHandler.requests) == 2
        _exchange(process, _request("shutdown", "system.shutdown", {}))
        assert process.wait(timeout=5) == 0
    finally:
        _stop(process)
        _stop_provider(server, provider_thread)


@pytest.mark.parametrize(
    ("status", "retryable", "request_count"),
    [(400, False, 1), (401, False, 1), (429, True, 1), (500, True, 1)],
)
def test_provider_http_status_is_sanitized_and_scoped_to_one_operation(
    tmp_path: Path,
    status: int,
    retryable: bool,
    request_count: int,
) -> None:
    server, provider_thread = _start_provider(f"http-{status}")
    app_root = _configure_app_root(tmp_path, server.server_address[1])
    process = _start_host(app_root)
    try:
        _wait_ready(process)
        operation_id = f"chat-http-{status}"
        _send(
            process,
            _request(
                operation_id,
                "chat.send",
                {"message": "status", "operationId": operation_id},
            ),
        )
        frames = [_read(process), _read(process), _read(process)]
        terminal = next(frame for frame in frames if frame.get("name") == "chat.failed")
        assert terminal["name"] == "chat.failed"
        assert terminal["payload"]["error"] == {
            "code": "PROVIDER_REQUEST_FAILED",
            "message": (
                f"API HTTP {status}: Rate limit exceeded for requested model "
                "(code: rate_limit; type: requests)"
            ),
            "retryable": retryable,
            "details": {},
        }
        assert "PRIVATE_PROVIDER_FAILURE" not in json.dumps(terminal)
        assert "sk-private-fixture" not in json.dumps(terminal)
        assert len(_ProviderHandler.requests) == request_count
        assert _exchange(process, _request("health", "system.health", {}))["ok"] is True
        _exchange(process, _request("shutdown", "system.shutdown", {}))
        assert process.wait(timeout=5) == 0
    finally:
        _stop(process)
        _stop_provider(server, provider_thread)


def test_provider_parameter_compatibility_fallback_completes(tmp_path: Path) -> None:
    server, provider_thread = _start_provider("compatibility")
    app_root = _configure_app_root(tmp_path, server.server_address[1])
    process = _start_host(app_root)
    try:
        _wait_ready(process)
        _send(
            process,
            _request(
                "chat-compatibility",
                "chat.send",
                {"message": "compatibility", "operationId": "chat-compatibility"},
            ),
        )
        frames = [_read(process), _read(process), _read(process)]
        assert any(frame.get("name") == "chat.completed" for frame in frames)
        assert len(_ProviderHandler.requests) == 2
        assert "response_format" in _ProviderHandler.requests[0]
        assert "response_format" not in _ProviderHandler.requests[1]
        _exchange(process, _request("shutdown", "system.shutdown", {}))
        assert process.wait(timeout=5) == 0
    finally:
        _stop(process)
        _stop_provider(server, provider_thread)


def test_connection_refused_is_retryable_and_does_not_change_readiness(tmp_path: Path) -> None:
    probe = ThreadingHTTPServer(("127.0.0.1", 0), _ProviderHandler)
    port = probe.server_address[1]
    probe.server_close()
    app_root = _configure_app_root(tmp_path, port)
    process = _start_host(app_root)
    try:
        _wait_ready(process)
        _send(
            process,
            _request(
                "chat-connection-refused",
                "chat.send",
                {"message": "connect", "operationId": "chat-connection-refused"},
            ),
        )
        frames = [_read(process), _read(process), _read(process)]
        terminal = next(frame for frame in frames if frame.get("name") == "chat.failed")
        assert terminal["payload"]["error"] == {
            "code": "PROVIDER_REQUEST_FAILED",
            "message": "模型请求失败。",
            "retryable": True,
            "details": {},
        }
        snapshot = _exchange(process, _request("snapshot", "core.snapshot", {}))
        assert snapshot["payload"]["readiness"] == "ready"
        _exchange(process, _request("shutdown", "system.shutdown", {}))
        assert process.wait(timeout=5) == 0
    finally:
        _stop(process)




def test_shutdown_during_blocked_provider_read_drains_terminal_and_process(tmp_path: Path) -> None:
    server, provider_thread = _start_provider("blocked-read")
    app_root = _configure_app_root(tmp_path, server.server_address[1])
    process = _start_host(app_root)
    try:
        _wait_ready(process)
        _send(
            process,
            _request(
                "chat-shutdown",
                "chat.send",
                {"message": "wait", "operationId": "chat-shutdown"},
            ),
        )
        assert _read(process)["name"] == "chat.started"
        assert _ProviderHandler.received.wait(3), _stderr_text(process)

        _send(process, _request("shutdown-active", "system.shutdown", {}))
        frames = [_read(process), _read(process), _read(process)]
        assert sum(frame.get("name") == "chat.cancelled" for frame in frames) == 1
        assert sum(
            frame.get("kind") == "response" and frame.get("id") == "shutdown-active"
            for frame in frames
        ) == 1
        assert sum(
            frame.get("kind") == "response" and frame.get("id") == "chat-shutdown"
            for frame in frames
        ) == 1
        assert process.wait(timeout=5) == 0
        assert process.stdout is not None and process.stdout.read() == b""
        assert process.stderr is not None
        stderr = _stderr_text(process)
        assert "LOCAL_TEST_KEY" not in stderr
        assert "PRIVATE_PROVIDER" not in stderr
    finally:
        _stop(process)
        _stop_provider(server, provider_thread)


def test_eof_during_blocked_provider_read_drains_terminal_and_process(tmp_path: Path) -> None:
    server, provider_thread = _start_provider("blocked-read")
    app_root = _configure_app_root(tmp_path, server.server_address[1])
    process = _start_host(app_root)
    try:
        _wait_ready(process)
        _send(
            process,
            _request(
                "chat-eof",
                "chat.send",
                {"message": "wait", "operationId": "chat-eof"},
            ),
        )
        assert _read(process)["name"] == "chat.started"
        assert _ProviderHandler.received.wait(3), _stderr_text(process)
        assert process.stdin is not None
        process.stdin.close()

        frames = [_read(process), _read(process)]
        assert sum(frame.get("name") == "chat.cancelled" for frame in frames) == 1
        assert sum(
            frame.get("kind") == "response" and frame.get("id") == "chat-eof"
            for frame in frames
        ) == 1
        assert process.wait(timeout=5) == 0
        assert process.stdout is not None and process.stdout.read() == b""
    finally:
        _stop(process)
        _stop_provider(server, provider_thread)


@pytest.mark.parametrize("switch_role", [False, True])
def test_studio_publish_updates_live_character_without_restarting_core_or_plugins(tmp_path, switch_role):
    import psutil
    server, provider_thread = _start_provider("complete")
    app_root = _configure_app_root(tmp_path, server.server_address[1])
    distribution = tmp_path / "distribution"
    for plugin in ("sakura_portrait", "sakura_spine", "sakura_gpt_sovits", "sakura_tts_hub"):
        shutil.copytree(REPO_ROOT / "plugins/builtin" / plugin, distribution / "plugins/builtin" / plugin)
    dependencies = distribution / "plugins/dependencies/sakura.tts.gpt-sovits"
    dependencies.mkdir(parents=True)
    (dependencies / ".sakura-dependencies.json").write_text(json.dumps({
        "schemaVersion": 1, "kind": "requirements.txt",
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
    }), encoding="utf-8")
    package = app_root / "characters/sakura"
    shutil.copyfile(REPO_ROOT / "desktop/frontend/prototypes/asr/assets/navi.png", package / "portraits/neutral.png")
    manifest = json.loads((package / "character.json").read_text(encoding="utf-8"))
    manifest["portrait"] = {"default": "portraits/neutral.png", "expressions": {"neutral": "portraits/neutral.png"}}
    (package / "character.json").write_text(json.dumps(manifest), encoding="utf-8")
    if switch_role:
        role_plugin = distribution / "plugins/builtin/fixture_role"
        role_plugin.mkdir()
        (role_plugin / "plugin.yaml").write_text(
            "api: 4\nid: fixture.role\nname: Role fixture\nversion: 1.0.0\nentry: plugin:Plugin\nprovides: []\nrequires: [sakura.host.character]\n",
            encoding="utf-8",
        )
        (role_plugin / "plugin.py").write_text(
            'from pathlib import Path\nclass Plugin:\n    def setup(self, context):\n        role = context.get("sakura.host.character").current()["id"]\n        Path(context.data_path("current.txt")).write_text(role, encoding="utf-8")\n',
            encoding="utf-8",
        )
        second = app_root / "characters/beta"
        shutil.copytree(package, second)
        second_manifest = {**manifest, "id": "beta", "display_name": "Beta"}
        (second / "character.json").write_text(json.dumps(second_manifest), encoding="utf-8")
        # The same card-relative path belongs to a separate character package.
        from app.config.character_loader import CharacterRegistry
        CharacterRegistry(app_root).get("beta").card_path.write_text("You are Beta with a golden book.", encoding="utf-8")
    process = _start_host(app_root, distribution_root=distribution)
    stderr = process._stderr_lines
    drain = process._stderr_thread
    def plugin_process_ids():
        return {child.pid for child in psutil.Process(process.pid).children()
                if "--plugin-id" in child.cmdline() and "fixture.role" not in child.cmdline()}
    try:
        _wait_ready(process, ["transport.concurrent-router", "assistant.plugins-v1"])
        expected_plugins = {"sakura.assistant.default", "sakura.model.openai_compatible", "sakura.portrait", "sakura.visual.spine", "sakura.tts.gpt-sovits", "sakura.tts"}
        if switch_role:
            expected_plugins.add("fixture.role")
        deadline = time.monotonic() + 5
        index = 0
        while True:
            response = _exchange(process, _request(f"plugins-ready-{index}", "plugins.settings.get", {}))
            assert response["ok"], response
            plugins = response["payload"]["plugins"]
            before = _exchange(process, _request(f"presentation-ready-{index}", "core.snapshot", {}))["payload"]
            if {item["pluginId"] for item in plugins} == expected_plugins and all(item["state"] == "active" for item in plugins) and before["characterPresentation"]["visual"]:
                break
            assert time.monotonic() < deadline, [(item["pluginId"], item["state"], item["reasonCode"]) for item in plugins]
            index += 1
        plugin_pids = plugin_process_ids()
        assert len(plugin_pids) == len(expected_plugins - {"fixture.role"})
        opened = _exchange(process, _request("open-role", "studio.character.open", {"characterId": "sakura"}))["payload"]
        doc = opened["doc"]
        doc["displayName"] = "更新后的角色"
        doc["cardText"] = "You are a character with a purple umbrella."
        doc["theme"]["primaryColor"] = "#123456"
        doc["visuals"]["resources"][0]["name"] = "新的形态名称"
        published = _exchange(process, _request("publish-role", "studio.character.publish", {"workspaceId": opened["workspaceId"], "doc": doc}))
        assert published["ok"], published
        assert published["payload"]["changePlan"] == "character_refresh"
        assert "applyError" not in published["payload"]
        after = _exchange(process, _request("after-save", "core.snapshot", {}))["payload"]
        assert after["generationId"] == before["generationId"]
        assert after["currentCharacterSummary"]["displayName"] == "更新后的角色"
        assert after["characterPresentation"]["themeTokens"]["primary"] == "#123456"
        assert after["characterPresentation"]["visual"]["bindingId"] != before["characterPresentation"]["visual"]["bindingId"]
        assert plugin_process_ids() == plugin_pids
        _send(process, _request("chat-after-save", "chat.send", {"message": "hello", "operationId": "chat-after-save"}))
        frames = [_read(process), _read(process), _read(process)]
        assert any(frame.get("name") == "chat.completed" for frame in frames), frames
        assert "purple umbrella" in json.dumps(_ProviderHandler.requests)
        assert plugin_process_ids() == plugin_pids
        if switch_role:
            _ProviderHandler.outcome = "blocked-read"
            _ProviderHandler.received.clear()
            _send(process, _request("old-role-chat", "chat.send", {"message": "old role pending", "operationId": "old-role-chat"}))
            assert _read(process)["name"] == "chat.started"
            assert _ProviderHandler.received.wait(2)
            for character_id, prompt in (("beta", "golden book"), ("sakura", "purple umbrella")):
                switch_request = _request(f"switch-{character_id}", "characters.settings.select", {"characterId": character_id})
                if character_id == "beta":
                    _send(process, switch_request)
                    frames = [_read(process), _read(process), _read(process)]
                    assert any(frame.get("name") == "chat.cancelled" for frame in frames), frames
                    switched = next(frame for frame in frames if frame.get("id") == "switch-beta")
                    _ProviderHandler.release.set()
                    _ProviderHandler.outcome = "complete"
                else:
                    switched = _exchange(process, switch_request)
                assert switched["ok"], switched
                assert switched["payload"]["changePlan"] == "character_switch"
                snapshot = _exchange(process, _request(f"state-{character_id}", "core.snapshot", {}))["payload"]
                assert snapshot["generationId"] == before["generationId"]
                assert snapshot["currentCharacterSummary"]["id"] == character_id
                assert snapshot["characterPresentation"]["characterId"] == character_id
                assert (app_root / "data/plugins/fixture.role/current.txt").read_text(encoding="utf-8") == character_id
                assert plugin_process_ids() == plugin_pids
                _send(process, _request(f"chat-{character_id}", "chat.send", {"message": f"hello {character_id}", "operationId": f"chat-{character_id}"}))
                reply = [_read(process), _read(process), _read(process)]
                assert any(frame.get("name") == "chat.completed" for frame in reply), reply
                assert prompt in json.dumps(_ProviderHandler.requests[-1])
            assert "hello beta" not in json.dumps(_ProviderHandler.requests[-1])
        _exchange(process, _request("shutdown-after-save", "system.shutdown", {}))
        process.wait(timeout=5)
        drain.join(2)
    finally:
        _stop(process)
        _stop_provider(server, provider_thread)
