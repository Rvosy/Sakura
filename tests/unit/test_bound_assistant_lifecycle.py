import pytest

from app.core_host.assistant_adapter import BoundAssistant
from app.plugin_sdk.sakura_cancellation import OperationCancelled
from app.plugins.runtime_v4 import PluginRuntimeError


class Application:
    def __init__(self, *, fail_at=None, abort_fails=False):
        self.fail_at = fail_at
        self.abort_fails = abort_fails
        self.error = PluginRuntimeError("PLUGIN_CALL_TIMEOUT")
        self.calls = []
        self.running = False
        self.cancelled = False
        self.input_released = False
        self.aborted = False

    def create_assistant_input(self, identity, descriptor):
        return {"artifactId": "input"}

    def call_bound_service(self, service, identity, method, *args):
        self.calls.append(method)
        if method == "begin":
            self.running = True
        if method == self.fail_at:
            self.fail_at = None
            raise self.error
        if method == "cancel":
            self.cancelled = True
        if method == "poll":
            if self.cancelled:
                # Input and history stay readable until the worker reports exit.
                assert not self.input_released
                self.running = False
                return {"state": "cancelled", "sequence": 0, "progress": []}
            return {"state": "running", "sequence": 0, "progress": []}
        return {"released": True}

    def abort_bound_service(self, service, identity, *, reason):
        assert identity == {"providerId": "assistant", "scopeId": "old"}
        assert not self.input_released
        self.aborted = True
        if self.abort_fails:
            raise OSError("process cleanup failed")
        self.running = False

    def release_assistant_input(self, artifact_id):
        assert not self.running
        self.input_released = True


def assistant(application):
    return BoundAssistant(application, {"providerId": "assistant", "scopeId": "old"})


@pytest.mark.parametrize("abort_fails", [False, True])
def test_lost_begin_ack_never_replays_and_preserves_input_until_process_exit(abort_fails):
    application = Application(fail_at="begin", abort_fails=abort_fails)
    with pytest.raises(PluginRuntimeError) as caught:
        assistant(application).run_turn({"operationId": "turn"}, cancel_checker=lambda: None)
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
    application = Application(fail_at="release")
    with pytest.raises(PluginRuntimeError) as caught:
        assistant(application).release("turn", status="completed")
    assert caught.value is application.error
    assert application.calls == ["release"]
    assert application.aborted
