from __future__ import annotations

import json
import os
import queue
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
from app.storage.chat_history import ChatHistoryStore
from app.core_host.real_chat import RealChatBoundary
from app.llm.chat_reply import ChatReply, ChatSegment


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


class _ProviderHandler(BaseHTTPRequestHandler):
    requests: list[dict[str, object]] = []
    outcome = "complete"
    release = threading.Event()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        type(self).requests.append(body)
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


def _hello() -> dict[str, object]:
    return _request(
        "hello",
        "system.hello",
        {
            "protocol": {"major": 2, "minMinor": 2, "maxMinor": 2},
            "requiredCapabilities": CAPABILITIES,
            "optionalCapabilities": ["transport.concurrent-router"],
        },
    )


def _start_host(app_root: Path) -> subprocess.Popen[bytes]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "app.core_host",
            "--app-root",
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
    assert process.stdin is not None
    process.stdin.write(bytes.fromhex(GENERATION_CREDENTIAL))
    process.stdin.flush()
    return process


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
                + process.stderr.read().decode("utf-8", errors="replace")
            )
    assert isinstance(value, dict)
    return value


def _exchange(process: subprocess.Popen[bytes], message: dict[str, object]) -> dict[str, object]:
    _send(process, message)
    response = _read(process)
    assert response["id"] == message["id"]
    return response


def _wait_ready(process: subprocess.Popen[bytes]) -> None:
    _exchange(process, _hello())
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
    raise TimeoutError("Assistant did not become ready")


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
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def _configure_app_root(tmp_path: Path, port: int) -> Path:
    app_root = tmp_path / "app-root"
    shutil.copytree(SOURCE_ROOT, app_root)
    (app_root / "data/config/api.yaml").write_text(
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
                "config_version: 4",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return app_root


def _start_provider(outcome: str) -> tuple[ThreadingHTTPServer, threading.Thread]:
    _ProviderHandler.requests = []
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


def test_prompt_dependency_gate_runs_before_pipeline_and_honors_cancel(tmp_path: Path) -> None:
    order: list[str] = []

    class Pipeline:
        def run_user_message(self, _messages, **_kwargs):  # type: ignore[no-untyped-def]
            order.append("pipeline")
            return SimpleNamespace(
                reply=ChatReply(
                    [
                        ChatSegment(
                            text="ok",
                            translation="好",
                            tone="中性",
                            portrait="neutral",
                        )
                    ]
                ),
                actions=[],
            )

    class History:
        def __init__(self, *_args) -> None:  # type: ignore[no-untyped-def]
            return None

        def assert_compatible_append(self) -> None:
            return None

        def load_recent(self, _limit: int):  # type: ignore[no-untyped-def]
            return []

        def append(self, *_args) -> None:  # type: ignore[no-untyped-def]
            return None

    def wait_prompt_dependencies(*, cancel_checker):  # type: ignore[no-untyped-def]
        cancel_checker()
        order.append("dependencies")
        return []

    runtime = SimpleNamespace(finish_trace_operation=lambda *_args, **_kwargs: True)
    session = SimpleNamespace(
        character=SimpleNamespace(id="sakura", display_name="Sakura"),
        runtime=runtime,
        pipeline=Pipeline(),
        tool_actions=None,
        memory_boundary=None,
        wait_prompt_dependencies=wait_prompt_dependencies,
    )
    boundary = RealChatBoundary(
        GENERATION_ID,
        GENERATION_CREDENTIAL,
        tmp_path,
        session_provider=lambda: session,
        history_factory=History,
    )
    request = _request(
        "dependency-order",
        "chat.send",
        {"message": "hello", "operationId": "dependency-order"},
    )
    boundary.reserve_send(request)
    boundary.handle_send(request)
    assert order == ["dependencies", "pipeline"]
    boundary.close()


def test_real_core_local_provider_completed_projection_and_history(tmp_path: Path) -> None:
    server, provider_thread = _start_provider("complete")
    port = server.server_address[1]
    app_root = _configure_app_root(tmp_path, port)
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
        if frames[1].get("name") == "chat.failed":
            _exchange(process, _request("debug-shutdown", "system.shutdown", {}))
            process.wait(timeout=5)
            assert process.stderr is not None
            raise AssertionError(process.stderr.read().decode("utf-8", errors="replace"))
        assert [frame.get("name", "response") for frame in frames] == [
            "chat.started",
            "chat.completed",
            "chat.send",
        ]
        terminal = frames[1]["payload"]
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
        assert frames[2]["payload"] == {
            "accepted": True,
            "operationId": "chat-local",
        }
        assert _ProviderHandler.requests
        serialized_request = json.dumps(_ProviderHandler.requests, ensure_ascii=False)
        assert "LOCAL_TEST_KEY" not in serialized_request

        history = ChatHistoryStore(app_root / "data/chat_history/sakura.jsonl").load()
        assert [(entry.role, entry.content) for entry in history] == [
            ("user", "ただいま"),
            ("assistant", "おかえり。"),
        ]
        shutdown = _exchange(process, _request("shutdown", "system.shutdown", {}))
        assert shutdown["payload"] == {"accepted": True}
        assert process.wait(timeout=5) == 0
        assert process.stderr is not None
        stderr = process.stderr.read().decode("utf-8", errors="replace")
        assert "LOCAL_TEST_KEY" not in stderr
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
        assert [frame.get("name") for frame in frames] == [
            "chat.started",
            "chat.failed",
            "chat.send",
        ]
        assert frames[1]["payload"] == {
            "operationId": "chat-invalid-json",
            "error": {
                "code": "PROVIDER_RESPONSE_INVALID",
                "message": "供应商响应格式无效：返回内容不是有效 JSON。",
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
        history = ChatHistoryStore(app_root / "data/chat_history/sakura.jsonl").load()
        assert [(entry.role, entry.content) for entry in history] == [("user", "hello")]
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
        deadline = time.monotonic() + 3
        while not _ProviderHandler.requests and time.monotonic() < deadline:
            time.sleep(0.01)
        assert _ProviderHandler.requests

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


def test_cancel_interrupts_provider_retry_sleep(tmp_path: Path) -> None:
    server, provider_thread = _start_provider("http-500")
    app_root = _configure_app_root(tmp_path, server.server_address[1])
    process = _start_host(app_root)
    try:
        _wait_ready(process)
        _send(
            process,
            _request(
                "chat-retry-cancel",
                "chat.send",
                {"message": "retry", "operationId": "chat-retry-cancel"},
            ),
        )
        assert _read(process)["name"] == "chat.started"
        deadline = time.monotonic() + 3
        while not _ProviderHandler.requests and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(_ProviderHandler.requests) == 1
        _send(
            process,
            _request(
                "cancel-retry",
                "chat.cancel",
                {"operationId": "chat-retry-cancel"},
            ),
        )
        frames = [_read(process), _read(process), _read(process)]
        assert sum(frame.get("name") == "chat.cancelled" for frame in frames) == 1
        assert not any(
            frame.get("name") in {"chat.completed", "chat.failed"} for frame in frames
        )
        assert len(_ProviderHandler.requests) == 1
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
        assert [frame.get("name") for frame in frames] == [
            "chat.started",
            "chat.failed",
            "chat.send",
        ]
        assert frames[1]["payload"]["error"] == {
            "code": "PROVIDER_RESPONSE_INVALID",
            "message": "供应商响应格式无效：回复结构不符合协议。",
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
    [(400, False, 1), (401, False, 1), (429, True, 3), (500, True, 3)],
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
        terminal = frames[1]
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
        assert frames[1]["name"] == "chat.completed"
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
        assert frames[1]["name"] == "chat.failed"
        assert frames[1]["payload"]["error"] == {
            "code": "PROVIDER_REQUEST_FAILED",
            "message": "Provider request failed",
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
        deadline = time.monotonic() + 3
        while not _ProviderHandler.requests and time.monotonic() < deadline:
            time.sleep(0.01)
        assert _ProviderHandler.requests

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
        stderr = process.stderr.read().decode("utf-8", errors="replace")
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
        deadline = time.monotonic() + 3
        while not _ProviderHandler.requests and time.monotonic() < deadline:
            time.sleep(0.01)
        assert _ProviderHandler.requests
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
