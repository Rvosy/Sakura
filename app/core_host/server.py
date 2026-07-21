"""Qt-free control dispatcher and single-writer host loop."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Any, BinaryIO

from .protocol import error_payload, read_frame, response, write_frame


CORE_VERSION = "0.1.0"
CAPABILITIES = ("system.hello", "system.health", "system.shutdown")
_WRITER_STOP = object()


@dataclass(frozen=True)
class HostConfig:
    generation_id: str

    def __post_init__(self) -> None:
        if not self.generation_id.strip():
            raise ValueError("generation_id must not be empty")


class WriterError(RuntimeError):
    pass


class ResponseWriter:
    """The only owner allowed to write protocol bytes to stdout."""

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self._queue: queue.Queue[dict[str, Any] | object] = queue.Queue(maxsize=32)
        self._error: BaseException | None = None
        self._closed = False
        self._thread = threading.Thread(
            target=self._run,
            name="sakura-core-host-writer",
        )
        self._thread.start()

    def send(self, message: dict[str, Any]) -> None:
        if self._closed:
            raise WriterError("writer is closed")
        if self._error is not None:
            raise WriterError("writer failed") from self._error
        self._queue.put(message, timeout=3)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._queue.put(_WRITER_STOP, timeout=3)
        self._thread.join(timeout=3)
        if self._thread.is_alive():
            raise WriterError("writer did not stop before deadline")
        if self._error is not None:
            raise WriterError("writer failed") from self._error

    def _run(self) -> None:
        try:
            while True:
                item = self._queue.get()
                try:
                    if item is _WRITER_STOP:
                        return
                    assert isinstance(item, dict)
                    write_frame(self._stream, item)
                finally:
                    self._queue.task_done()
        except BaseException as error:  # noqa: BLE001 - transferred to owner thread
            self._error = error


class ControlDispatcher:
    def __init__(self, config: HostConfig) -> None:
        self._config = config

    def dispatch(self, request: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        if request["generationId"] != self._config.generation_id:
            return (
                response(
                    request,
                    generation_id=self._config.generation_id,
                    error=error_payload("GENERATION_MISMATCH", "request belongs to another generation"),
                ),
                False,
            )
        if request["kind"] != "request":
            return (
                response(
                    request,
                    generation_id=self._config.generation_id,
                    error=error_payload("INVALID_CONTROL", "control plane accepts requests only"),
                ),
                False,
            )

        name = request["name"]
        if name == "system.hello":
            payload = {
                "capabilities": list(CAPABILITIES),
                "coreVersion": CORE_VERSION,
                "hostState": "transport_ready",
            }
        elif name == "system.health":
            payload = {"hostState": "transport_ready", "status": "healthy"}
        elif name == "system.shutdown":
            payload = {"accepted": True}
        else:
            return (
                response(
                    request,
                    generation_id=self._config.generation_id,
                    error=error_payload("UNKNOWN_CONTROL", "unsupported control request"),
                ),
                False,
            )
        return (
            response(request, generation_id=self._config.generation_id, payload=payload),
            name == "system.shutdown",
        )


def run_host(input_stream: BinaryIO, output_stream: BinaryIO, config: HostConfig) -> None:
    writer = ResponseWriter(output_stream)
    try:
        while True:
            request = read_frame(input_stream)
            if request is None:
                return
            message, should_stop = ControlDispatcher(config).dispatch(request)
            writer.send(message)
            if should_stop:
                return
    finally:
        writer.close()
