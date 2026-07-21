"""Qt-free control dispatcher and single-writer host loop."""

from __future__ import annotations

import queue
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, BinaryIO

from .protocol import error_payload, read_frame, response, write_frame


CORE_VERSION = "0.1.0"
CAPABILITIES = (
    "system.hello",
    "system.health",
    "system.shutdown",
    "core.initialize",
    "core.snapshot",
)
_WRITER_STOP = object()
_INITIALIZE_MODES = frozenset({"ready", "setup_required", "degraded", "failed", "hang"})


@dataclass(frozen=True)
class HostConfig:
    generation_id: str
    generation_number: int = 1

    def __post_init__(self) -> None:
        if not self.generation_id.strip():
            raise ValueError("generation_id must not be empty")
        if (
            isinstance(self.generation_number, bool)
            or not isinstance(self.generation_number, int)
            or self.generation_number < 1
        ):
            raise ValueError("generation_number must be a positive integer")


class WriterError(RuntimeError):
    pass


class InitializeError(ValueError):
    pass


class ReadinessController:
    """Owns the fake background initialization state for one generation."""

    def __init__(self, config: HostConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._worker: threading.Thread | None = None
        self._closed = False
        self._readiness = "transport_ready"
        self._revision = 0
        self._component_state = "disabled"

    def begin(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        mode, delay_ms = self._validate_initialize_payload(payload)
        with self._lock:
            if self._closed:
                raise InitializeError("Core Host is shutting down")
            if self._worker is not None:
                return {
                    "accepted": True,
                    "alreadyStarted": True,
                    "readiness": self._readiness,
                }
            self._readiness = "initializing"
            self._component_state = "initializing"
            self._revision = 1
            self._worker = threading.Thread(
                target=self._initialize,
                args=(mode, delay_ms),
                name="sakura-core-host-initialize",
            )
            self._worker.start()
            return {
                "accepted": True,
                "alreadyStarted": False,
                "readiness": "initializing",
            }

    def readiness(self) -> str:
        with self._lock:
            return self._readiness

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schemaVersion": 1,
                "generationId": self._config.generation_id,
                "generationNumber": self._config.generation_number,
                "revision": self._revision,
                "readiness": self._readiness,
                "components": {"fixture": {"state": self._component_state}},
                "capabilities": list(CAPABILITIES),
                "currentCharacterSummary": None,
                "activeInteractionSummary": None,
                "coreConfigRevision": 0,
            }

    def close(self) -> None:
        with self._lock:
            if self._closed:
                worker = self._worker
            else:
                self._closed = True
                self._cancel.set()
                worker = self._worker
        if worker is not None:
            worker.join(timeout=1)
            if worker.is_alive():
                raise WriterError("initialize worker did not stop before deadline")

    @staticmethod
    def _validate_initialize_payload(payload: Mapping[str, Any]) -> tuple[str, int]:
        if set(payload) - {"mode", "delayMs"}:
            raise InitializeError("initialize payload contains unknown fields")
        mode = payload.get("mode", "ready")
        delay_ms = payload.get("delayMs", 0)
        if not isinstance(mode, str) or mode not in _INITIALIZE_MODES:
            raise InitializeError("initialize mode is unsupported")
        if isinstance(delay_ms, bool) or not isinstance(delay_ms, int) or not 0 <= delay_ms <= 5000:
            raise InitializeError("initialize delayMs must be an integer from 0 to 5000")
        return mode, delay_ms

    def _initialize(self, mode: str, delay_ms: int) -> None:
        if delay_ms and self._cancel.wait(delay_ms / 1000):
            return
        if mode == "hang":
            self._cancel.wait()
            return
        with self._lock:
            if self._closed:
                return
            self._readiness = mode
            self._component_state = mode
            self._revision = 2


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
        self._readiness = ReadinessController(config)

    def close(self) -> None:
        self._readiness.close()

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
                "hostState": self._readiness.readiness(),
            }
        elif name == "system.health":
            payload = {"hostState": self._readiness.readiness(), "status": "healthy"}
        elif name == "core.initialize":
            try:
                payload = self._readiness.begin(request["payload"])
            except InitializeError as error:
                return (
                    response(
                        request,
                        generation_id=self._config.generation_id,
                        error=error_payload("INVALID_INITIALIZE", str(error)),
                    ),
                    False,
                )
        elif name == "core.snapshot":
            payload = self._readiness.snapshot()
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
    dispatcher = ControlDispatcher(config)
    try:
        while True:
            request = read_frame(input_stream)
            if request is None:
                return
            message, should_stop = dispatcher.dispatch(request)
            writer.send(message)
            if should_stop:
                return
    finally:
        dispatcher.close()
        writer.close()
