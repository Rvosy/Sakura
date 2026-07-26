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
from typing import BinaryIO

from app.core_host.protocol import encode_frame, read_frame
from app.storage.chat_history import ChatHistoryStore


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
        if type(self).outcome == "invalid-json":
            response = b"not-json"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
            return
        content = json.dumps(
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
                "message": "Provider response was invalid",
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
        frames = [_read(process), _read(process), _read(process)]
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
