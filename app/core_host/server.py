"""Qt-free control dispatcher and single-writer host loop."""

from __future__ import annotations

import queue
import hmac
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, BinaryIO

from .protocol import PROTOCOL_MAJOR, PROTOCOL_MINOR, error_payload, read_frame, response, write_frame


CORE_VERSION = "0.1.0"
CAPABILITIES = (
    "system.hello",
    "system.health",
    "system.shutdown",
    "core.initialize",
    "core.snapshot",
)
MIN_PROTOCOL_MINOR = 0
REQUIRED_CAPABILITIES = frozenset(CAPABILITIES)
_WRITER_STOP = object()
_INITIALIZE_MODES = frozenset({"ready", "setup_required", "degraded", "failed", "hang"})


@dataclass(frozen=True)
class HostConfig:
    generation_id: str
    generation_credential: str
    generation_number: int = 1

    def __post_init__(self) -> None:
        if not self.generation_id.strip():
            raise ValueError("generation_id must not be empty")
        if len(self.generation_credential) != 32 or any(
            character not in "0123456789abcdef" for character in self.generation_credential
        ):
            raise ValueError("generation_credential must be a 128-bit lowercase hex value")
        if (
            isinstance(self.generation_number, bool)
            or not isinstance(self.generation_number, int)
            or self.generation_number < 1
        ):
            raise ValueError("generation_number must be a positive integer")


class WriterError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class InitializeError(ValueError):
    pass


class TransportFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class NegotiationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


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
                raise WriterError(
                    "SHUTDOWN_DURING_INITIALIZE",
                    "initialize worker did not stop before shutdown deadline",
                )

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
            raise WriterError("WRITER_QUEUE_CLOSED", "writer queue is closed")
        if self._error is not None:
            raise WriterError("TRANSPORT_WRITE_FAILED", "writer failed") from self._error
        try:
            self._queue.put(message, timeout=3)
        except queue.Full as error:
            raise WriterError("WRITER_QUEUE_CLOSED", "writer queue is unavailable") from error

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._queue.put(_WRITER_STOP, timeout=3)
            except queue.Full as error:
                raise WriterError("WRITER_QUEUE_CLOSED", "writer queue is unavailable") from error
        self._thread.join(timeout=3)
        if self._thread.is_alive():
            raise WriterError("TRANSPORT_WRITE_FAILED", "writer did not stop before deadline")
        if self._error is not None:
            raise WriterError("TRANSPORT_WRITE_FAILED", "writer failed") from self._error

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
        self._handshake = "pending"
        self._protocol_minor = PROTOCOL_MINOR
        self._negotiated_capabilities: tuple[str, ...] = ()

    def close(self) -> None:
        self._readiness.close()

    def dispatch(self, request: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        supplied_credential = request.get("generationCredential")
        if not isinstance(supplied_credential, str) or not hmac.compare_digest(
            supplied_credential, self._config.generation_credential
        ):
            raise TransportFailure(
                "GENERATION_CREDENTIAL_MISMATCH",
                "request credential does not match the active generation",
            )
        if request["generationId"] != self._config.generation_id:
            return (
                response(
                    request,
                    generation_id=self._config.generation_id,
                    generation_credential=self._config.generation_credential,
                    protocol_minor=self._protocol_minor,
                    error=error_payload("GENERATION_MISMATCH", "request belongs to another generation"),
                ),
                False,
            )
        if request["kind"] != "request":
            return (
                response(
                    request,
                    generation_id=self._config.generation_id,
                    generation_credential=self._config.generation_credential,
                    protocol_minor=self._protocol_minor,
                    error=error_payload("INVALID_CONTROL", "control plane accepts requests only"),
                ),
                False,
            )

        name = request["name"]
        if self._handshake == "failed":
            return self._error_response(
                request, "HANDSHAKE_FAILED", "protocol negotiation already failed"
            ), False
        if self._handshake == "pending" and name != "system.hello":
            if name == "system.shutdown":
                return self._error_response(
                    request,
                    "SHUTDOWN_DURING_HANDSHAKE",
                    "shutdown interrupted protocol negotiation",
                ), True
            return self._error_response(
                request, "HANDSHAKE_REQUIRED", "system.hello must be the first request"
            ), False
        if self._handshake == "complete" and name == "system.hello":
            return self._error_response(
                request, "HANDSHAKE_ALREADY_COMPLETE", "system.hello cannot be repeated"
            ), False

        if name == "system.hello":
            try:
                payload = self._negotiate(request)
            except NegotiationError as error:
                self._handshake = "failed"
                return self._error_response(request, error.code, str(error)), False
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
                        generation_credential=self._config.generation_credential,
                        protocol_minor=self._protocol_minor,
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
                        generation_credential=self._config.generation_credential,
                        protocol_minor=self._protocol_minor,
                    error=error_payload("UNKNOWN_CONTROL", "unsupported control request"),
                ),
                False,
            )
        return (
            response(
                request,
                generation_id=self._config.generation_id,
                generation_credential=self._config.generation_credential,
                protocol_minor=self._protocol_minor,
                payload=payload,
            ),
            name == "system.shutdown",
        )

    def _error_response(
        self, request: dict[str, Any], code: str, message: str
    ) -> dict[str, Any]:
        return response(
            request,
            generation_id=self._config.generation_id,
            generation_credential=self._config.generation_credential,
            protocol_minor=self._protocol_minor,
            error=error_payload(code, message),
        )

    def _negotiate(self, request: dict[str, Any]) -> dict[str, Any]:
        payload = request["payload"]
        if set(payload) != {"protocol", "requiredCapabilities", "optionalCapabilities"}:
            raise NegotiationError("INVALID_NEGOTIATION", "hello payload fields are invalid")
        protocol = payload.get("protocol")
        if not isinstance(protocol, Mapping) or set(protocol) != {"major", "minMinor", "maxMinor"}:
            raise NegotiationError("INVALID_NEGOTIATION", "protocol range is invalid")
        major = _negotiation_integer(protocol, "major")
        minimum = _negotiation_integer(protocol, "minMinor")
        maximum = _negotiation_integer(protocol, "maxMinor")
        if minimum > maximum:
            raise NegotiationError("INVALID_NEGOTIATION", "protocol minor range is invalid")
        required = _capability_list(payload, "requiredCapabilities")
        optional = _capability_list(payload, "optionalCapabilities")
        if set(required) & set(optional):
            raise NegotiationError("INVALID_NEGOTIATION", "capability lists overlap")
        if major != PROTOCOL_MAJOR or request["protocolMajor"] != major:
            raise NegotiationError("PROTOCOL_MAJOR_MISMATCH", "protocol major is incompatible")
        selected_minimum = max(minimum, MIN_PROTOCOL_MINOR)
        selected_maximum = min(maximum, PROTOCOL_MINOR)
        if selected_minimum > selected_maximum:
            raise NegotiationError(
                "CAPABILITY_NEGOTIATION_FAILED", "protocol minor ranges do not overlap"
            )
        missing = [capability for capability in required if capability not in REQUIRED_CAPABILITIES]
        if missing:
            raise NegotiationError(
                "CAPABILITY_NEGOTIATION_FAILED", "a required capability is unavailable"
            )
        requested = set(required) | set(optional)
        selected = tuple(capability for capability in CAPABILITIES if capability in requested)
        self._protocol_minor = selected_maximum
        self._negotiated_capabilities = selected
        self._handshake = "complete"
        return {
            "capabilities": list(selected),
            "coreVersion": CORE_VERSION,
            "hostState": self._readiness.readiness(),
            "protocol": {
                "major": PROTOCOL_MAJOR,
                "minMinor": MIN_PROTOCOL_MINOR,
                "maxMinor": PROTOCOL_MINOR,
            },
            "negotiated": {
                "major": PROTOCOL_MAJOR,
                "minor": selected_maximum,
                "capabilities": list(selected),
            },
        }


def _negotiation_integer(mapping: Mapping[str, Any], key: str) -> int:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise NegotiationError("INVALID_NEGOTIATION", f"{key} must be a non-negative integer")
    return value


def _capability_list(mapping: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = mapping.get(key)
    if not isinstance(value, list):
        raise NegotiationError("INVALID_NEGOTIATION", f"{key} must be an array")
    capabilities: list[str] = []
    for capability in value:
        if not isinstance(capability, str) or not capability or capability != capability.strip():
            raise NegotiationError("INVALID_NEGOTIATION", f"{key} contains an invalid capability")
        if capability in capabilities:
            raise NegotiationError("INVALID_NEGOTIATION", f"{key} contains a duplicate capability")
        capabilities.append(capability)
    return tuple(capabilities)


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
