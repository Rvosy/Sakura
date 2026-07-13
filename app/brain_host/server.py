"""Brain Host 同步帧服务器。"""

from __future__ import annotations

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

    def serve_forever(self) -> None:
        while self.running:
            message = self.transport.receive()
            if message is None:
                break
            self._handle(message)
        self.tracker.close()
        self.transport.close()

    def _handle(self, message: dict[str, Any]) -> None:
        self.tracker.accept(message)
        request_id = str(message["id"])
        self.outbound_sequence += 1
        try:
            if message["kind"] != "request":
                raise BrainHostError("INVALID_REQUEST", "Brain Host accepts request messages only")
            method = str(message["method"])
            if not self.authenticated and method != "system.hello":
                raise BrainHostError("HANDSHAKE_REQUIRED", "system.hello must be called first")
            payload = message.get("payload", {})
            if not isinstance(payload, dict):
                raise BrainHostError("INVALID_REQUEST", "payload must be an object")
            result = self.application.handle_request(method, payload)
            if method == "system.hello":
                self.authenticated = True
            response = {
                "protocol": PROTOCOL_VERSION,
                "kind": "response",
                "id": request_id,
                "session_id": self.application.config.session_id,
                "sequence": self.outbound_sequence,
                "ok": True,
                "payload": result,
            }
            if method == "system.shutdown":
                self.running = False
        except BrainHostError as exc:
            response = create_error_response(
                request_id=request_id,
                session_id=self.application.config.session_id,
                sequence=self.outbound_sequence,
                code=exc.code,
                message=exc.message,
                retryable=exc.retryable,
                details=exc.details,
            )
            if exc.code == "AUTHENTICATION_FAILED":
                self.running = False
        finally:
            self.tracker.complete(request_id)
        self.transport.send(response)


def run_server(application: BrainHostApplication, transport: FramedTransport) -> None:
    try:
        BrainHostServer(application, transport).serve_forever()
    except ProtocolError:
        transport.close()
        raise
