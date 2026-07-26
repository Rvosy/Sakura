"""Length-prefixed JSON framing for the Runtime v2 control plane."""

from __future__ import annotations

import json
import struct
from collections.abc import Mapping
from typing import Any, BinaryIO


PROTOCOL_MAJOR = 2
PROTOCOL_MINOR = 2
EVENT_PROTOCOL_MINOR = 2
HEADER_SIZE = 4
MAX_FRAME_SIZE = 8 * 1024 * 1024
MESSAGE_KINDS = frozenset({"request", "response", "event"})
PRIORITIES = frozenset({"control", "interactive", "background"})


class ProtocolError(RuntimeError):
    """Protocol failure with a stable machine-readable code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details = dict(details or {})


def _require_string(mapping: Mapping[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError("INVALID_ENVELOPE", f"{key} must be a non-empty string")
    return value


def _require_non_negative_int(mapping: Mapping[str, Any], key: str) -> int:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProtocolError("INVALID_ENVELOPE", f"{key} must be a non-negative integer")
    return value


def validate_envelope(message: Mapping[str, Any]) -> None:
    if not isinstance(message, Mapping):
        raise ProtocolError("INVALID_ENVELOPE", "message must be a JSON object")
    _require_non_negative_int(message, "protocolMajor")
    _require_non_negative_int(message, "protocolMinor")
    kind = _require_string(message, "kind")
    if kind not in MESSAGE_KINDS:
        raise ProtocolError("INVALID_ENVELOPE", "unknown message kind")
    _require_string(message, "generationId")
    if kind in {"response", "event"} or "generationCredential" in message:
        _require_string(message, "generationCredential")
    _require_string(message, "id")
    _require_string(message, "name")
    if not isinstance(message.get("payload"), Mapping):
        raise ProtocolError("INVALID_ENVELOPE", "payload must be an object")

    if kind == "request":
        deadline = _require_non_negative_int(message, "deadlineMs")
        if deadline == 0:
            raise ProtocolError("INVALID_ENVELOPE", "deadlineMs must be positive")
        if _require_string(message, "priority") not in PRIORITIES:
            raise ProtocolError("INVALID_ENVELOPE", "unknown priority")
    elif kind == "response":
        ok = message.get("ok")
        if not isinstance(ok, bool):
            raise ProtocolError("INVALID_ENVELOPE", "response must include boolean ok")
        if not ok:
            error = message.get("error")
            if not isinstance(error, Mapping):
                raise ProtocolError("INVALID_ENVELOPE", "failed response must include error")
            _require_string(error, "code")
            _require_string(error, "message")
            if not isinstance(error.get("retryable"), bool):
                raise ProtocolError("INVALID_ENVELOPE", "error retryable must be boolean")
            if not isinstance(error.get("details", {}), Mapping):
                raise ProtocolError("INVALID_ENVELOPE", "error details must be an object")
    else:
        minor = _require_non_negative_int(message, "protocolMinor")
        if minor < EVENT_PROTOCOL_MINOR:
            raise ProtocolError("INVALID_ENVELOPE", "event requires protocol minor 2.2")
        for forbidden in ("deadlineMs", "priority", "ok", "error"):
            if forbidden in message:
                raise ProtocolError("INVALID_ENVELOPE", f"event must not include {forbidden}")


def encode_frame(message: Mapping[str, Any]) -> bytes:
    validate_envelope(message)
    try:
        payload = json.dumps(
            dict(message),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ProtocolError("INVALID_JSON", "message is not JSON serializable") from error
    if len(payload) > MAX_FRAME_SIZE:
        raise ProtocolError(
            "FRAME_TOO_LARGE",
            f"frame payload exceeds {MAX_FRAME_SIZE} bytes",
            details={"length": len(payload), "limit": MAX_FRAME_SIZE},
        )
    return struct.pack(">I", len(payload)) + payload


def _decode_payload(payload: bytes) -> dict[str, Any]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProtocolError("INVALID_UTF8", "frame payload is not valid UTF-8") from error
    try:
        message = json.loads(text)
    except json.JSONDecodeError as error:
        raise ProtocolError("INVALID_JSON", "frame payload is not valid JSON") from error
    if not isinstance(message, dict):
        raise ProtocolError("INVALID_ENVELOPE", "message must be a JSON object")
    validate_envelope(message)
    return message


class FrameDecoder:
    """Incremental decoder accepting arbitrary fragmentation and merged frames."""

    def __init__(self, *, max_frame_size: int = MAX_FRAME_SIZE) -> None:
        self.max_frame_size = max_frame_size
        self._buffer = bytearray()
        self._expected_length: int | None = None

    def feed(self, data: bytes | bytearray | memoryview) -> list[dict[str, Any]]:
        self._buffer.extend(data)
        messages: list[dict[str, Any]] = []
        while True:
            if self._expected_length is None:
                if len(self._buffer) < HEADER_SIZE:
                    break
                length = struct.unpack(">I", self._buffer[:HEADER_SIZE])[0]
                del self._buffer[:HEADER_SIZE]
                if length == 0:
                    self._buffer.clear()
                    raise ProtocolError("INVALID_FRAME", "frame payload must not be empty")
                if length > self.max_frame_size:
                    self._buffer.clear()
                    code = (
                        "STDOUT_FRAMING_POLLUTION"
                        if all(0x21 <= byte <= 0x7E for byte in struct.pack(">I", length))
                        else "FRAME_TOO_LARGE"
                    )
                    raise ProtocolError(
                        code,
                        f"frame payload exceeds {self.max_frame_size} bytes",
                        details={"length": length, "limit": self.max_frame_size},
                    )
                self._expected_length = length

            expected = self._expected_length
            if expected is None or len(self._buffer) < expected:
                break
            payload = bytes(self._buffer[:expected])
            del self._buffer[:expected]
            self._expected_length = None
            messages.append(_decode_payload(payload))
        return messages

    def finish(self) -> None:
        if self._expected_length is not None or self._buffer:
            raise ProtocolError("INCOMPLETE_FRAME", "stream ended in the middle of a frame")


def decode_frame(frame: bytes) -> dict[str, Any]:
    decoder = FrameDecoder()
    messages = decoder.feed(frame)
    decoder.finish()
    if len(messages) != 1:
        raise ProtocolError("INVALID_FRAME_COUNT", "expected exactly one frame")
    return messages[0]


def _read_exact(stream: BinaryIO, length: int, *, clean_eof: bool = False) -> bytes | None:
    chunks = bytearray()
    while len(chunks) < length:
        try:
            chunk = stream.read(length - len(chunks))
        except OSError as error:
            raise ProtocolError("TRANSPORT_READ_FAILED", "pipe read failed") from error
        if not chunk:
            if clean_eof and not chunks:
                return None
            raise ProtocolError("INCOMPLETE_FRAME", "stream ended in the middle of a frame")
        chunks.extend(chunk)
    return bytes(chunks)


def read_frame(stream: BinaryIO) -> dict[str, Any] | None:
    header = _read_exact(stream, HEADER_SIZE, clean_eof=True)
    if header is None:
        return None
    length = struct.unpack(">I", header)[0]
    if length == 0:
        raise ProtocolError("INVALID_FRAME", "frame payload must not be empty")
    if length > MAX_FRAME_SIZE:
        code = (
            "STDOUT_FRAMING_POLLUTION"
            if all(0x21 <= byte <= 0x7E for byte in header)
            else "FRAME_TOO_LARGE"
        )
        raise ProtocolError(
            code,
            f"frame payload exceeds {MAX_FRAME_SIZE} bytes",
            details={"length": length, "limit": MAX_FRAME_SIZE},
        )
    payload = _read_exact(stream, length)
    assert payload is not None
    return _decode_payload(payload)


def write_frame(stream: BinaryIO, message: Mapping[str, Any]) -> None:
    try:
        stream.write(encode_frame(message))
        stream.flush()
    except OSError as error:
        raise ProtocolError("TRANSPORT_WRITE_FAILED", "pipe write failed") from error


def response(
    request: Mapping[str, Any],
    *,
    generation_id: str,
    generation_credential: str,
    protocol_minor: int = PROTOCOL_MINOR,
    payload: Mapping[str, Any] | None = None,
    error: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    succeeded = error is None
    message: dict[str, Any] = {
        "protocolMajor": PROTOCOL_MAJOR,
        "protocolMinor": protocol_minor,
        "kind": "response",
        "generationId": generation_id,
        "generationCredential": generation_credential,
        "id": str(request.get("id", "unknown")),
        "name": str(request.get("name", "unknown")),
        "payload": dict(payload or {}),
        "ok": succeeded,
    }
    if error is not None:
        message["error"] = dict(error)
    validate_envelope(message)
    return message


def event(
    request: Mapping[str, Any],
    *,
    generation_id: str,
    generation_credential: str,
    name: str | None = None,
    payload: Mapping[str, Any] | None = None,
    protocol_minor: int = EVENT_PROTOCOL_MINOR,
) -> dict[str, Any]:
    """Build the deliberately small 2.2 event envelope.

    Events carry only the identity of the request that produced them.  They
    never masquerade as responses and therefore cannot complete a waiter.
    """
    message: dict[str, Any] = {
        "protocolMajor": PROTOCOL_MAJOR,
        "protocolMinor": protocol_minor,
        "kind": "event",
        "generationId": generation_id,
        "generationCredential": generation_credential,
        "id": str(request.get("id", "unknown")),
        "name": name or str(request.get("name", "unknown")),
        "payload": dict(payload or {}),
    }
    validate_envelope(message)
    return message


def error_payload(code: str, message: str) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "retryable": False,
        "details": {},
    }
