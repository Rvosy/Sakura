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


def test_blocking_file_fixture_does_not_delay_health(tmp_path: Path) -> None:
    marker = tmp_path / "blocking-fixture.txt"
    marker.write_text("fixture-complete", encoding="utf-8")
    release = threading.Event()
    started = threading.Event()
    requests = [hello(), request("fixture-file", "fixture.file"), request("health", "system.health")]
    input_stream = io.BytesIO(b"".join(encode_frame(message) for message in requests))
    output_stream = io.BytesIO()
    dispatcher = ControlDispatcher(HostConfig(ROOT, GENERATION_ID, CREDENTIAL))

    def fixture_handler(incoming: dict[str, object]) -> dict[str, object]:
        started.set()
        assert release.wait(2)
        assert marker.read_bytes() == b"fixture-complete"
        return response(
            incoming,
            generation_id=GENERATION_ID,
            generation_credential=CREDENTIAL,
            protocol_minor=2,
            payload={"read": True},
        )

    writer = ResponseWriter(output_stream)
    router = ConcurrentHostRouter(
        input_stream,
        writer,
        dispatcher,
        fixture_handler=fixture_handler,
    )
    failure: list[BaseException] = []
    runner = threading.Thread(target=lambda: _run_router(router, failure))
    runner.start()
    assert started.wait(1)

    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        try:
            messages = FrameDecoder().feed(output_stream.getvalue())
        except Exception:
            messages = []
        if any(message.get("id") == "health" for message in messages):
            break
        time.sleep(0.01)
    else:
        raise AssertionError("health response was blocked by file fixture")
    release.set()
    runner.join(3)
    writer.close()
    dispatcher.close()
    assert not failure


def _run_router(router: ConcurrentHostRouter, failures: list[BaseException]) -> None:
    try:
        router.run()
    except BaseException as error:  # noqa: BLE001 - asserted by the test owner
        failures.append(error)


def test_fixture_queue_saturation_returns_a_bounded_overload_response() -> None:
    class HoldAfterInput(io.BytesIO):
        def __init__(self, data: bytes) -> None:
            super().__init__(data)
            self.release = threading.Event()
            self._held = False

        def read(self, size: int = -1) -> bytes:
            value = super().read(size)
            if value:
                return value
            if not self._held:
                self._held = True
                self.release.wait(2)
            return b""

    release_fixtures = threading.Event()
    requests = [hello(), *[request(f"fixture-{index}", "fixture.blocking") for index in range(13)]]
    input_stream = HoldAfterInput(b"".join(encode_frame(message) for message in requests))
    output_stream = io.BytesIO()
    dispatcher = ControlDispatcher(HostConfig(ROOT, GENERATION_ID, CREDENTIAL))

    def fixture_handler(incoming: dict[str, object]) -> dict[str, object]:
        assert release_fixtures.wait(2)
        return response(
            incoming,
            generation_id=GENERATION_ID,
            generation_credential=CREDENTIAL,
            protocol_minor=2,
            payload={"accepted": True},
        )

    writer = ResponseWriter(output_stream)
    router = ConcurrentHostRouter(
        input_stream,
        writer,
        dispatcher,
        fixture_handler=fixture_handler,
    )
    failures: list[BaseException] = []
    runner = threading.Thread(target=lambda: _run_router(router, failures))
    runner.start()
    deadline = time.monotonic() + 2
    saw_overload = False
    while time.monotonic() < deadline:
        try:
            messages = FrameDecoder().feed(output_stream.getvalue())
        except Exception:
            messages = []
        saw_overload = any(
            message.get("error", {}).get("code") == "ROUTER_QUEUE_FULL" for message in messages
        )
        if saw_overload:
            break
        time.sleep(0.01)
    release_fixtures.set()
    input_stream.release.set()
    runner.join(4)
    writer.close()
    dispatcher.close()
    assert saw_overload
    assert not failures
