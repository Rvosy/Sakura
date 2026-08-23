from __future__ import annotations

import io
import json
import struct
import threading
from pathlib import Path

import pytest

import app.core_host.server as server_module
from app.core_host.router import ConcurrentHostRouter
from app.core_host.protocol import (
    MAX_FRAME_SIZE,
    FrameDecoder,
    ProtocolError,
    decode_frame,
    encode_frame,
    event,
    read_frame,
)
from app.core_host.server import ControlDispatcher, HostConfig, ResponseWriter, WriterError, run_host


GENERATION_ID = "00000000-0000-4000-8000-000000001c01"
GENERATION_CREDENTIAL = "11" * 16
APP_ROOT = Path("/isolated/not-read/core-host-protocol")
WP_2_01_ENVELOPES = json.loads(
    (Path(__file__).resolve().parents[2] / "tests/fixtures/runtime_v2/wp_2_01/envelopes.json")
    .read_text(encoding="utf-8")
)


def request(request_id: str, name: str = "system.hello") -> dict[str, object]:
    return {
        "protocolMajor": 2,
        "protocolMinor": 1,
        "kind": "request",
        "generationId": GENERATION_ID,
        "generationCredential": GENERATION_CREDENTIAL,
        "id": request_id,
        "name": name,
        "payload": {},
        "deadlineMs": 3000,
        "priority": "control",
    }


def test_codec_accepts_every_split_and_multiple_merged_frames() -> None:
    first = encode_frame(request("one"))
    second = encode_frame(request("two", "system.health"))
    for split in range(len(first) + 1):
        decoder = FrameDecoder()
        before = decoder.feed(first[:split])
        after = decoder.feed(first[split:])
        assert before + after == [request("one")]
        decoder.finish()

    decoder = FrameDecoder()
    assert decoder.feed(first + second) == [
        request("one"),
        request("two", "system.health"),
    ]
    decoder.finish()


@pytest.mark.parametrize(
    ("frame", "code"),
    [
        (struct.pack(">I", 1) + b"\xff", "INVALID_UTF8"),
        (struct.pack(">I", 1) + b"{", "INVALID_JSON"),
        (struct.pack(">I", 0), "INVALID_FRAME"),
        (struct.pack(">I", MAX_FRAME_SIZE + 1), "FRAME_TOO_LARGE"),
        (b"pollution", "STDOUT_FRAMING_POLLUTION"),
    ],
)
def test_malformed_and_polluted_frames_fail_with_stable_codes(
    frame: bytes, code: str
) -> None:
    with pytest.raises(ProtocolError) as raised:
        decode_frame(frame)
    assert raised.value.code == code


@pytest.mark.parametrize(
    "partial",
    [b"\x00", b"\x00\x00\x00", struct.pack(">I", 10) + b"{}"],
)
def test_incomplete_header_or_payload_fails_at_eof(partial: bytes) -> None:
    decoder = FrameDecoder()
    assert decoder.feed(partial) == []
    with pytest.raises(ProtocolError) as raised:
        decoder.finish()
    assert raised.value.code == "INCOMPLETE_FRAME"


def test_read_frame_distinguishes_clean_eof_from_partial_eof() -> None:
    assert read_frame(io.BytesIO()) is None
    with pytest.raises(ProtocolError) as raised:
        read_frame(io.BytesIO(b"\x00\x00"))
    assert raised.value.code == "INCOMPLETE_FRAME"


def test_envelope_and_error_shape_are_strict_and_json_safe() -> None:
    missing_payload = request("missing-payload")
    del missing_payload["payload"]
    with pytest.raises(ProtocolError) as raised:
        encode_frame(missing_payload)
    assert raised.value.code == "INVALID_ENVELOPE"

    invalid = request("bad")
    invalid["deadlineMs"] = True
    with pytest.raises(ProtocolError) as raised:
        encode_frame(invalid)
    assert raised.value.code == "INVALID_ENVELOPE"

    oversized = request("large")
    oversized["payload"] = {"value": "x" * MAX_FRAME_SIZE}
    with pytest.raises(ProtocolError) as raised:
        encode_frame(oversized)
    assert raised.value.code == "FRAME_TOO_LARGE"

    encoded = encode_frame(request("unicode"))
    payload_length = struct.unpack(">I", encoded[:4])[0]
    decoded_json = json.loads(encoded[4 : 4 + payload_length].decode("utf-8"))
    assert decoded_json == request("unicode")


def test_protocol_22_event_is_distinct_from_21_request_response() -> None:
    source = request("event-source", "fixture.blocking")
    source["protocolMinor"] = 2
    message = event(
        source,
        generation_id=GENERATION_ID,
        generation_credential=GENERATION_CREDENTIAL,
        name="fixture.completed",
        payload={"state": "completed"},
    )
    assert decode_frame(encode_frame(message)) == message
    assert "ok" not in message
    assert "deadlineMs" not in message
    assert "priority" not in message

    for key, value in (
        ("protocolMinor", 1),
        ("ok", True),
        ("deadlineMs", 3000),
        ("priority", "interactive"),
    ):
        invalid = dict(message)
        invalid[key] = value
        with pytest.raises(ProtocolError) as raised:
            encode_frame(invalid)
        assert raised.value.code == "INVALID_ENVELOPE"

    legacy = request("legacy-health", "system.health")
    legacy["protocolMinor"] = 1
    assert decode_frame(encode_frame(legacy)) == legacy


def test_wp_2_01_shared_envelopes_validate_in_python() -> None:
    assert decode_frame(encode_frame(WP_2_01_ENVELOPES["request"])) == WP_2_01_ENVELOPES[
        "request"
    ]
    assert decode_frame(encode_frame(WP_2_01_ENVELOPES["event"])) == WP_2_01_ENVELOPES[
        "event"
    ]


def test_single_writer_queue_closes_idempotently_and_rejects_late_writes() -> None:
    output = io.BytesIO()
    writer = ResponseWriter(output)
    dispatcher = ControlDispatcher(HostConfig(APP_ROOT, GENERATION_ID, GENERATION_CREDENTIAL))
    hello_request = request("hello", "system.hello")
    hello_request["payload"] = {
        "protocol": {"major": 2, "minMinor": 0, "maxMinor": 1},
        "requiredCapabilities": [
            "system.hello",
            "system.health",
            "system.shutdown",
            "core.initialize",
            "core.snapshot",
        ],
        "optionalCapabilities": [],
    }
    assert dispatcher.dispatch(hello_request)[0]["ok"] is True
    first, first_stop = dispatcher.dispatch(request("shutdown-1", "system.shutdown"))
    second, second_stop = dispatcher.dispatch(request("shutdown-2", "system.shutdown"))
    assert first_stop is True
    assert second_stop is True
    writer.send(first)
    writer.send(second)
    writer.close()
    writer.close()

    decoder = FrameDecoder()
    assert decoder.feed(output.getvalue()) == [first, second]
    decoder.finish()
    with pytest.raises(WriterError):
        writer.send(first)
    try:
        writer.send(first)
    except WriterError as error:
        assert error.code == "WRITER_QUEUE_CLOSED"


def test_attaching_tts_boundary_registers_startup_warmup_callback() -> None:
    dispatcher = ControlDispatcher(HostConfig(APP_ROOT, GENERATION_ID, GENERATION_CREDENTIAL))
    callbacks: list[object] = []
    dispatcher._readiness.set_session_published_callback = (  # type: ignore[method-assign]
        callbacks.append
    )

    class Boundary:
        def warmup_current_selection(self) -> None:
            return None

    boundary = Boundary()
    dispatcher.attach_tts_boundary(boundary)

    assert callbacks == [boundary.warmup_current_selection]


def test_router_invalidates_generation_work_before_waiting_for_workers() -> None:
    calls: list[str] = []
    invalidated = threading.Event()

    class Dispatcher:
        def invalidate_generation_work(self) -> None:
            calls.append("invalidate")
            invalidated.set()

    router = ConcurrentHostRouter(
        io.BytesIO(),
        object(),
        Dispatcher(),
    )

    def stop_after_invalidation() -> None:
        assert invalidated.wait(1)
        calls.append("worker-stopped")

    worker = threading.Thread(target=stop_after_invalidation)
    worker.start()
    router._threads.append(worker)

    router.close()

    assert calls == ["invalidate", "worker-stopped"]


def test_router_drains_detached_event_producers_before_closing_event_writer() -> None:
    messages: list[dict[str, object]] = []
    router: ConcurrentHostRouter

    class Writer:
        def send(self, message: dict[str, object], *, wait: bool = True) -> None:
            assert wait is True
            messages.append(message)

    class Dispatcher:
        def invalidate_generation_work(self) -> None:
            return None

        def drain_generation_work(self) -> None:
            router.publish_event({"kind": "event", "name": "chat.cancelled"})

    router = ConcurrentHostRouter(io.BytesIO(), Writer(), Dispatcher())

    router.run()

    assert messages == [{"kind": "event", "name": "chat.cancelled"}]


def test_writer_queue_saturation_and_slow_write_fail_with_bounded_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server_module, "_WRITER_OPERATION_TIMEOUT_SECONDS", 0.05)

    class BlockingOutput:
        def __init__(self) -> None:
            self.entered = threading.Event()
            self.release = threading.Event()

        def write(self, data: bytes) -> int:
            self.entered.set()
            assert self.release.wait(2)
            return len(data)

        def flush(self) -> None:
            return None

    output = BlockingOutput()
    writer = ResponseWriter(output)
    first = request("writer-slow")
    first["kind"] = "response"
    first.pop("deadlineMs")
    first.pop("priority")
    first["ok"] = True

    failures: list[BaseException] = []
    sender = threading.Thread(
        target=lambda: _capture_writer_failure(writer, first, failures)
    )
    sender.start()
    assert output.entered.wait(1)
    sender.join(1)
    assert len(failures) == 1
    assert getattr(failures[0], "code", None) == "TRANSPORT_WRITE_FAILED"

    saturated = None
    for index in range(server_module.WRITER_QUEUE_LIMIT + 2):
        message = dict(first)
        message["id"] = f"queued-{index}"
        try:
            writer.send(message, wait=False)
        except WriterError as error:
            saturated = error
            break
    assert saturated is not None
    assert saturated.code == "WRITER_QUEUE_CLOSED"
    output.release.set()
    writer.close()


def _capture_writer_failure(
    writer: ResponseWriter,
    message: dict[str, object],
    failures: list[BaseException],
) -> None:
    try:
        writer.send(message)
    except BaseException as error:  # noqa: BLE001 - asserted by the test owner
        failures.append(error)


def test_run_host_raises_first_cleanup_failure_and_attaches_sanitized_later_notes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    first = RuntimeError("PRIVATE_DISPATCHER_CLOSE")
    second = ValueError("PRIVATE_WRITER_CLOSE")

    class Dispatcher:
        def __init__(self, _config: HostConfig) -> None:
            pass

        def close(self) -> None:
            events.append("dispatcher")
            raise first

    class Writer:
        def __init__(self, _stream: io.BytesIO) -> None:
            pass

        def close(self) -> None:
            events.append("writer")
            raise second

    monkeypatch.setattr(server_module, "ControlDispatcher", Dispatcher)
    monkeypatch.setattr(server_module, "ResponseWriter", Writer)

    with pytest.raises(RuntimeError) as raised:
        run_host(
            io.BytesIO(),
            io.BytesIO(),
            HostConfig(APP_ROOT, GENERATION_ID, GENERATION_CREDENTIAL),
        )

    assert raised.value is first
    assert events == ["dispatcher", "writer"]
    notes = getattr(raised.value, "__notes__", [])
    assert any("ValueError" in note for note in notes)
    assert all("PRIVATE_" not in note for note in notes)


def test_run_host_preserves_primary_failure_and_attempts_every_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    primary = OSError("PRIVATE_PRIMARY")

    class Dispatcher:
        def __init__(self, _config: HostConfig) -> None:
            pass

        def close(self) -> None:
            events.append("dispatcher")
            raise RuntimeError("PRIVATE_DISPATCHER_CLOSE")

    class Writer:
        def __init__(self, _stream: io.BytesIO) -> None:
            pass

        def close(self) -> None:
            events.append("writer")
            raise ValueError("PRIVATE_WRITER_CLOSE")

    monkeypatch.setattr(server_module, "ControlDispatcher", Dispatcher)
    monkeypatch.setattr(server_module, "ResponseWriter", Writer)
    monkeypatch.setattr(server_module, "read_frame", lambda _stream: (_ for _ in ()).throw(primary))

    with pytest.raises(OSError) as raised:
        run_host(
            io.BytesIO(),
            io.BytesIO(),
            HostConfig(APP_ROOT, GENERATION_ID, GENERATION_CREDENTIAL),
        )

    assert raised.value is primary
    assert events == ["dispatcher", "writer"]
    notes = getattr(raised.value, "__notes__", [])
    assert any("RuntimeError" in note for note in notes)
    assert any("ValueError" in note for note in notes)
    assert all("PRIVATE_" not in note for note in notes)


def test_run_host_writer_failure_keeps_dispatcher_then_writer_cleanup_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    writer_failure = WriterError("TRANSPORT_WRITE_FAILED", "sanitized")

    class Dispatcher:
        def __init__(self, _config: HostConfig) -> None:
            pass

        def dispatch(self, _request: dict[str, object]) -> tuple[dict[str, object], bool]:
            return {"response": True}, False

        def close(self) -> None:
            events.append("dispatcher")

    class Writer:
        def __init__(self, _stream: io.BytesIO) -> None:
            pass

        def send(self, _message: dict[str, object]) -> None:
            raise writer_failure

        def close(self) -> None:
            events.append("writer")

    monkeypatch.setattr(server_module, "ControlDispatcher", Dispatcher)
    monkeypatch.setattr(server_module, "ResponseWriter", Writer)
    monkeypatch.setattr(server_module, "read_frame", lambda _stream: request("one"))

    with pytest.raises(WriterError) as raised:
        run_host(
            io.BytesIO(),
            io.BytesIO(),
            HostConfig(APP_ROOT, GENERATION_ID, GENERATION_CREDENTIAL),
        )

    assert raised.value is writer_failure
    assert events == ["dispatcher", "writer"]


def test_run_host_reaches_writer_cleanup_when_initializer_close_never_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    close_entered = threading.Event()
    close_release = threading.Event()
    initialized = threading.Event()

    class Initializer:
        close_calls = 0

        def initialize(self, _cancel: threading.Event) -> object:
            initialized.set()
            return type(
                "Result",
                (),
                {
                    "state": "ready",
                    "code": "READY",
                    "retryable": False,
                    "current_character_summary": None,
                },
            )()

        def close(self) -> None:
            self.close_calls += 1
            close_entered.set()
            close_release.wait()

    initializer = Initializer()

    class Dispatcher(ControlDispatcher):
        def __init__(self, config: HostConfig) -> None:
            super().__init__(config, initializer_factory=lambda _root: initializer)
            self._readiness.begin({})
            assert initialized.wait(1)

        def close(self) -> None:
            events.append("dispatcher")
            super().close()

    class Writer(ResponseWriter):
        def close(self) -> None:
            events.append("writer")
            super().close()

    monkeypatch.setattr(server_module, "ControlDispatcher", Dispatcher)
    monkeypatch.setattr(server_module, "ResponseWriter", Writer)
    failures: list[BaseException] = []
    host_done = threading.Event()

    def run() -> None:
        try:
            run_host(
                io.BytesIO(),
                io.BytesIO(),
                HostConfig(APP_ROOT, GENERATION_ID, GENERATION_CREDENTIAL),
            )
        except BaseException as error:  # noqa: BLE001 - asserted in test owner
            failures.append(error)
        finally:
            host_done.set()

    runner = threading.Thread(target=run)
    runner.start()
    assert close_entered.wait(1)
    completed_within_owner_budget = host_done.wait(1.5)
    if not completed_within_owner_budget:
        close_release.set()
        runner.join(1)

    assert completed_within_owner_budget
    assert events == ["dispatcher", "writer"]
    assert len(failures) == 1
    assert getattr(failures[0], "code", None) == "SHUTDOWN_DURING_INITIALIZE"
    assert initializer.close_calls == 1
    close_release.set()


def test_real_writer_failure_is_observed_before_waiting_for_peer_eof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class BlockingAfterFrame:
        def __init__(self, frame: bytes) -> None:
            self._source = io.BytesIO(frame)
            self.second_read_entered = threading.Event()
            self.release = threading.Event()

        def read(self, size: int = -1) -> bytes:
            chunk = self._source.read(size)
            if chunk:
                return chunk
            self.second_read_entered.set()
            self.release.wait()
            return b""

    class FailingOutput:
        def __init__(self) -> None:
            self.write_failed = threading.Event()

        def write(self, _data: bytes) -> int:
            self.write_failed.set()
            raise OSError("PRIVATE_OUTPUT_FAILURE")

        def flush(self) -> None:
            return None

    class Dispatcher:
        def __init__(self, _config: HostConfig) -> None:
            pass

        def dispatch(self, incoming: dict[str, object]) -> tuple[dict[str, object], bool]:
            return (
                server_module.response(
                    incoming,
                    generation_id=GENERATION_ID,
                    generation_credential=GENERATION_CREDENTIAL,
                    payload={"accepted": True},
                ),
                False,
            )

        def close(self) -> None:
            events.append("dispatcher")

    class TrackingWriter(ResponseWriter):
        def close(self) -> None:
            events.append("writer")
            super().close()

    input_stream = BlockingAfterFrame(encode_frame(request("one")))
    output_stream = FailingOutput()
    monkeypatch.setattr(server_module, "ControlDispatcher", Dispatcher)
    monkeypatch.setattr(server_module, "ResponseWriter", TrackingWriter)
    failures: list[BaseException] = []
    host_done = threading.Event()

    def run() -> None:
        try:
            run_host(
                input_stream,
                output_stream,
                HostConfig(APP_ROOT, GENERATION_ID, GENERATION_CREDENTIAL),
            )
        except BaseException as error:  # noqa: BLE001 - asserted in test owner
            failures.append(error)
        finally:
            host_done.set()

    runner = threading.Thread(target=run)
    runner.start()
    assert output_stream.write_failed.wait(1)
    completed_before_peer_eof = host_done.wait(0.5)
    if not completed_before_peer_eof:
        input_stream.release.set()
        runner.join(1)

    assert completed_before_peer_eof
    assert not input_stream.second_read_entered.is_set()
    assert events == ["dispatcher", "writer"]
    assert len(failures) == 1
    assert getattr(failures[0], "code", None) == "TRANSPORT_WRITE_FAILED"
    input_stream.release.set()
