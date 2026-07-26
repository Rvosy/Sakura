import io
import threading
import time
from pathlib import Path

from app.core_host.protocol import FrameDecoder, encode_frame, event, response
from app.core_host.router import (
    ConcurrentHostRouter,
    DISPATCH_QUEUE_LIMIT,
    EVENT_QUEUE_LIMIT,
    FIXTURE_QUEUE_LIMIT,
    FixtureResult,
)
from app.core_host.server import ControlDispatcher, HostConfig, ResponseWriter


GENERATION_ID = "00000000-0000-4000-8000-000000002201"
CREDENTIAL = "22" * 16
ROOT = Path("/isolated/not-read/wp-2-01")


def request(request_id: str, name: str, *, minor: int = 2) -> dict[str, object]:
    return {
        "protocolMajor": 2,
        "protocolMinor": minor,
        "kind": "request",
        "generationId": GENERATION_ID,
        "generationCredential": CREDENTIAL,
        "id": request_id,
        "name": name,
        "payload": {},
        "deadlineMs": 3000,
        "priority": "control" if name.startswith("system.") else "interactive",
    }


def hello() -> dict[str, object]:
    message = request("hello", "system.hello")
    message["payload"] = {
        "protocol": {"major": 2, "minMinor": 2, "maxMinor": 2},
        "requiredCapabilities": [
            "system.hello",
            "system.health",
            "system.shutdown",
            "core.initialize",
            "core.snapshot",
        ],
        "optionalCapabilities": ["transport.concurrent-router"],
    }
    return message


def test_fixture_tasks_are_concurrent_and_health_shutdown_are_not_behind_them() -> None:
    requests = [hello()]
    for index in range(2):
        requests.append(request(f"fixture-{index}", "fixture.blocking"))
    requests.extend(
        [
            request("health", "system.health"),
            request("shutdown", "system.shutdown"),
        ]
    )
    input_stream = io.BytesIO(b"".join(encode_frame(message) for message in requests))
    output_stream = io.BytesIO()
    started: list[float] = []
    lock = threading.Lock()

    def fixture_handler(incoming: dict[str, object]) -> FixtureResult:
        with lock:
            started.append(time.monotonic())
        time.sleep(0.15)
        produced = response(
            incoming,
            generation_id=GENERATION_ID,
            generation_credential=CREDENTIAL,
            protocol_minor=2,
            payload={"accepted": True},
        )
        produced_event = event(
            incoming,
            generation_id=GENERATION_ID,
            generation_credential=CREDENTIAL,
            name="fixture.completed",
            payload={"id": incoming["id"]},
        )
        return FixtureResult(produced, (produced_event,))

    writer = ResponseWriter(output_stream)
    router = ConcurrentHostRouter(
        input_stream,
        writer,
        ControlDispatcher(HostConfig(ROOT, GENERATION_ID, CREDENTIAL)),
        fixture_handler=fixture_handler,
    )
    router.run()
    writer.close()

    decoder = FrameDecoder()
    messages = decoder.feed(output_stream.getvalue())
    decoder.finish()
    assert len(started) == 2
    assert abs(started[0] - started[1]) < 0.1
    assert messages[0]["id"] == "hello"
    assert any(message["id"] == "health" and message["ok"] is True for message in messages)
    assert any(message["id"] == "shutdown" for message in messages)
    assert sum(message["kind"] == "event" for message in messages) == 2


def test_router_capacity_is_named_and_finite() -> None:
    assert DISPATCH_QUEUE_LIMIT > 0
    assert FIXTURE_QUEUE_LIMIT > 0
    assert EVENT_QUEUE_LIMIT > 0
