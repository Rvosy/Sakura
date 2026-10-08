"""A real Core transport that stalls at one configured startup phase."""
from __future__ import annotations

import json
from pathlib import Path
import struct
import sys
import threading


def read_exact(size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = sys.stdin.buffer.read(size - len(data))
        if not chunk:
            raise SystemExit(0)
        data.extend(chunk)
    return bytes(data)


read_exact(16)
blocked = Path("phase.txt").read_text()
while True:
    size = struct.unpack(">I", read_exact(4))[0]
    request = json.loads(read_exact(size))
    if request["name"] == blocked:
        Path("waiting").write_text(blocked)
        threading.Event().wait()
    capabilities = [
        "system.hello", "system.health", "system.shutdown", "core.initialize", "core.snapshot",
        "transport.concurrent-router",
    ]
    response = {
        **{key: request[key] for key in (
            "protocolMajor", "protocolMinor", "generationId", "generationCredential", "id", "name"
        )},
        "kind": "response", "ok": True,
        "payload": {
            "coreVersion": "1.0.0", "hostState": "transport_ready",
            "protocol": {"major": 2, "minMinor": 0, "maxMinor": 2},
            "negotiated": {"major": 2, "minor": 2, "capabilities": capabilities},
            "capabilities": capabilities,
        },
    }
    encoded = json.dumps(response).encode()
    sys.stdout.buffer.write(struct.pack(">I", len(encoded)) + encoded)
    sys.stdout.buffer.flush()
