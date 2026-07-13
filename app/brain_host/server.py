"""Brain Host 帧服务器；请求读取与后台领域事件写出相互独立。"""

from __future__ import annotations

import threading
from typing import Any

from app.brain_host.application import BrainHostApplication
from app.brain_host.errors import BrainHostError
from app.brain_host.protocol import (
    PROTOCOL_VERSION,
    ProtocolError,
    SessionTracker,
    create_error_response,
)
from app.brain_host.transport import FramedTransport


class BrainHostServer:
    def __init__(self, application: BrainHostApplication, transport: FramedTransport) -> None:
        self.application = application
        self.transport = transport
        self.tracker = SessionTracker(application.config.session_id)
        self.outbound_sequence = 0
        self.authenticated = False
        self.running = True
        self._send_lock = threading.Lock()
        self.application.set_event_sink(self._emit_event)

    def serve_forever(self) -> None:
        try:
            while self.running:
                message = self.transport.receive()
                if message is None:
                    break
                self._handle(message)
        finally:
            self.running = False
            self.application.set_event_sink(None)
            self.application.shutdown()
            self.tracker.close()
            self.transport.close()

    def _handle(self, message: dict[str, Any]) -> None:
        self.tracker.accept(message)
        request_id = str(message["id"])
        try:
            if message["kind"] != "request":
                raise BrainHostError("INVALID_REQUEST", "Brain Host accepts request messages only")
            method = str(message["method"])
            if not self.authenticated and method != "system.hello":
                raise BrainHostError("HANDSHAKE_REQUIRED", "system.hello must be called first")
            payload = message.get("payload", {})
            if not isinstance(payload, dict):
                raise BrainHostError("INVALID_REQUEST", "payload must be an object")
            result = self.application.handle_request(method, payload, request_id=request_id)
            if method == "system.hello":
                self.authenticated = True
            response_payload = result
            error = None
            if method == "system.shutdown":
                self.running = False
        except BrainHostError as exc:
            response_payload = None
            error = exc
            if exc.code == "AUTHENTICATION_FAILED":
                self.running = False
        finally:
            self.tracker.complete(request_id)
        self._send_response(request_id, response_payload, error)

    def _send_response(
        self,
        request_id: str,
        payload: dict[str, Any] | None,
        error: BrainHostError | None,
    ) -> None:
        with self._send_lock:
            self.outbound_sequence += 1
            if error is None:
                response = {
                    "protocol": PROTOCOL_VERSION,
                    "kind": "response",
                    "id": request_id,
                    "session_id": self.application.config.session_id,
                    "sequence": self.outbound_sequence,
                    "ok": True,
                    "payload": payload or {},
                }
            else:
                response = create_error_response(
                    request_id=request_id,
                    session_id=self.application.config.session_id,
                    sequence=self.outbound_sequence,
                    code=error.code,
                    message=error.message,
                    retryable=error.retryable,
                    details=error.details,
                )
            self.transport.send(response)

    def _emit_event(self, name: str, payload: dict[str, Any]) -> None:
        with self._send_lock:
            if not self.running:
                return
            self.outbound_sequence += 1
            self.transport.send(
                {
                    "protocol": PROTOCOL_VERSION,
                    "kind": "event",
                    "id": f"event-{self.outbound_sequence}",
                    "session_id": self.application.config.session_id,
                    "sequence": self.outbound_sequence,
                    "method": name,
                    "payload": payload,
                }
            )


def run_server(application: BrainHostApplication, transport: FramedTransport) -> None:
    try:
        BrainHostServer(application, transport).serve_forever()
    except ProtocolError:
        transport.close()
        raise
