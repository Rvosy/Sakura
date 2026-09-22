"""Screen cleanup crosses the real request timeout and late-response boundary."""

import socket
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.core_host.screen_host import ScreenHost, ScreenHostError, _CANCELLED_OPERATION_LIMIT
from app.plugin_sdk.sakura_screen import ScreenClient, ScreenError
from app.plugins.host_services import HOST_CALLER, HOST_CALLER_SCOPE
from app.plugins.sakura_plugin_sdk import DEFAULT_CALL_TIMEOUT_SECONDS, PluginApiError, PluginContext, RpcPeer


@contextmanager
def caller(owner="screen-client", scope="scope"):
    first, second = HOST_CALLER.set(owner), HOST_CALLER_SCOPE.set(scope)
    try:
        yield
    finally:
        HOST_CALLER_SCOPE.reset(second)
        HOST_CALLER.reset(first)


class ScreenRpc:
    def __init__(self, tmp_path):
        self.events, self.calls = [], []
        self.before_capture = self.after_capture = self.after_cleanup = lambda: None
        self.capture_finished = threading.Event()
        self.screen = ScreenHost("generation", session_provider=lambda: "session", emit_callback=self.emit,
            resource_consumer=lambda *args, **kwargs: SimpleNamespace(data_url="data:image/jpeg;base64,AA==",
                width=1, height=1, captured_at="2026-09-19T00:00:00Z", screen_name="fixture"))
        self.sockets = socket.socketpair()
        self.streams = [sock.makefile("rwb", buffering=0) for sock in self.sockets]
        self.client_peer = RpcPeer(self.streams[0], self.streams[0], generation_id="generation",
            plugin_id="screen-client", request_handler=lambda *args: None)
        self.host_peer = RpcPeer(self.streams[1], self.streams[1], generation_id="generation",
            plugin_id="screen-client", request_handler=self.handle)
        self.client_peer.start(thread_name="screen-client-rpc")
        self.host_peer.start(thread_name="screen-host-rpc")
        context = PluginContext("screen-client", tmp_path, tmp_path,
            lambda service, method, args: self.client_peer.request("service.call",
                {"serviceKey": service, "method": method, "args": list(args)}),
            lambda name, payload: self.client_peer.request(name, payload, timeout=payload.get("timeoutSeconds", 3)))
        self.client = ScreenClient(context.get("sakura.host.screen"), capture_timeout=0.15, cleanup_timeout=0.15)

    def emit(self, name, payload):
        assert name == "host.screen.capture"
        assert set(payload) == {"requestId", "sessionId", "resolution"}
        self.events.append(payload)
        self.screen.complete({**payload, "resource": {}})

    def handle(self, name, payload):
        assert name == "service.call"
        assert payload["serviceKey"] == "sakura.host.screen"
        method = payload["method"]
        self.calls.append(method)
        with caller():
            try:
                if method == "capture":
                    self.before_capture()
                result = getattr(self.screen, method)(*payload["args"])
                if method == "capture":
                    self.after_capture()
                elif method == "release_capture":
                    self.after_cleanup()
                return result
            finally:
                if method == "capture":
                    self.capture_finished.set()

    def capture(self):
        return self.client.capture({"sessionId": "session", "resolution": "720p"})

    def close(self):
        self.screen.close()
        self.client_peer.close()
        self.host_peer.close()
        for sock in self.sockets:
            sock.shutdown(socket.SHUT_RDWR)
        for stream in self.streams:
            stream.close()
        for sock in self.sockets:
            sock.close()


@pytest.fixture
def rpc(tmp_path):
    fixture = ScreenRpc(tmp_path)
    try:
        yield fixture
    finally:
        fixture.close()


def test_default_screen_deadline_survives_standard_rpc_timeout(rpc):
    emitted, finished = threading.Event(), threading.Event()
    pending, results, errors = [], [], []
    def emit(name, payload):
        pending.append(payload)
        emitted.set()
    def capture():
        try:
            results.append(rpc.capture())
        except BaseException as error:
            errors.append(error)
        finally:
            finished.set()
    rpc.client = ScreenClient(rpc.client._service)
    rpc.screen._emit = emit
    worker = threading.Thread(target=capture)
    worker.start()
    try:
        assert emitted.wait(3)
        assert not finished.wait(DEFAULT_CALL_TIMEOUT_SECONDS + 0.1)
        assert len(pending) == 1
        assert rpc.screen.complete({**pending[0], "resource": {}}) == {"accepted": True}
        assert finished.wait(3)
        assert not errors
        assert len(results) == 1
        assert rpc.client.release(results[0]["resourceId"]) == {"released": True}
        assert not rpc.screen._resources
        assert not rpc.screen._operations
    finally:
        rpc.screen.close()
        worker.join(3)


@pytest.mark.parametrize("delay", ["before_capture", "after_capture"])
def test_capture_timeout_cleans_late_capture_and_lost_reply_and_scope_can_continue(rpc, delay):
    gate, reached = threading.Event(), threading.Event()
    def hold():
        reached.set()
        assert gate.wait(3)
    setattr(rpc, delay, hold)
    try:
        with pytest.raises(PluginApiError, match="PLUGIN_CALL_TIMEOUT"):
            rpc.capture()
        assert reached.is_set()
        assert rpc.calls == ["capture", "release_capture"]
        assert not rpc.screen._resources
        assert not rpc.screen._operations
        assert not rpc.client._pending
        gate.set()
        assert rpc.capture_finished.wait(3)
        assert not rpc.screen._pending
        assert not rpc.screen._cancelled.get(("screen-client", "scope"))
        assert len(rpc.events) == (0 if delay == "before_capture" else 1)
        setattr(rpc, delay, lambda: None)
        result = rpc.capture()
        assert rpc.client.release(result["resourceId"]) == {"released": True}
        assert not rpc.screen._resources
        assert not rpc.screen._operations
    finally:
        gate.set()


def test_unknown_cleanup_ack_blocks_new_capture_until_confirmed(rpc):
    capture_gate, cleanup_gate = threading.Event(), threading.Event()
    def hold_capture():
        assert capture_gate.wait(3)
    def hold_cleanup():
        assert cleanup_gate.wait(3)
    rpc.after_capture, rpc.after_cleanup = hold_capture, hold_cleanup
    try:
        with pytest.raises(PluginApiError, match="PLUGIN_CALL_TIMEOUT") as failed:
            rpc.capture()
        assert failed.value.recovery_error.code == "PLUGIN_CALL_TIMEOUT"
        assert len(rpc.client._pending) == 1
        assert not rpc.screen._resources
        with pytest.raises(ScreenError, match="SCREEN_CAPTURE_CLEANUP_PENDING"):
            rpc.capture()
        assert rpc.calls.count("capture") == 1
        assert len(rpc.client._pending) == 1
        cleanup_gate.set()
        capture_gate.set()
        assert rpc.capture_finished.wait(3)
        rpc.after_capture = lambda: None
        image = rpc.capture()
        assert not rpc.client._pending
        rpc.client.release(image["resourceId"])
        assert not rpc.screen._resources
        assert not rpc.screen._operations
    finally:
        capture_gate.set()
        cleanup_gate.set()


def test_capture_timeout_cancels_host_wait_and_consumes_late_shell_resource(rpc):
    pending = []
    rpc.screen._emit = lambda name, payload: pending.append(payload)
    with pytest.raises(PluginApiError, match="PLUGIN_CALL_TIMEOUT"):
        rpc.capture()
    assert rpc.capture_finished.wait(3)
    assert len(pending) == 1
    consumed = []
    rpc.screen._consume = lambda resource, **kwargs: consumed.append(resource)
    assert rpc.screen.complete({**pending[0], "resource": {"late": True}}) == {"accepted": False}
    assert consumed == [{"late": True}]
    assert not rpc.screen._pending
    assert not rpc.screen._resources
    assert not rpc.screen._operations


def test_client_close_cleans_inflight_capture_before_its_reply_arrives(rpc):
    gate, reached = threading.Event(), threading.Event()
    errors = []
    def hold():
        reached.set()
        assert gate.wait(3)
    def capture():
        try:
            rpc.capture()
        except BaseException as error:
            errors.append(error)
    rpc.after_capture = hold
    rpc.client._capture_timeout = 10
    worker = threading.Thread(target=capture)
    worker.start()
    try:
        assert reached.wait(3)
        rpc.client.close()
        assert not rpc.screen._resources
        assert not rpc.screen._operations
        gate.set()
        worker.join(3)
        assert not worker.is_alive()
        assert len(errors) == 1 and errors[0].code == "SCREEN_CLIENT_CLOSED"
        assert not rpc.client._pending
    finally:
        gate.set()
        worker.join(3)


def test_operation_cleanup_is_owner_scoped_and_known_release_does_not_accumulate(rpc):
    for index in range(25):
        with caller():
            image = rpc.screen.capture({"operationId": str(index), "sessionId": "session", "resolution": "720p"})
        with caller(owner="other"):
            assert rpc.screen.release_capture(str(index)) == {"released": False}
        assert image["resourceId"] in rpc.screen._resources
        with caller():
            assert rpc.screen.release_capture(str(index)) == {"released": True}
        assert not rpc.screen._resources
        assert not rpc.screen._operations
        assert not rpc.screen._cancelled.get(("screen-client", "scope"))


def test_cancelled_operation_limit_never_revives_late_capture_and_reload_recovers(rpc):
    with caller():
        with pytest.raises(ScreenHostError, match="SCREEN_CAPTURE_REQUEST_INVALID"):
            rpc.screen.capture({"sessionId": "session", "resolution": "720p"})
        for index in range(_CANCELLED_OPERATION_LIMIT):
            assert rpc.screen.release_capture(str(index)) == {"released": False}
        with pytest.raises(ScreenHostError, match="SCREEN_CANCELLATION_LIMIT"):
            rpc.screen.release_capture("overflow")
        with pytest.raises(ScreenHostError, match="SCREEN_CAPTURE_CANCELLED"):
            rpc.screen.capture({"operationId": "0", "sessionId": "session", "resolution": "720p"})
        with pytest.raises(ScreenHostError, match="SCREEN_CANCELLATION_LIMIT"):
            rpc.screen.capture({"operationId": "fresh", "sessionId": "session", "resolution": "720p"})
    assert not rpc.events
    with caller(owner="other"):
        image = rpc.screen.capture({"operationId": "0", "sessionId": "session", "resolution": "720p"})
        rpc.screen.release(image["resourceId"])
    rpc.screen.revoke_scope("screen-client")
    assert not rpc.screen._cancelled
    assert not rpc.screen._cancelled_limit
    with caller(scope="replacement"):
        image = rpc.screen.capture({"operationId": "0", "sessionId": "session", "resolution": "720p"})
        rpc.screen.release(image["resourceId"])
