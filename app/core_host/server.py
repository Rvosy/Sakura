"""Qt-free control dispatcher and single-writer host loop."""

from __future__ import annotations

import hmac
import queue
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import Any, BinaryIO, Callable

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
_READINESS_CLOSE_TIMEOUT_SECONDS = 1.0
_WRITER_OPERATION_TIMEOUT_SECONDS = 3.0
_SUMMARY_KEYS = (
    "id",
    "displayName",
    "initialMessage",
    "replyTones",
    "portraitChoices",
)


@dataclass(frozen=True)
class HostConfig:
    app_root: Path
    generation_id: str
    generation_credential: str = field(repr=False)
    generation_number: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.app_root, Path):
            raise TypeError("app_root must be a Path")
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


def _default_initializer_factory(app_root: Path) -> object:
    from .assistant_adapter import AssistantAdapter

    return AssistantAdapter(app_root)


@dataclass
class _WriteRequest:
    message: dict[str, Any] = field(repr=False)
    completed: threading.Event = field(default_factory=threading.Event, repr=False)
    error: BaseException | None = field(default=None, repr=False)


class ReadinessController:
    """Owns one background Assistant initialization for one generation."""

    def __init__(
        self,
        config: HostConfig,
        *,
        initializer_factory: Callable[[Path], object] = _default_initializer_factory,
    ) -> None:
        self._config = config
        self._initializer_factory = initializer_factory
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._worker: threading.Thread | None = None
        self._closed = False
        self._close_called = False
        self._readiness = "transport_ready"
        self._revision = 0
        self._component: dict[str, object] | None = None
        self._current_character_summary: dict[str, object] | None = None
        self._initializer: object | None = None
        self._initializer_close_claimed = False
        self._initializer_close_thread: threading.Thread | None = None
        self._background_close_error: BaseException | None = None

    def begin(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping) or payload:
            raise InitializeError("initialize payload must be an empty mapping")
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
            self._component = {
                "state": "initializing",
                "code": "INITIALIZING",
                "retryable": False,
            }
            self._revision = 1
            self._worker = threading.Thread(
                target=self._initialize,
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
            components = {}
            if self._component is not None:
                components["assistant"] = dict(self._component)
            return {
                "schemaVersion": 1,
                "generationId": self._config.generation_id,
                "generationNumber": self._config.generation_number,
                "revision": self._revision,
                "readiness": self._readiness,
                "components": components,
                "capabilities": list(CAPABILITIES),
                "currentCharacterSummary": self._copy_summary(
                    self._current_character_summary
                ),
                "activeInteractionSummary": None,
                "coreConfigRevision": 0,
            }

    def close(self) -> None:
        deadline = monotonic() + _READINESS_CLOSE_TIMEOUT_SECONDS
        with self._lock:
            if self._close_called:
                return
            self._close_called = True
            self._closed = True
            self._cancel.set()
            worker = self._worker
            initializer = self._claim_initializer_close_locked()
        if initializer is not None:
            self._start_initializer_close(initializer)
        if worker is not None:
            worker.join(timeout=max(0.0, deadline - monotonic()))
        with self._lock:
            close_thread = self._initializer_close_thread
        if close_thread is not None:
            close_thread.join(timeout=max(0.0, deadline - monotonic()))
        with self._lock:
            background_error = self._background_close_error
            self._background_close_error = None
        primary_error: BaseException | None = background_error
        primary_traceback = (
            background_error.__traceback__ if background_error is not None else None
        )
        if (worker is not None and worker.is_alive()) or (
            close_thread is not None and close_thread.is_alive()
        ):
            timeout_error = WriterError(
                "SHUTDOWN_DURING_INITIALIZE",
                "Assistant cleanup did not stop before shutdown deadline",
            )
            if primary_error is None:
                primary_error = timeout_error
                primary_traceback = timeout_error.__traceback__
            else:
                self._add_cleanup_note(primary_error, timeout_error)
        if primary_error is not None:
            raise primary_error.with_traceback(primary_traceback)

    def _initialize(self) -> None:
        initializer: object | None = None
        try:
            initializer = self._initializer_factory(self._config.app_root)
            with self._lock:
                self._initializer = initializer
                close_now = self._closed
                claimed = self._claim_initializer_close_locked() if close_now else None
            if claimed is not None:
                self._start_initializer_close(claimed)
                return

            initialize = getattr(initializer, "initialize")
            result = initialize(self._cancel)
            summary = self._project_summary(result.current_character_summary)
            with self._lock:
                if self._closed:
                    claimed = self._claim_initializer_close_locked()
                else:
                    self._readiness = result.state
                    self._component = {
                        "state": result.state,
                        "code": result.code,
                        "retryable": result.retryable,
                    }
                    self._current_character_summary = summary
                    self._revision = 2
                    claimed = None
            if claimed is not None:
                self._start_initializer_close(claimed)
        except BaseException:  # noqa: BLE001 - publish a stable, sanitized readiness
            with self._lock:
                if self._closed:
                    claimed = self._claim_initializer_close_locked()
                else:
                    self._readiness = "failed"
                    self._component = {
                        "state": "failed",
                        "code": "ASSISTANT_INITIALIZATION_FAILED",
                        "retryable": False,
                    }
                    self._current_character_summary = None
                    self._revision = 2
                    claimed = None
            if claimed is not None:
                self._start_initializer_close(claimed)

    def _claim_initializer_close_locked(self) -> object | None:
        if self._initializer is None or self._initializer_close_claimed:
            return None
        self._initializer_close_claimed = True
        return self._initializer

    def _start_initializer_close(self, initializer: object) -> None:
        def close_owned_initializer() -> None:
            try:
                self._close_initializer(initializer)
            except BaseException as error:  # noqa: BLE001 - transferred to lifecycle owner
                with self._lock:
                    if self._background_close_error is None:
                        self._background_close_error = error
                    else:
                        self._add_cleanup_note(self._background_close_error, error)

        close_thread = threading.Thread(
            target=close_owned_initializer,
            name="sakura-core-host-initializer-close",
            daemon=True,
        )
        with self._lock:
            self._initializer_close_thread = close_thread
        close_thread.start()

    @staticmethod
    def _close_initializer(initializer: object) -> None:
        close = getattr(initializer, "close", None)
        if callable(close):
            close()

    @staticmethod
    def _project_summary(summary: object) -> dict[str, object] | None:
        if summary is None:
            return None
        if not isinstance(summary, Mapping):
            raise TypeError("current character summary must be a mapping")
        projected = {key: summary[key] for key in _SUMMARY_KEYS}
        if any(not isinstance(projected[key], str) for key in _SUMMARY_KEYS[:3]):
            raise TypeError("current character summary strings are invalid")
        for key in _SUMMARY_KEYS[3:]:
            values = projected[key]
            if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
                raise TypeError("current character summary arrays are invalid")
            projected[key] = [*values]
        return projected

    @staticmethod
    def _copy_summary(summary: dict[str, object] | None) -> dict[str, object] | None:
        if summary is None:
            return None
        copied = dict(summary)
        copied["replyTones"] = [*summary["replyTones"]]  # type: ignore[misc]
        copied["portraitChoices"] = [*summary["portraitChoices"]]  # type: ignore[misc]
        return copied

    @staticmethod
    def _add_cleanup_note(primary: BaseException, additional: BaseException) -> None:
        primary.add_note(f"Additional cleanup failure: {type(additional).__name__}")


class ResponseWriter:
    """The only owner allowed to write protocol bytes to stdout."""

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self._queue: queue.Queue[_WriteRequest | object] = queue.Queue(maxsize=32)
        self._error: BaseException | None = None
        self._closed = False
        self._thread = threading.Thread(
            target=self._run,
            name="sakura-core-host-writer",
        )
        self._thread.start()

    def send(self, message: dict[str, Any]) -> None:
        deadline = monotonic() + _WRITER_OPERATION_TIMEOUT_SECONDS
        if self._closed:
            raise WriterError("WRITER_QUEUE_CLOSED", "writer queue is closed")
        if self._error is not None:
            raise WriterError("TRANSPORT_WRITE_FAILED", "writer failed") from self._error
        request = _WriteRequest(message)
        try:
            self._queue.put(
                request,
                timeout=max(0.0, deadline - monotonic()),
            )
        except queue.Full as error:
            raise WriterError("WRITER_QUEUE_CLOSED", "writer queue is unavailable") from error
        if not request.completed.wait(timeout=max(0.0, deadline - monotonic())):
            raise WriterError(
                "TRANSPORT_WRITE_FAILED",
                "writer did not acknowledge the response before deadline",
            )
        if request.error is not None:
            raise WriterError("TRANSPORT_WRITE_FAILED", "writer failed") from request.error

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
                    assert isinstance(item, _WriteRequest)
                    try:
                        write_frame(self._stream, item.message)
                    except BaseException as error:  # noqa: BLE001 - transferred to owner thread
                        self._error = error
                        item.error = error
                        raise
                    finally:
                        item.completed.set()
                finally:
                    self._queue.task_done()
        except BaseException as error:  # noqa: BLE001 - transferred to owner thread
            self._error = error


class ControlDispatcher:
    def __init__(
        self,
        config: HostConfig,
        *,
        initializer_factory: Callable[[Path], object] = _default_initializer_factory,
    ) -> None:
        self._config = config
        self._readiness = ReadinessController(
            config,
            initializer_factory=initializer_factory,
        )
        self._handshake = "pending"
        self._protocol_minor = PROTOCOL_MINOR
        self._negotiated_capabilities: tuple[str, ...] = ()
        self._close_lock = threading.Lock()
        self._closed = False

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
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
    writer: ResponseWriter | None = None
    dispatcher: ControlDispatcher | None = None
    primary_error: BaseException | None = None
    primary_traceback = None
    try:
        writer = ResponseWriter(output_stream)
        dispatcher = ControlDispatcher(config)
        while True:
            request = read_frame(input_stream)
            if request is None:
                break
            message, should_stop = dispatcher.dispatch(request)
            writer.send(message)
            if should_stop:
                break
    except BaseException as error:  # noqa: BLE001 - preserve process-boundary failure
        primary_error = error
        primary_traceback = error.__traceback__

    for owner in (dispatcher, writer):
        if owner is None:
            continue
        try:
            owner.close()
        except BaseException as error:  # noqa: BLE001 - deterministic cleanup aggregation
            if primary_error is None:
                primary_error = error
                primary_traceback = error.__traceback__
            else:
                primary_error.add_note(
                    f"Additional cleanup failure: {type(error).__name__}"
                )
    if primary_error is not None:
        raise primary_error.with_traceback(primary_traceback)
