from __future__ import annotations

import threading
import time
import io
from pathlib import Path

import pytest

from app.core_host.chat_fixture import ChatFixtureBoundary
from app.core_host.protocol import FrameDecoder, encode_frame
from app.core_host.server import ControlDispatcher, HostConfig, run_host


GENERATION_ID = "00000000-0000-4000-8000-000000002202"
CREDENTIAL = "22" * 16
ROOT = Path("/isolated/not-read/wp-2-02")


def request(request_id: str, name: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "protocolMajor": 2,
        "protocolMinor": 2,
        "kind": "request",
        "generationId": GENERATION_ID,
        "generationCredential": CREDENTIAL,
        "id": request_id,
        "name": name,
        "payload": payload,
        "deadlineMs": 3000,
        "priority": "interactive",
    }


def test_chat_fixture_has_one_terminal_under_cancel_race() -> None:
    published: list[dict[str, object]] = []
    boundary = ChatFixtureBoundary(
        generation_id=GENERATION_ID,
        generation_credential=CREDENTIAL,
        event_publisher=published.append,
    )
    incoming = request(
        "chat-1",
        "chat.send",
        {
            "message": "hello",
            "operationId": "chat-1",
            "fixture": {"kind": "sleep", "delayMs": 500},
        },
    )
    running = threading.Thread(target=lambda: boundary.handle_send(incoming))
    running.start()
    assert boundary.wait_started("chat-1", timeout=1)
    first = boundary.handle_cancel(request("cancel-1", "chat.cancel", {"operationId": "chat-1"}))
    second = boundary.handle_cancel(request("cancel-2", "chat.cancel", {"operationId": "chat-1"}))
    running.join(1)
    assert first["payload"]["accepted"] is True
    assert second["payload"]["accepted"] is False
    terminals = [item for item in published if item["name"] in {"chat.completed", "chat.failed", "chat.cancelled"}]
    assert [item["name"] for item in terminals] == ["chat.cancelled"]
    assert boundary.active_interaction_summary() is None


def test_chat_fixture_rejects_invalid_payload_and_forbidden_transport_fields() -> None:
    boundary = ChatFixtureBoundary(GENERATION_ID, CREDENTIAL)
    for payload in (
        {},
        {"message": 7},
        {"message": "x", "generationId": "forged"},
        {"message": "x", "operationId": "forged"},
    ):
        with pytest.raises(ValueError):
            boundary.handle_send(request("chat-invalid", "chat.send", payload))


def test_chat_fixture_snapshot_revision_is_monotonic_and_public() -> None:
    boundary = ChatFixtureBoundary(GENERATION_ID, CREDENTIAL)
    first = boundary.snapshot_fields("ready", {"id": "sakura"})
    assert set(first) == {
        "generationId",
        "revision",
        "readiness",
        "currentCharacterSummary",
        "activeInteractionSummary",
    }
    assert first["generationId"] == GENERATION_ID
    assert first["revision"] == 0
    assert "credential" not in repr(first).lower()


def test_control_dispatcher_allows_chat_cancel_without_waiting_for_fixture() -> None:
    boundary = ChatFixtureBoundary(GENERATION_ID, CREDENTIAL)
    dispatcher = ControlDispatcher(
        HostConfig(ROOT, GENERATION_ID, CREDENTIAL),
        chat_boundary=boundary,
    )
    hello = request(
        "hello",
        "system.hello",
        {
            "protocol": {"major": 2, "minMinor": 2, "maxMinor": 2},
            "requiredCapabilities": [
                "system.hello",
                "system.health",
                "system.shutdown",
                "core.initialize",
                "core.snapshot",
            ],
            "optionalCapabilities": ["transport.concurrent-router"],
        },
    )
    hello["priority"] = "control"
    assert dispatcher.dispatch(hello)[0]["ok"] is True
    response, should_stop = dispatcher.dispatch(
        request("cancel", "chat.cancel", {"operationId": "unknown"})
    )
    assert should_stop is False
    assert response["ok"] is True
    dispatcher.close()


def test_run_host_chat_cancel_keeps_control_response_ahead_of_sleep_fixture() -> None:
    hello = request(
        "hello",
        "system.hello",
        {
            "protocol": {"major": 2, "minMinor": 2, "maxMinor": 2},
            "requiredCapabilities": [
                "system.hello",
                "system.health",
                "system.shutdown",
                "core.initialize",
                "core.snapshot",
            ],
            "optionalCapabilities": ["transport.concurrent-router"],
        },
    )
    send = request(
        "chat-1",
        "chat.send",
        {
            "message": "hello",
            "operationId": "chat-1",
            "fixture": {"kind": "sleep", "delayMs": 10_000},
        },
    )
    cancel = request("cancel-1", "chat.cancel", {"operationId": "chat-1"})
    health = request("health-1", "system.health", {})
    health["priority"] = "control"
    shutdown = request("shutdown-1", "system.shutdown", {})
    shutdown["priority"] = "control"

    class HeldInput(io.BytesIO):
        def __init__(self, data: bytes) -> None:
            super().__init__(data)
            self.release = threading.Event()

        def read(self, size: int = -1) -> bytes:
            value = super().read(size)
            if value:
                return value
            self.release.wait(2)
            return b""

    input_stream = HeldInput(
        b"".join(encode_frame(item) for item in (hello, send, cancel, health, shutdown))
    )
    output_stream = io.BytesIO()
    failures: list[BaseException] = []
    runner = threading.Thread(
        target=lambda: _capture_run_host(input_stream, output_stream, failures)
    )
    runner.start()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            messages = FrameDecoder().feed(output_stream.getvalue())
        except Exception:
            messages = []
        if any(message.get("id") == "cancel-1" for message in messages):
            break
        time.sleep(0.01)
    input_stream.release.set()
    runner.join(3)
    assert not failures
    messages = FrameDecoder().feed(output_stream.getvalue())
    assert any(message.get("id") == "cancel-1" and message["ok"] for message in messages)
    assert any(message.get("id") == "health-1" and message["ok"] for message in messages)
    assert any(message.get("id") == "shutdown-1" and message["ok"] for message in messages)
    assert any(message.get("name") == "chat.cancelled" for message in messages)


def _capture_run_host(
    input_stream: io.BytesIO,
    output_stream: io.BytesIO,
    failures: list[BaseException],
) -> None:
    try:
        run_host(input_stream, output_stream, HostConfig(ROOT, GENERATION_ID, CREDENTIAL))
    except BaseException as error:  # noqa: BLE001
        failures.append(error)
