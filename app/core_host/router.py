"""Bounded concurrent transport router for the Runtime v2 chat surface.

The router deliberately owns no Assistant objects.  It only separates frame
reading, control dispatch, bounded fixture execution, event publication and
the single stdout writer. Assistant domain objects stay behind the injected boundary.
"""

from __future__ import annotations

import queue
import sys
import threading
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field as dataclass_field
from time import monotonic
from typing import Any, BinaryIO

from .protocol import read_frame, response


DISPATCH_QUEUE_LIMIT = 32
FIXTURE_QUEUE_LIMIT = 8
FIXTURE_WORKER_COUNT = 4
EVENT_QUEUE_LIMIT = 32
ROUTER_CLOSE_TIMEOUT_SECONDS = 3.0

_STOP = object()


def _request_interaction_context(
    request: Mapping[str, Any],
) -> AbstractContextManager[None]:
    if request.get("name") != "chat.send":
        return nullcontext()
    operation_id = request.get("id")
    if not isinstance(operation_id, str) or not operation_id.strip():
        return nullcontext()
    from app.core.interaction import interaction_context

    return interaction_context(operation_id)


@dataclass(frozen=True)
class FixtureResult:
    """Messages produced by a bounded, injected fixture handler."""

    response: Mapping[str, Any]
    events: tuple[Mapping[str, Any], ...] = ()


@dataclass
class _Ticket:
    request: dict[str, Any]
    done: threading.Event = dataclass_field(default_factory=threading.Event)
    error: BaseException | None = None


@dataclass
class _EventTicket:
    message: dict[str, Any]
    done: threading.Event = dataclass_field(default_factory=threading.Event)
    error: BaseException | None = None


class RouterFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class ConcurrentHostRouter:
    """Run one reader, one dispatcher, bounded fixture slots and one writer."""

    def __init__(
        self,
        input_stream: BinaryIO,
        writer: Any,
        dispatcher: Any,
        *,
        fixture_handler: Callable[[dict[str, Any]], FixtureResult | Mapping[str, Any]] | None = None,
        fixture_names: frozenset[str] = frozenset(),
        read_frame_fn: Callable[[BinaryIO], dict[str, Any] | None] = read_frame,
    ) -> None:
        self._input = input_stream
        self._writer = writer
        self._dispatcher = dispatcher
        self._fixture_handler = fixture_handler
        self._fixture_names = fixture_names
        self._read_frame = read_frame_fn
        self._dispatch: queue.Queue[_Ticket | object] = queue.Queue(maxsize=DISPATCH_QUEUE_LIMIT)
        self._fixtures: queue.Queue[_Ticket | object] = queue.Queue(maxsize=FIXTURE_QUEUE_LIMIT)
        self._events: queue.Queue[Mapping[str, Any] | object] = queue.Queue(maxsize=EVENT_QUEUE_LIMIT)
        self._fixture_slots = threading.BoundedSemaphore(FIXTURE_WORKER_COUNT)
        self._stop = threading.Event()
        self._events_closing = threading.Event()
        self._closed = False
        self._lock = threading.Lock()
        self._fatal: BaseException | None = None
        self._threads: list[threading.Thread] = []

    @property
    def fatal_error(self) -> BaseException | None:
        with self._lock:
            return self._fatal

    def publish_event(self, message: Mapping[str, Any]) -> None:
        """Publish a bounded event; an unrecoverable full queue fails closed."""
        if self._events_closing.is_set():
            raise RouterFailure("GENERATION_INVALIDATED", "router is closing")
        ticket = _EventTicket(dict(message))
        try:
            self._events.put(ticket, timeout=ROUTER_CLOSE_TIMEOUT_SECONDS)
        except queue.Full as error:
            failure = RouterFailure("EVENT_QUEUE_FULL", "event queue is full")
            self._set_fatal(failure)
            raise failure from error
        if not ticket.done.wait(ROUTER_CLOSE_TIMEOUT_SECONDS):
            failure = RouterFailure(
                "TRANSPORT_WRITE_FAILED",
                "chat event was not acknowledged before its deadline",
            )
            self._set_fatal(failure)
            raise failure
        if ticket.error is not None:
            raise ticket.error

    def run(self) -> None:
        self._start_threads()
        try:
            # This is the sole owner of input reads.  Dispatch and fixture work
            # never execute on this reader thread.
            while not self._stop.is_set():
                if self.fatal_error is not None:
                    raise self.fatal_error
                request = self._read_frame(self._input)
                if request is None:
                    break
                ticket = _Ticket(request)
                try:
                    self._dispatch.put(ticket, timeout=ROUTER_CLOSE_TIMEOUT_SECONDS)
                except queue.Full as error:
                    raise RouterFailure("DISPATCH_QUEUE_FULL", "dispatcher queue is full") from error
                if not self._is_fixture(request):
                    # Control acknowledgements are bounded and keep the reader
                    # from consuming unbounded input after a writer failure.
                    ticket.done.wait(ROUTER_CLOSE_TIMEOUT_SECONDS)
                    if ticket.error is not None:
                        raise ticket.error
                if request.get("name") == "system.shutdown":
                    # Shutdown response is handled by the dispatcher; do not
                    # read an unbounded stream after the terminal control item.
                    ticket.done.wait(ROUTER_CLOSE_TIMEOUT_SECONDS)
                    break
            if self.fatal_error is not None:
                raise self.fatal_error
        finally:
            self.close()

    def close(self) -> None:
        invalidate = getattr(self._dispatcher, "invalidate_generation_work", None)
        if not callable(invalidate):
            invalidate = getattr(self._dispatcher, "invalidate_chat_generation", None)
        if callable(invalidate):
            invalidate()
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._stop.set()
        self._put_stop(self._dispatch)
        self._put_stop(self._fixtures)
        deadline = monotonic() + ROUTER_CLOSE_TIMEOUT_SECONDS
        event_threads = [thread for thread in self._threads if thread.name.endswith("event-writer")]
        worker_threads = [thread for thread in self._threads if thread not in event_threads]
        for thread in worker_threads:
            thread.join(timeout=max(0.0, deadline - monotonic()))
        self._events_closing.set()
        try:
            self._events.put(_STOP, timeout=max(0.0, deadline - monotonic()))
        except queue.Full:
            pass
        for thread in event_threads:
            thread.join(timeout=max(0.0, deadline - monotonic()))
        alive = [thread.name for thread in self._threads if thread.is_alive()]
        if alive and self.fatal_error is None:
            try:
                print(f"Core router workers still alive: {','.join(alive)}", file=sys.stderr)
            except Exception:
                pass
            self._set_fatal(RouterFailure("ROUTER_CLOSE_TIMEOUT", "router workers did not stop"))
        if self.fatal_error is not None:
            raise self.fatal_error

    def _start_threads(self) -> None:
        specs = (
            ("sakura-core-host-dispatcher", self._dispatch_loop),
            ("sakura-core-host-fixture-0", self._fixture_loop),
            ("sakura-core-host-fixture-1", self._fixture_loop),
            ("sakura-core-host-fixture-2", self._fixture_loop),
            ("sakura-core-host-fixture-3", self._fixture_loop),
            ("sakura-core-host-event-writer", self._event_loop),
        )
        for name, target in specs:
            thread = threading.Thread(target=target, name=name)
            thread.start()
            self._threads.append(thread)

    @staticmethod
    def _put_stop(target: queue.Queue[Any]) -> None:
        try:
            target.put_nowait(_STOP)
        except queue.Full:
            # The worker checks _stop between items.  A full queue therefore
            # cannot prevent bounded shutdown or force an unbounded sentinel.
            pass

    def _dispatch_loop(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._dispatch.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                if item is _STOP:
                    return
                assert isinstance(item, _Ticket)
                request = item.request
                if self._is_fixture(request):
                    if not self._fixture_slots.acquire(blocking=False):
                        self._send_overload(request, item)
                        continue
                    fixture_owner = getattr(self._fixture_handler, "__self__", None)
                    reserve = getattr(fixture_owner, "reserve_send", None)
                    abandon = getattr(fixture_owner, "abandon_send", None)
                    try:
                        if callable(reserve):
                            reserve(request)
                        self._fixtures.put_nowait(item)
                    except (ValueError, RuntimeError) as error:
                        if callable(abandon):
                            abandon(request)
                        self._fixture_slots.release()
                        self._send_fixture_rejection(request, item, error)
                    except queue.Full:
                        if callable(abandon):
                            abandon(request)
                        self._fixture_slots.release()
                        self._send_overload(request, item)
                    continue
                message, should_stop = self._dispatcher.dispatch(request)
                self._send(message)
                item.done.set()
                if should_stop:
                    self._stop.set()
                    return
            except BaseException as error:  # noqa: BLE001 - transferred to owner
                item.error = error if isinstance(item, _Ticket) else error
                if isinstance(item, _Ticket):
                    item.done.set()
                self._set_fatal(error)
                return
            finally:
                self._dispatch.task_done()

    def _fixture_loop(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._fixtures.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                if item is _STOP:
                    return
                assert isinstance(item, _Ticket)
                try:
                    with _request_interaction_context(item.request):
                        result = self._fixture_handler(item.request)  # type: ignore[misc]
                    if isinstance(result, FixtureResult):
                        for message in result.events:
                            if not self._events_enabled():
                                raise RouterFailure(
                                    "CAPABILITY_NEGOTIATION_FAILED",
                                    "event capability was not negotiated",
                                )
                            self._events.put(message, timeout=ROUTER_CLOSE_TIMEOUT_SECONDS)
                        self._send(result.response)
                    else:
                        self._send(result)
                except queue.Full as error:
                    raise RouterFailure("EVENT_QUEUE_FULL", "event queue is full") from error
                finally:
                    item.done.set()
            except BaseException as error:  # noqa: BLE001 - transferred to owner
                item.error = error if isinstance(item, _Ticket) else error
                if isinstance(item, _Ticket):
                    item.done.set()
                self._set_fatal(error)
            finally:
                if item is not _STOP:
                    self._fixture_slots.release()
                self._fixtures.task_done()

    def _event_loop(self) -> None:
        while True:
            try:
                item = self._events.get(timeout=0.1)
            except queue.Empty:
                if self._stop.is_set():
                    continue
                continue
            try:
                if item is _STOP:
                    return
                if isinstance(item, _EventTicket):
                    try:
                        self._send(item.message)
                    except BaseException as error:  # noqa: BLE001
                        item.error = error
                        raise
                    finally:
                        item.done.set()
                else:
                    self._send(item)
            except BaseException as error:  # noqa: BLE001 - transferred to owner
                self._set_fatal(error)
                return
            finally:
                self._events.task_done()

    def _is_fixture(self, request: Mapping[str, Any]) -> bool:
        name = request.get("name")
        return self._fixture_handler is not None and (
            name in self._fixture_names or (isinstance(name, str) and name.startswith("fixture."))
        )

    def _events_enabled(self) -> bool:
        enabled = getattr(self._dispatcher, "events_enabled", None)
        return bool(enabled()) if callable(enabled) else True

    def _send_overload(self, request: dict[str, Any], ticket: _Ticket) -> None:
        message = response(
            request,
            generation_id=str(request["generationId"]),
            generation_credential=str(request["generationCredential"]),
            protocol_minor=int(request["protocolMinor"]),
            error={
                "code": "ROUTER_QUEUE_FULL",
                "message": "bounded fixture execution capacity is full",
                "retryable": True,
                "details": {},
            },
        )
        self._send(message)
        ticket.done.set()

    def _send_fixture_rejection(
        self,
        request: dict[str, Any],
        ticket: _Ticket,
        error: BaseException,
    ) -> None:
        code = str(getattr(error, "code", "")) or (
            "CHAT_EXECUTION_LIMIT_EXCEEDED"
            if str(error) == "CHAT_EXECUTION_LIMIT_EXCEEDED"
            else "INVALID_CHAT_PAYLOAD"
        )
        public_message = str(getattr(error, "public_message", "")) or "chat request was rejected"
        retryable = bool(getattr(error, "retryable", code == "CHAT_EXECUTION_LIMIT_EXCEEDED"))
        message = response(
            request,
            generation_id=str(request["generationId"]),
            generation_credential=str(request["generationCredential"]),
            protocol_minor=int(request["protocolMinor"]),
            error={
                "code": code,
                "message": public_message,
                "retryable": retryable,
                "details": {},
            },
        )
        self._send(message)
        ticket.done.set()

    def _send(self, message: Mapping[str, Any]) -> None:
        try:
            self._writer.send(dict(message), wait=True)
        except TypeError:
            # Test doubles and the frozen pre-router writer only accept send(x).
            self._writer.send(dict(message))

    def _set_fatal(self, error: BaseException) -> None:
        if not isinstance(error, RouterFailure) and not hasattr(error, "code"):
            error = RouterFailure("ROUTER_WORKER_FAILED", "router worker failed")
        with self._lock:
            if self._fatal is None:
                self._fatal = error
                try:
                    print(
                        f"Core router failed: {getattr(error, 'code', 'ROUTER_FAILURE')}",
                        file=sys.stderr,
                    )
                except Exception:
                    pass
            self._stop.set()


__all__ = [
    "ConcurrentHostRouter",
    "DISPATCH_QUEUE_LIMIT",
    "EVENT_QUEUE_LIMIT",
    "FIXTURE_QUEUE_LIMIT",
    "FIXTURE_WORKER_COUNT",
    "FixtureResult",
    "RouterFailure",
]
