import pytest

from app.core_host.assistant_adapter import BoundAssistant
from app.plugin_sdk.sakura_cancellation import OperationCancelled
from app.plugins.runtime_v4 import PluginRuntimeError


class Application:
    def __init__(self, *, fail_at=None, abort_fails=False, completes=False):
        self.fail_at = fail_at
        self.abort_fails = abort_fails
        self.error = PluginRuntimeError("PLUGIN_CALL_TIMEOUT")
        self.calls = []
        self.running = False
        self.cancelled = False
        self.input_released = False
        self.aborted = False
        self.completes = completes

    def create_assistant_input(self, identity, descriptor):
        return {"artifactId": "input"}

    def call_bound_service(self, service, identity, method, *args, timeout=None):
        self.calls.append(method)
        if method == "begin":
            self.running = True
        if method == self.fail_at:
            self.fail_at = None
            raise self.error
        if method == "cancel":
            self.cancelled = True
        if method == "poll":
            if self.completes:
                self.running = False
                return {"state": "completed", "sequence": 0, "progress": []}
            if self.cancelled:
                # Input and history stay readable until the worker reports exit.
                assert not self.input_released
                self.running = False
                return {"state": "cancelled", "sequence": 0, "progress": []}
            return {"state": "running", "sequence": 0, "progress": []}
        return {"released": True}

    def abort_bound_service(self, service, identity, *, reason):
        assert identity == {"providerId": "assistant", "scopeId": "old"}
        assert not self.running or not self.input_released
        self.aborted = True
        if self.abort_fails:
            raise OSError("process cleanup failed")
        self.running = False

    def release_assistant_input(self, artifact_id):
        assert not self.running
        self.input_released = True

    def read_assistant_result(self, _artifact):
        return {"reply": {"segments": [{"text": "ready"}]}, "actions": []}


def assistant(application):
    return BoundAssistant(application, {"providerId": "assistant", "scopeId": "old"})


@pytest.mark.parametrize("state", ["ready", "degraded", "setup_required", "failed"])
def test_prepare_accepts_third_party_status_codes_without_requesting_recovery(state):
    from types import SimpleNamespace
    calls = []
    result = {"state": state, "code": "vendor.account_required", "message": "请登录。", "retryable": True, "extra": "ignored"}
    application = SimpleNamespace(call_bound_service=lambda *args: calls.append(args) or result)
    assert assistant(application).prepare({}) == {key: result[key] for key in ("state", "code", "message", "retryable")}
    assert len(calls) == 1


@pytest.mark.parametrize("invalid", [None, {}, {"state": "initializing"}, {"state": []},
    {"code": ""}, {"code": "x" * 81}, {"code": "has space"},
    {"message": None}, {"message": "文" * 2001}, {"retryable": "false"}])
def test_prepare_rejects_invalid_public_status(invalid):
    from types import SimpleNamespace
    result = {"state": "ready", "code": "READY", "message": "", "retryable": False}
    if isinstance(invalid, dict) and invalid:
        result.update(invalid)
    else:
        result = invalid
    application = SimpleNamespace(call_bound_service=lambda *args: result)
    with pytest.raises(ValueError, match="ASSISTANT_PREPARE_INVALID"):
        assistant(application).prepare({})


@pytest.mark.parametrize("abort_fails", [False, True])
def test_lost_begin_ack_never_replays_and_preserves_input_until_process_exit(abort_fails):
    application = Application(fail_at="begin", abort_fails=abort_fails)
    bound = assistant(application)
    with pytest.raises(PluginRuntimeError) as caught:
        bound.run_turn({"operationId": "turn"}, cancel_checker=lambda: None)
    bound.release("turn", status="failed")
    assert caught.value is application.error
    assert application.calls == ["begin"]
    assert application.aborted
    assert application.input_released is not abort_fails


def test_poll_failure_cancels_and_waits_before_releasing_history_grant():
    application = Application(fail_at="poll")
    with pytest.raises(PluginRuntimeError) as caught:
        assistant(application).run_turn({"operationId": "turn"}, cancel_checker=lambda: None)
    assert caught.value is application.error
    assert application.calls == ["begin", "poll", "cancel", "poll"]
    assert application.input_released
    assert not application.aborted


def test_cancel_transport_failure_keeps_original_cancellation_and_reaps_process():
    application = Application(fail_at="cancel")
    error = OperationCancelled()

    def cancel_checker():
        if application.running:
            raise error

    with pytest.raises(OperationCancelled) as caught:
        assistant(application).run_turn({"operationId": "turn"}, cancel_checker=cancel_checker)
    assert caught.value is error
    assert application.calls == ["begin", "cancel"]
    assert application.aborted
    assert application.input_released


def test_lost_release_ack_reaps_exact_provider_instead_of_leaving_busy_slot():
    application = Application(fail_at="release", completes=True)
    bound = assistant(application)
    bound.run_turn({"operationId": "turn"}, cancel_checker=lambda: None)
    application.calls.clear()
    with pytest.raises(PluginRuntimeError) as caught:
        bound.release("turn", status="completed")
    assert caught.value is application.error
    assert application.calls == ["release"]
    assert application.aborted


@pytest.mark.parametrize("code", ["ASSISTANT_BUSY", "ASSISTANT_CLOSED", "ASSISTANT_INPUT_INVALID"])
def test_known_begin_rejection_never_claims_or_releases_an_operation(code):
    class RejectingApplication(Application):
        def call_bound_service(self, service, identity, method, *args, **kwargs):
            assert method == "begin"
            self.calls.append(method)
            raise PluginRuntimeError(code)

    application = RejectingApplication()
    bound = assistant(application)
    with pytest.raises(PluginRuntimeError, match=code):
        bound.run_turn({"operationId": "turn"}, cancel_checker=lambda: None)
    assert bound.release("turn", status="failed") == {"released": True}
    assert application.calls == ["begin"]
    assert application.input_released
    assert not application.aborted


@pytest.mark.parametrize("phase", ["cancel", "release"])
def test_responsive_but_stuck_cleanup_expires_and_aborts_only_once(monkeypatch, phase):
    from app.core_host import assistant_adapter
    now = [0.0]
    monkeypatch.setattr(assistant_adapter, "monotonic", lambda: now[0])
    timeouts = []

    class StuckApplication(Application):
        cleaning = False

        def call_bound_service(self, service, identity, method, *args, timeout=None):
            if method in {"cancel", "release"}:
                self.cleaning = True
                self.calls.append(method)
                timeouts.append(timeout)
                return {"released": False, "cancelled": True}
            if method == "poll" and self.cleaning:
                self.calls.append(method)
                timeouts.append(timeout)
                now[0] += 0.4
                return {"state": "running", "sequence": 0, "progress": []}
            return super().call_bound_service(service, identity, method, *args, timeout=timeout)

    application = StuckApplication(completes=phase == "release")
    bound = assistant(application)
    cancellation = OperationCancelled()
    def check():
        if phase == "cancel" and application.running:
            raise cancellation

    if phase == "cancel":
        with pytest.raises(OperationCancelled) as caught:
            bound.run_turn({"operationId": "turn"}, cancel_checker=check)
        assert caught.value is cancellation
    else:
        bound.run_turn({"operationId": "turn"}, cancel_checker=check)
        with pytest.raises(PluginRuntimeError, match="ASSISTANT_CLEANUP_TIMEOUT"):
            bound.release("turn", status="completed")
    assert application.aborted
    assert application.input_released
    assert 0 < now[0] <= bound.CLEANUP_TIMEOUT_SECONDS + 0.4
    assert all(0 < timeout <= bound.CLEANUP_TIMEOUT_SECONDS for timeout in timeouts)
    assert timeouts == sorted(timeouts, reverse=True)
    calls = list(application.calls)
    bound.release("turn", status="failed")
    assert application.calls == calls


def test_normal_generation_has_no_cleanup_deadline(monkeypatch):
    from app.core_host import assistant_adapter
    now = [0.0]
    monkeypatch.setattr(assistant_adapter, "monotonic", lambda: now[0])
    class SlowApplication(Application):
        def call_bound_service(self, service, identity, method, *args, timeout=None):
            if method == "poll":
                assert timeout is None
                now[0] += 100
                self.completes = now[0] >= 300
            return super().call_bound_service(service, identity, method, *args, timeout=timeout)
    application = SlowApplication()
    bound = assistant(application)
    assert bound.run_turn({"operationId": "turn"}, cancel_checker=lambda: None).reply.text == "ready"
    assert bound.release("turn", status="completed") == {"released": True}
    assert now[0] == 300
    assert not application.aborted


def test_cancel_and_later_release_share_one_cleanup_budget(monkeypatch):
    from app.core_host import assistant_adapter
    now = [0.0]
    monkeypatch.setattr(assistant_adapter, "monotonic", lambda: now[0])
    class ApplicationWithSlowCancel(Application):
        releasing = False
        def call_bound_service(self, service, identity, method, *args, timeout=None):
            if method == "release":
                self.releasing = True
                return {"released": False}
            if method == "poll":
                if self.releasing:
                    now[0] += 0.3
                    return {"state": "running", "sequence": 0, "progress": []}
                if self.cancelled:
                    now[0] += 1.8
            return super().call_bound_service(service, identity, method, *args, timeout=timeout)
    application = ApplicationWithSlowCancel()
    bound = assistant(application)
    def check():
        if application.running:
            raise OperationCancelled()
    with pytest.raises(OperationCancelled):
        bound.run_turn({"operationId": "turn"}, cancel_checker=check)
    with pytest.raises(PluginRuntimeError, match="ASSISTANT_CLEANUP_TIMEOUT"):
        bound.release("turn", status="cancelled")
    assert now[0] == pytest.approx(2.1)
    assert application.aborted
