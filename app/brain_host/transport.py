"""Brain Host stdin/stdout 二进制帧传输。"""

from __future__ import annotations

import struct
import threading
from collections.abc import Mapping
from typing import Any, BinaryIO

from app.brain_host.protocol import (
    HEADER_SIZE,
    MAX_FRAME_SIZE,
    ProtocolError,
    decode_frame,
    encode_frame,
)


class FramedTransport:
    """只在传入的二进制流上读写协议帧，不输出日志。"""

    def __init__(self, reader: BinaryIO, writer: BinaryIO) -> None:
        self.reader = reader
        self.writer = writer
        self.closed = False
        self._write_lock = threading.Lock()

    def send(self, message: Mapping[str, Any]) -> None:
        frame = encode_frame(message)
        with self._write_lock:
            if self.closed:
                raise ProtocolError("SESSION_CLOSED", "transport is closed")
            self.writer.write(frame)
            self.writer.flush()

    def receive(self) -> dict[str, Any] | None:
        if self.closed:
            raise ProtocolError("SESSION_CLOSED", "transport is closed")
        header = self._read_exact(HEADER_SIZE, allow_clean_eof=True)
        if header is None:
            return None
        length = struct.unpack(">I", header)[0]
        if length > MAX_FRAME_SIZE:
            raise ProtocolError(
                "FRAME_TOO_LARGE",
                f"frame payload exceeds {MAX_FRAME_SIZE} bytes",
                details={"length": length, "limit": MAX_FRAME_SIZE},
            )
        payload = self._read_exact(length, allow_clean_eof=False)
        if payload is None:  # pragma: no cover - allow_clean_eof=False 保证不会返回 None。
            raise ProtocolError("INCOMPLETE_FRAME", "missing frame payload")
        return decode_frame(header + payload)

    def close(self) -> None:
        with self._write_lock:
            self.closed = True

    def _read_exact(self, size: int, *, allow_clean_eof: bool) -> bytes | None:
        data = bytearray()
        while len(data) < size:
            chunk = self.reader.read(size - len(data))
            if not chunk:
                if allow_clean_eof and not data:
                    return None
                raise ProtocolError("INCOMPLETE_FRAME", "stream ended in the middle of a frame")
            data.extend(chunk)
        return bytes(data)
