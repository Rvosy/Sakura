from __future__ import annotations

import json
import os
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Any


def _read_frame() -> dict[str, Any] | None:
    header = sys.stdin.buffer.read(4)
    if not header:
        return None
    if len(header) != 4:
        raise RuntimeError("incomplete frame header")
    length = struct.unpack(">I", header)[0]
    payload = sys.stdin.buffer.read(length)
    if len(payload) != length:
        raise RuntimeError("incomplete frame payload")
    message = json.loads(payload.decode("utf-8"))
    if not isinstance(message, dict):
        raise RuntimeError("frame payload must be an object")
    return message


def _write_frame(message: dict[str, Any]) -> None:
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(struct.pack(">I", len(payload)))
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


def _next_launch_count(path: Path | None) -> int:
    if path is None:
        return 1
    try:
        count = int(path.read_text(encoding="utf-8")) + 1
    except (FileNotFoundError, ValueError):
        count = 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(count), encoding="utf-8")
    return count


def _record_launch(path: Path | None, launch_count: int) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                {
                    "launch": launch_count,
                    "session_id": os.environ.get("SAKURA_SESSION_ID", ""),
                    "credential": os.environ.get("SAKURA_SESSION_CREDENTIAL", ""),
                },
                ensure_ascii=False,
            )
            + "\n"
        )


def _response(
    request: dict[str, Any],
    payload: dict[str, Any],
    *,
    sequence: int | None = None,
) -> dict[str, Any]:
    return {
        "protocol": 1,
        "kind": "response",
        "id": request["id"],
        "session_id": os.environ["SAKURA_SESSION_ID"],
        "sequence": sequence if sequence is not None else request["sequence"],
        "ok": True,
        "payload": payload,
    }


def _arm_triggered_crash(trigger: Path) -> None:
    def watch() -> None:
        while not trigger.exists():
            time.sleep(0.01)
        os._exit(17)

    threading.Thread(target=watch, name="fake-brain-crash", daemon=True).start()


def main() -> int:
    mode = os.environ.get("FAKE_BRAIN_MODE", "healthy")
    counter_text = os.environ.get("FAKE_BRAIN_COUNTER", "").strip()
    record_text = os.environ.get("FAKE_BRAIN_RECORD", "").strip()
    trigger_text = os.environ.get("FAKE_BRAIN_TRIGGER", "").strip()
    launch_count = _next_launch_count(Path(counter_text) if counter_text else None)
    _record_launch(Path(record_text) if record_text else None, launch_count)

    if mode == "crash_once_on_trigger" and launch_count == 1 and trigger_text:
        _arm_triggered_crash(Path(trigger_text))

    authenticated = False
    outbound_sequence = 0
    while True:
        request = _read_frame()
        if request is None:
            return 0
        method = request.get("method")
        payload = request.get("payload", {})
        if method == "system.hello":
            if mode == "ignore_hello":
                while True:
                    time.sleep(1)
            authenticated = (
                payload.get("protocol") == 1
                and payload.get("session_credential")
                == os.environ.get("SAKURA_SESSION_CREDENTIAL")
            )
            if not authenticated:
                return 4
            outbound_sequence += 1
            _write_frame(
                _response(
                    request,
                    {
                        "protocol": 1,
                        "session_id": os.environ["SAKURA_SESSION_ID"],
                        "backend_state": "ready",
                        "startup": {"fake": True},
                    },
                    sequence=outbound_sequence,
                )
            )
            continue
        if not authenticated:
            return 5
        if method == "system.health":
            outbound_sequence += 1
            _write_frame(
                _response(
                    request,
                    {"state": "ready", "ready": True},
                    sequence=outbound_sequence,
                )
            )
            if mode == "always_crash":
                os._exit(17)
            continue
        if method == "system.shutdown":
            if mode == "ignore_shutdown":
                while True:
                    time.sleep(1)
            outbound_sequence += 1
            _write_frame(
                _response(request, {"state": "stopped"}, sequence=outbound_sequence)
            )
            return 0
        if mode == "chat_events" and method == "chat.send":
            outbound_sequence += 1
            _write_frame(
                _response(
                    request,
                    {
                        "version": 1,
                        "interactionId": "interaction-fake",
                        "requestId": request["id"],
                    },
                    sequence=outbound_sequence,
                )
            )
            outbound_sequence += 1
            _write_frame(
                {
                    "protocol": 1,
                    "kind": "event",
                    "id": f"event-{outbound_sequence}",
                    "session_id": os.environ["SAKURA_SESSION_ID"],
                    "sequence": outbound_sequence,
                    "method": "chat.progress",
                    "payload": {
                        "version": 1,
                        "interactionId": "interaction-fake",
                        "stage": "thinking",
                    },
                }
            )
            outbound_sequence += 1
            _write_frame(
                {
                    "protocol": 1,
                    "kind": "event",
                    "id": f"event-{outbound_sequence}",
                    "session_id": os.environ["SAKURA_SESSION_ID"],
                    "sequence": outbound_sequence,
                    "method": "chat.reply",
                    "payload": {
                        "version": 1,
                        "interactionId": "interaction-fake",
                        "reply": {"version": 1, "segments": []},
                    },
                }
            )
            continue
        outbound_sequence += 1
        _write_frame(_response(request, {}, sequence=outbound_sequence))


if __name__ == "__main__":
    raise SystemExit(main())
