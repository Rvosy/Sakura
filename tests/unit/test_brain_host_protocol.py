from __future__ import annotations

import io
import json
import struct
from pathlib import Path

import pytest

from app.brain_host.protocol import (
    MAX_FRAME_SIZE,
    FrameDecoder,
    ProtocolError,
    SessionTracker,
    create_error_response,
    decode_frame,
    encode_frame,
    validate_envelope,
)
from app.brain_host.transport import FramedTransport


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "brain_host_frame_v1.json"


def _request(*, request_id: str = "req-1", sequence: int = 1) -> dict[str, object]:
    return {
        "protocol": 1,
        "kind": "request",
        "id": request_id,
        "session_id": "session-1",
        "sequence": sequence,
        "method": "system.health",
        "deadline_ms": 30_000,
        "payload": {},
    }


def test_python_encoder_matches_shared_golden_frame() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    encoded = encode_frame(fixture["message"])

    assert encoded == bytes.fromhex(fixture["frame_hex"])
    assert decode_frame(encoded) == fixture["message"]


def test_incremental_decoder_accepts_fragmented_and_coalesced_frames() -> None:
    first = encode_frame(_request(request_id="req-1", sequence=1))
    second = encode_frame(_request(request_id="req-2", sequence=2))
    decoder = FrameDecoder()

    decoded: list[dict[str, object]] = []
    wire = first + second
    for offset in range(0, len(wire), 3):
        decoded.extend(decoder.feed(wire[offset : offset + 3]))
    decoder.finish()

    assert [item["id"] for item in decoded] == ["req-1", "req-2"]


def test_decoder_rejects_oversized_frame_before_reading_payload() -> None:
    decoder = FrameDecoder()

    with pytest.raises(ProtocolError, match="FRAME_TOO_LARGE") as exc_info:
        decoder.feed(struct.pack(">I", MAX_FRAME_SIZE + 1))

    assert exc_info.value.code == "FRAME_TOO_LARGE"


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (b"\xff", "INVALID_UTF8"),
        (b"{not-json}", "INVALID_JSON"),
    ],
)
def test_decoder_rejects_invalid_utf8_and_json(payload: bytes, code: str) -> None:
    frame = struct.pack(">I", len(payload)) + payload

    with pytest.raises(ProtocolError) as exc_info:
        decode_frame(frame)

    assert exc_info.value.code == code


def test_decoder_reports_incomplete_frame_at_eof() -> None:
    decoder = FrameDecoder()
    decoder.feed(struct.pack(">I", 20) + b"{}")

    with pytest.raises(ProtocolError) as exc_info:
        decoder.finish()

    assert exc_info.value.code == "INCOMPLETE_FRAME"


@pytest.mark.parametrize(
    "kind",
    ["request", "response", "event", "cancel", "stream_chunk", "stream_end"],
)
def test_protocol_accepts_all_phase_one_message_kinds(kind: str) -> None:
    envelope = _request()
    envelope["kind"] = kind
    if kind != "request":
        envelope.pop("method")
    if kind == "response":
        envelope["ok"] = True
    if kind == "cancel":
        envelope["target_id"] = "req-parent"

    validate_envelope(envelope)


def test_protocol_rejects_unknown_message_kind() -> None:
    envelope = _request()
    envelope["kind"] = "debug_log"

    with pytest.raises(ProtocolError) as exc_info:
        validate_envelope(envelope)

    assert exc_info.value.code == "INVALID_ENVELOPE"


def test_error_response_has_stable_machine_readable_shape() -> None:
    response = create_error_response(
        request_id="req-1",
        session_id="session-1",
        sequence=2,
        code="BACKEND_UNAVAILABLE",
        message="Brain is unavailable",
        retryable=True,
        details={"state": "starting"},
    )

    assert response == {
        "protocol": 1,
        "kind": "response",
        "id": "req-1",
        "session_id": "session-1",
        "sequence": 2,
        "ok": False,
        "error": {
            "code": "BACKEND_UNAVAILABLE",
            "message": "Brain is unavailable",
            "retryable": True,
            "details": {"state": "starting"},
        },
    }


def test_session_tracker_rejects_duplicate_ids_and_sequence_gaps() -> None:
    tracker = SessionTracker("session-1")
    tracker.accept(_request(request_id="req-1", sequence=1))

    with pytest.raises(ProtocolError) as duplicate:
        tracker.accept(_request(request_id="req-1", sequence=2))
    assert duplicate.value.code == "DUPLICATE_REQUEST_ID"

    with pytest.raises(ProtocolError) as sequence:
        tracker.accept(_request(request_id="req-2", sequence=3))
    assert sequence.value.code == "INVALID_SEQUENCE"


def test_closing_session_terminates_pending_requests() -> None:
    tracker = SessionTracker("session-1")
    tracker.accept(_request(request_id="req-1", sequence=1))
    tracker.accept(_request(request_id="req-2", sequence=2))

    terminated = tracker.close()

    assert terminated == ("req-1", "req-2")
    assert tracker.pending_request_ids == ()
    with pytest.raises(ProtocolError) as exc_info:
        tracker.accept(_request(request_id="req-3", sequence=3))
    assert exc_info.value.code == "SESSION_CLOSED"


class _FragmentedReader(io.BytesIO):
    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = 2
        return super().read(min(size, 2))


def test_transport_reads_fragmented_frame_and_flushes_writes() -> None:
    message = _request()
    writer = io.BytesIO()
    transport = FramedTransport(_FragmentedReader(encode_frame(message)), writer)

    assert transport.receive() == message
    transport.send(message)

    assert writer.getvalue() == encode_frame(message)
