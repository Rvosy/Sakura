from __future__ import annotations

import json
import struct
import sys
import time


def read_exact(length: int) -> bytes:
    value = sys.stdin.buffer.read(length)
    if len(value) != length:
        raise SystemExit(2)
    return value


read_exact(16)
length = struct.unpack(">I", read_exact(4))[0]
request = json.loads(read_exact(length).decode("utf-8"))
response = {
    "protocolMajor": 2,
    "protocolMinor": 1,
    "kind": "response",
    "generationId": request["generationId"],
    "generationCredential": "00" * 16,
    "id": request["id"],
    "name": request["name"],
    "payload": {},
    "ok": True,
}
payload = json.dumps(response, separators=(",", ":")).encode("utf-8")
sys.stdout.buffer.write(struct.pack(">I", len(payload)) + payload)
sys.stdout.buffer.flush()
time.sleep(30)
