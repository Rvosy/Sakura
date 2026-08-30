from __future__ import annotations

import json
import os
import struct
import sys


CAPABILITIES = [
    "system.hello",
    "system.health",
    "system.shutdown",
    "core.initialize",
    "core.snapshot",
]


def read_exact(length: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < length:
        chunk = sys.stdin.buffer.read(length - len(chunks))
        if not chunk:
            raise SystemExit(0)
        chunks.extend(chunk)
    return bytes(chunks)


def read_frame() -> dict[str, object]:
    length = struct.unpack(">I", read_exact(4))[0]
    return json.loads(read_exact(length).decode("utf-8"))


def write_response(request: dict[str, object], payload: dict[str, object]) -> None:
    response = {
        "protocolMajor": 2,
        "protocolMinor": 1,
        "kind": "response",
        "generationId": request["generationId"],
        "generationCredential": request["generationCredential"],
        "id": request["id"],
        "name": request["name"],
        "payload": payload,
        "ok": True,
    }
    encoded = json.dumps(response, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(struct.pack(">I", len(encoded)) + encoded)
    sys.stdout.buffer.flush()


credential = read_exact(16).hex()
hello = read_frame()
os.write(2, "ordinary\n分片 UTF-8\n".encode("utf-8"))
os.write(2, b"\xff\x00binary\n")
os.write(
    2,
    (
        f"credential={credential} token=private Authorization: Bearer private "
        "cookie=session content=user-chat\n"
    ).encode("utf-8"),
)
for _ in range(256):
    os.write(2, b"x" * 4096)
write_response(
    hello,
    {
        "capabilities": CAPABILITIES,
        "coreVersion": "fixture",
        "hostState": "transport_ready",
        "protocol": {"major": 2, "minMinor": 0, "maxMinor": 1},
        "negotiated": {"major": 2, "minor": 1, "capabilities": CAPABILITIES},
    },
)
shutdown = read_frame()
write_response(shutdown, {"accepted": True})
