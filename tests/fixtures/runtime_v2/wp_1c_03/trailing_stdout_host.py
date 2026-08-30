from __future__ import annotations

import json
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
    value = sys.stdin.buffer.read(length)
    if len(value) != length:
        raise SystemExit(2)
    return value


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
respond(shutdown, {"accepted": True})
sys.stdout.buffer.write(b"trailing stdout pollution")
sys.stdout.buffer.flush()
