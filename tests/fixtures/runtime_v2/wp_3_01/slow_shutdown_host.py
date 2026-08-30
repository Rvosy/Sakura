from __future__ import annotations

import json
import struct
import sys
import time


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


def respond(request: dict[str, object], payload: dict[str, object]) -> None:
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


read_exact(16)
hello = read_frame()
respond(
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
time.sleep(2.9)
respond(shutdown, {"accepted": True})
