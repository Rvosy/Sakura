from __future__ import annotations

import io
import json
import struct
from pathlib import Path

import pytest

import app.core_host.server as server_module
from app.core_host.protocol import (
    MAX_FRAME_SIZE,
    FrameDecoder,
    ProtocolError,
    decode_frame,
    encode_frame,
    read_frame,
)
from app.core_host.server import ControlDispatcher, HostConfig, ResponseWriter, WriterError, run_host


GENERATION_ID = "00000000-0000-4000-8000-000000001c01"
GENERATION_CREDENTIAL = "11" * 16
APP_ROOT = Path("/isolated/not-read/core-host-protocol")


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
