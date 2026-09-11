import io
import json

import pytest

from app.core.diagnostics import exception_diagnostics, safe_diagnostic_text
from app.core.runtime_log import log_event, log_message
from app.core_host.runtime_logging import install_runtime_logging, CORE_BRIDGE_PREFIX
from app.plugins.sakura_plugin_sdk import PluginApiError, _exception_diagnostics


def test_wrapped_error_keeps_root_message_frames_and_system_code():
    try:
        try:
            raise PermissionError(13, "cannot replace file", r"C:\Users\private\plugin.py")
        except OSError as cause:
            raise RuntimeError("plugin installation failed") from cause
    except RuntimeError as error:
        fields = exception_diagnostics(error, reason_code="INSTALL_FAILED", stage="replace")
    assert "cannot replace file" in fields["diagnostic"]
    assert fields["errno"] == 13
    assert fields["cause_type"] == "PermissionError"
    assert "RuntimeError" in fields["exception_chain"]
    assert " at " in fields["exception_stack"]
    assert "C:" not in json.dumps(fields)


def test_local_diagnostic_redacts_fragments_without_discarding_error():
    raw = '''Permission denied: 'C:\\Users\\Private Name\\config.yaml'
https://user:pass@example.test/v1/models?api_key=secret-value
Authorization: Bearer private-token
{"password": "quoted private value"}'''
    text = safe_diagnostic_text(raw)
    assert "Permission denied" in text and "config.yaml" in text
    assert "https://example.test/v1/models" in text
    for private in ["Private Name", "private-token", "quoted private value", "secret-value", "user:pass"]:
        assert private not in text
    assert "[truncated:" in safe_diagnostic_text("多行错误\n" * 2000, 512)
    assert "C:" not in safe_diagnostic_text("file:///C:/Users/private/file.txt")


def test_broken_message_and_cyclic_cause_do_not_break_reporting():
    class Broken(Exception):
        def __str__(self):
            raise ValueError("bad formatter")
    error = Broken()
    error.__cause__ = error
    result = exception_diagnostics(error, reason_code="FAILED", stage="test")
    assert "could not be formatted" in result["diagnostic"]


@pytest.mark.parametrize("custom", [False, True])
def test_fixed_and_custom_logs_preserve_multiline_diagnostics(custom):
    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    try:
        try:
            raise RuntimeError("weights have incompatible sizes\nexpected [400, 192], got [322, 192]")
        except RuntimeError:
            if custom:
                log_message("warning", "weight loading failed")
            else:
                log_event("TTS", "weight loading failed", event="tts.weights.failed", severity="warning")
    finally:
        bridge.close()
    record = json.loads(stream.getvalue().splitlines()[0][len(CORE_BRIDGE_PREFIX):])
    assert "expected [400, 192]" in record["attributes"]["diagnostic"]
    assert "\n" in record["attributes"]["diagnostic"]
    assert "exception_stack" in record["attributes"]


def test_worker_diagnostic_survives_host_exception_wrapping():
    try:
        raise ImportError("No module named demo_dependency")
    except ImportError as error:
        remote = _exception_diagnostics(error)
    wrapped = PluginApiError("PLUGIN_CALL_FAILED", "plugin call failed", diagnostics=remote)
    try:
        raise RuntimeError("Settings action failed") from wrapped
    except RuntimeError as error:
        fields = exception_diagnostics(error, reason_code="SETTINGS_ACTION_FAILED", stage="action")
    assert fields["cause_type"] == "ImportError"
    assert "demo_dependency" in fields["diagnostic"]
    assert "Remote:" in fields["exception_stack"]


def test_exception_group_preserves_independent_failures():
    group = ExceptionGroup("parallel providers failed", [TimeoutError("connection timed out"), OSError(28, "No space left on device")])
    fields = exception_diagnostics(group, reason_code="PROVIDERS_FAILED", stage="load")
    assert "connection timed out" in fields["exception_chain"]
    assert "No space left on device" in fields["exception_chain"]


def test_provider_diagnostics_keep_error_fields_without_response_content():
    from app.llm.api_client import ApiRequestError
    error = ApiRequestError('API HTTP 401: {"error":{"message":"invalid credential","code":"invalid_api_key"},"choices":[{"message":{"content":"unrelated private output"}}]}')
    fields = exception_diagnostics(error, reason_code="API_FAILED", stage="request")
    assert "invalid credential" in fields["diagnostic"]
    assert "invalid_api_key" in fields["exception_chain"]
    assert "unrelated private output" not in json.dumps(fields)


def test_plugin_logging_and_fixed_diagnostics_cross_host_with_original_error(tmp_path):
    from app.plugins.sakura_plugin_sdk import PluginContext
    from app.core_host.plugin_host_services import _LoggingHostService, _DiagnosticsHostService
    from app.plugins.host_services import HOST_CALLER
    import threading

    delivered = threading.Event()
    def remote(service, method, args):
        token = HOST_CALLER.set("fixture.diagnostics")
        try:
            result = (_LoggingHostService() if service == "sakura.host.logging" else _DiagnosticsHostService()).call(method, args)
            delivered.set()
            return result
        finally:
            HOST_CALLER.reset(token)
    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    context = PluginContext("fixture.diagnostics", tmp_path, tmp_path, remote, lambda *_: None)
    try:
        try:
            raise RuntimeError("tensor sizes differ\nexpected 192, got 256 token=private-fixture-secret")
        except RuntimeError:
            context.get("sakura.host.logging").error("合成失败")
            assert context.get("sakura.host.diagnostics").emit({"event": "tts.synthesis.failed", "severity": "error", "attributes": {"stage": "synthesis"}})
        assert delivered.wait(2)
    finally:
        context.close()
        bridge.close()
    records = [json.loads(line[len(CORE_BRIDGE_PREFIX):]) for line in stream.getvalue().splitlines() if line.startswith(CORE_BRIDGE_PREFIX)]
    assert len(records) == 2
    for record in records:
        fields = record["attributes"]
        assert "expected 192, got 256" in fields["diagnostic"]
        assert "\n" in fields["exception_stack"]
        assert "private-fixture-secret" not in json.dumps(record)


def test_process_failure_excerpt_excludes_previous_launch_and_normal_output(tmp_path):
    from app.plugin_sdk.sakura_process import process_failure_diagnostics
    path = tmp_path / "engine.log"
    old = b"ValueError: stale error from old launch\n"
    path.write_bytes(old + b"normal generated speech output\nTraceback (most recent call last):\n  File \"engine.py\", line 15, in load\nModuleNotFoundError: No module named torch\n")
    fields = process_failure_diagnostics(path, len(old))
    assert "No module named torch" in fields["diagnostic"]
    assert "engine.py" in fields["exception_stack"]
    assert "stale error" not in repr(fields)
    assert "normal generated speech" not in repr(fields)
    assert process_failure_diagnostics(path, path.stat().st_size) == {}


def test_plugin_stderr_drains_large_output_and_preserves_late_errors():
    import subprocess
    import sys
    from types import SimpleNamespace
    from app.plugins.runtime_v4 import _PluginProcess

    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    process = subprocess.Popen([sys.executable, "-c", "import sys; sys.stderr.write('progress\\n' * 20000); sys.stderr.write('RuntimeError: late native failure token=hidden-credential\\n')"], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    worker = _PluginProcess.__new__(_PluginProcess)
    worker._spec = SimpleNamespace(plugin_id="fixture.stderr")
    try:
        worker._drain_stderr(process)
        assert process.wait(timeout=5) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        bridge.close()
    assert "late native failure" in stream.getvalue().decode()
    assert "hidden-credential" not in stream.getvalue().decode()


def test_diagnostic_failure_cannot_replace_business_error():
    class UnusualError(Exception):
        @property
        def stage(self):
            raise RuntimeError("broken property")
    fields = exception_diagnostics(UnusualError("original failure"), reason_code="FAILED", stage="action")
    assert fields["diagnostic"] == "original failure"
    assert "incomplete" in fields["exception_stack"]


def test_rollback_failure_is_kept_alongside_the_original_failure():
    from app.core_host.plugin_settings import PluginSettingsError
    try:
        try:
            raise OSError("failed to replace plugin files")
        except OSError as primary:
            try:
                raise PermissionError("rollback directory is locked")
            except PermissionError as recovery:
                raise PluginSettingsError("PLUGIN_INSTALL_ROLLBACK_FAILED", "插件安装失败", recovery_error=recovery) from primary
    except PluginSettingsError as error:
        fields = exception_diagnostics(error, reason_code=error.code, stage="install")
    assert "failed to replace plugin files" in fields["diagnostic"]
    assert "rollback directory is locked" in fields["recovery_diagnostic"]
    assert " at " in fields["recovery_diagnostic"]


def test_failed_response_scrubs_request_credentials_from_the_entire_chain():
    from app.core_host.protocol import response, error_payload
    request = {"id": "settings-save", "name": "settings.provider_model.save", "payload": {"draft": {"providers": [{"credential": {"action": "replace", "value": "bare-private-credential"}}]}}}
    try:
        raise ValueError("invalid configuration bare-private-credential")
    except ValueError:
        result = response(request, generation_id="generation-test", generation_credential="generation-secret", error=error_payload("SAVE_FAILED", "保存失败"))
        success = response(request, generation_id="generation-test", generation_credential="generation-secret")
    assert "bare-private-credential" not in json.dumps(result)
    assert "invalid configuration" in result["error"]["details"]["diagnostics"]["diagnostic"]
    assert "diagnostics" not in json.dumps(success)
