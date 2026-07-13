"""Sakura Brain Host 第一阶段长度前缀 JSON 协议。"""

from __future__ import annotations

import json
import struct
from collections.abc import Mapping
from typing import Any


PROTOCOL_VERSION = 1
HEADER_SIZE = 4
MAX_FRAME_SIZE = 8 * 1024 * 1024
MESSAGE_KINDS = frozenset(
    {"request", "response", "event", "cancel", "stream_chunk", "stream_end"}
)


class ProtocolError(RuntimeError):
    """带稳定机器错误码的协议异常。"""

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


def validate_envelope(message: Mapping[str, Any]) -> None:
    """验证第一阶段 IPC envelope，不接受隐式类型转换。"""

    if not isinstance(message, Mapping):
        raise ProtocolError("INVALID_ENVELOPE", "message must be a JSON object")
    if message.get("protocol") != PROTOCOL_VERSION:
        raise ProtocolError("INVALID_ENVELOPE", "unsupported protocol version")

    kind = message.get("kind")
    if kind not in MESSAGE_KINDS:
        raise ProtocolError("INVALID_ENVELOPE", "unknown message kind")
    _require_non_empty_string(message, "id")
    _require_non_empty_string(message, "session_id")

    sequence = message.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise ProtocolError("INVALID_ENVELOPE", "sequence must be a positive integer")

    if kind == "request":
        _require_non_empty_string(message, "method")
    elif kind == "response" and not isinstance(message.get("ok"), bool):
        raise ProtocolError("INVALID_ENVELOPE", "response must include boolean ok")
    elif kind == "cancel":
        _require_non_empty_string(message, "target_id")

    if kind == "response" and message.get("ok") is False:
        error = message.get("error")
        if not isinstance(error, Mapping):
            raise ProtocolError("INVALID_ENVELOPE", "failed response must include error")
        _require_non_empty_string(error, "code")
        _require_non_empty_string(error, "message")
        if not isinstance(error.get("retryable"), bool):
            raise ProtocolError("INVALID_ENVELOPE", "error retryable must be boolean")
        if not isinstance(error.get("details", {}), Mapping):
            raise ProtocolError("INVALID_ENVELOPE", "error details must be an object")


def _require_non_empty_string(mapping: Mapping[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError("INVALID_ENVELOPE", f"{key} must be a non-empty string")
    return value


def encode_frame(message: Mapping[str, Any]) -> bytes:
    """将 envelope 编码为 4 字节大端长度 + 规范 JSON。"""

    validate_envelope(message)
    try:
        payload = json.dumps(
            dict(message),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProtocolError("INVALID_JSON", "message is not JSON serializable") from exc
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
    except UnicodeDecodeError as exc:
        raise ProtocolError("INVALID_UTF8", "frame payload is not valid UTF-8") from exc
    try:
        message = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError("INVALID_JSON", "frame payload is not valid JSON") from exc
    if not isinstance(message, dict):
        raise ProtocolError("INVALID_ENVELOPE", "message must be a JSON object")
    validate_envelope(message)
    return message


class FrameDecoder:
    """支持任意分片和多帧合并输入的增量解码器。"""

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
                self._expected_length = struct.unpack(">I", self._buffer[:HEADER_SIZE])[0]
                del self._buffer[:HEADER_SIZE]
                if self._expected_length > self.max_frame_size:
                    length = self._expected_length
                    self._expected_length = None
                    self._buffer.clear()
                    raise ProtocolError(
                        "FRAME_TOO_LARGE",
                        f"frame payload exceeds {self.max_frame_size} bytes",
                        details={"length": length, "limit": self.max_frame_size},
                    )

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


def create_error_response(
    *,
    request_id: str,
    session_id: str,
    sequence: int,
    code: str,
    message: str,
    retryable: bool = False,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL_VERSION,
        "kind": "response",
        "id": request_id,
        "session_id": session_id,
        "sequence": sequence,
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "details": dict(details or {}),
        },
    }


class SessionTracker:
    """验证单会话顺序并跟踪尚未完成的请求。"""

    def __init__(self, session_id: str) -> None:
        if not session_id:
            raise ValueError("session_id must not be empty")
        self.session_id = session_id
        self.last_sequence = 0
        self._seen_request_ids: set[str] = set()
        self._pending_request_ids: set[str] = set()
        self._closed = False

    @property
    def pending_request_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._pending_request_ids))

    def accept(self, message: Mapping[str, Any]) -> None:
        if self._closed:
            raise ProtocolError("SESSION_CLOSED", "IPC session is closed")
        validate_envelope(message)
        if message["session_id"] != self.session_id:
            raise ProtocolError("SESSION_MISMATCH", "message belongs to another session")

        sequence = int(message["sequence"])
        expected_sequence = self.last_sequence + 1
        if sequence != expected_sequence:
            raise ProtocolError(
                "INVALID_SEQUENCE",
                f"expected sequence {expected_sequence}, got {sequence}",
                details={"expected": expected_sequence, "actual": sequence},
            )

        if message["kind"] == "request":
            request_id = str(message["id"])
            if request_id in self._seen_request_ids:
                raise ProtocolError("DUPLICATE_REQUEST_ID", "request ID was already used")
            self._seen_request_ids.add(request_id)
            self._pending_request_ids.add(request_id)

        self.last_sequence = sequence

    def complete(self, request_id: str) -> bool:
        if request_id not in self._pending_request_ids:
            return False
        self._pending_request_ids.remove(request_id)
        return True

    def close(self) -> tuple[str, ...]:
        terminated = self.pending_request_ids
        self._pending_request_ids.clear()
        self._closed = True
        return terminated
