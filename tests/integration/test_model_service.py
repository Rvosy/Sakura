"""Real provider and two independent consumers, using only ordinary Services."""
from __future__ import annotations

import json
import queue
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.core_host.plugin_application import PluginApplicationHost
from app.plugins.dependencies import PluginDependencyRoots
from app.plugin_sdk.sakura_tools import ToolRegistry
from app.storage.paths import StoragePaths
from app.storage.runtime_roots import RuntimeRoots


SERVICE = "sakura.model.openai_compatible"
REF = {"serviceKey": SERVICE, "profileId": "fixture", "modelId": "fixture"}
CONSUMER = '''
import threading
import time
from types import SimpleNamespace
import sakura_model_client as model_module
from sakura_model import ModelClient
from sakura_cancellation import OperationCancelled

class Consumer:
    def setup(self, context):
        self.context = context
        self.done = threading.Event()
        self.cancelled = threading.Event()
        self.client = None
        self.worker = None
        context.effect(self.close)
        context.provide(context.plugin_id, self, exports=("start", "wait", "cancel", "raw", "identity", "complete"))
    def identity(self):
        return self.context.bind("sakura.model.openai_compatible").identity
    def raw(self, method, args):
        return self.context.bind("sakura.model.openai_compatible").invoke(method, *args, timeout_seconds=2)
    def complete(self, ref, request):
        client = ModelClient(self.context, ref)
        try:
            return client.complete(request)
        finally:
            client.close()
    def start(self, ref, request, lose_ack=False, cleanup_fault=None):
        if request["messages"][0]["content"] == "large":
            request["messages"][0]["content"] += "x" * 1100000
        self.done.clear()
        self.cancelled.clear()
        self.client = ModelClient(self.context, ref)
        if cleanup_fault:
            original = self.client._service.invoke
            original_clock, offset = model_module.time, [0.]
            model_module.time = SimpleNamespace(monotonic=lambda: time.monotonic() + offset[0])
            release_failed = False
            self.cleanup_calls = []
            def invoke(method, *args, **kwargs):
                nonlocal release_failed
                if method == "begin":
                    self.operation_id = args[0]["operationId"]
                if method in {"cancel", "release"}:
                    self.cleanup_calls.append(method)
                if method == "release" and cleanup_fault == "release_not_sent" and not release_failed:
                    release_failed = True
                    offset[0] += kwargs["timeout_seconds"] + 1
                    raise TimeoutError("release did not reach provider")
                result = original(method, *args, **kwargs)
                if method == "cancel":
                    offset[0] += kwargs["timeout_seconds"] + 1
                    raise TimeoutError("cancel acknowledgement exhausted cleanup budget")
                return result
            self.client._service.invoke = invoke
        if lose_ack:
            original = self.client._service.invoke
            def invoke(method, *args, **kwargs):
                result = original(method, *args, **kwargs)
                if method == "begin":
                    raise TimeoutError("acknowledgement lost")
                return result
            self.client._service.invoke = invoke
        def check():
            if self.cancelled.is_set():
                raise OperationCancelled()
        def run():
            try:
                result = self.client.complete(request, cancel_checker=check)
                content = result["message"].get("content") or ""
                self.result = {"state": "completed", "length": len(content), "content": content[:100]}
            except BaseException as error:
                self.result = {"state": "cancelled" if isinstance(error, OperationCancelled) else "failed", "code": getattr(error, "code", type(error).__name__)}
            finally:
                if cleanup_fault:
                    self.result["pendingBeforeClose"] = len(self.client._operations)
                self.client.close()
                if cleanup_fault:
                    self.result.update(operationId=self.operation_id, cleanupCalls=self.cleanup_calls,
                                       pendingAfterClose=len(self.client._operations))
                    model_module.time = original_clock
                self.done.set()
        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()
        return True
    def wait(self):
        if not self.done.wait(3):
            return {"state": "running"}
        return self.result
    def cancel(self):
        self.cancelled.set()
        return True
    def close(self):
        self.cancelled.set()
        if self.client:
            self.client.close()
        if self.worker:
            self.worker.join(1)
'''


@pytest.fixture
def model_process(tmp_path, monkeypatch, assistant_dependencies):
    distribution, user = tmp_path / "distribution", tmp_path / "user"
    user.mkdir()
    root = Path(__file__).resolve().parents[2]
    shutil.copytree(root / "plugins/builtin/sakura_model_openai_compatible", distribution / "plugins/builtin/model", ignore=shutil.ignore_patterns("__pycache__"))
    # Observe the actual provider's retained jobs without adding a production API.
    provider = distribution / "plugins/builtin/model"
    with (provider / "plugin.py").open("a", encoding="utf-8") as source:
        source.write('''

class FixtureModelPlugin(ModelPlugin):
    def setup(self, context):
        super().setup(context)
        context.provide("fixture.model.inspect", self, exports=("wait_disposed",))
    def wait_disposed(self, operation_id):
        with self.changed:
            self.changed.wait_for(lambda: not any(job.operation_id == operation_id for job in self.jobs.values()), timeout=3)
            return [{"owner": job.owner[0], "operationId": job.operation_id, "state": job.state} for job in self.jobs.values()]
''')
    manifest = provider / "plugin.yaml"
    manifest.write_text(manifest.read_text(encoding="utf-8").replace("entry: plugin:ModelPlugin", "entry: plugin:FixtureModelPlugin")
                        .replace("provides:\n", "provides:\n  - fixture.model.inspect\n"), encoding="utf-8")
    for identity in ("fixture.model.a", "fixture.model.b"):
        plugin = distribution / "plugins/builtin" / identity
        plugin.mkdir()
        (plugin / "plugin.yaml").write_text(f"api: 4\nid: {identity}\nname: Consumer\nversion: 1.0.0\nentry: plugin:Consumer\nenabled: true\nprovides: [{identity}]\nrequires: [sakura.host.artifacts]\n", encoding="utf-8")
        (plugin / "plugin.py").write_text(CONSUMER, encoding="utf-8")
    original = PluginDependencyRoots.verified_root
    monkeypatch.setattr(PluginDependencyRoots, "verified_root", lambda self, plugin_id, *args, **kwargs: assistant_dependencies if plugin_id == SERVICE else original(self, plugin_id, *args, **kwargs))
    requests, gate = queue.Queue(), threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.put({"listModels": True})
            models = [{"id": f"discovered-{index:04d}-" + "x" * 48} for index in range(700)] if self.path.startswith("/large/") else [{"id": "fixture"}, {"id": "discovered"}]
            payload = json.dumps({"data": models}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.put(body)
            text = body["messages"][0]["content"]
            if text == "stream-tools" and body.get("stream"):
                deltas = [
                    {"role": "assistant", "reasoning_content": "先检查", "opaque_continuation": {"signature": "opaque-1"}},
                    {"reasoning_content": "工具参数。", "opaque_continuation": {"signature": "opaque-1"}},
                    {"tool_calls": [{"index": 0, "id": "call-stream-1", "type": "function",
                                     "function": {"name": "lookup", "arguments": '{"city":'},
                                     "extra_content": {"google": {"thought_signature": "tool-signature"}}}]},
                    {"tool_calls": [{"index": 0, "function": {"arguments": '"杭州"}'}}]},
                    {"reasoning_content": None},
                ]
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for index, delta in enumerate(deltas):
                    chunk = {"id": "stream", "object": "chat.completion.chunk", "created": 0, "model": "fixture",
                             "choices": [{"index": 0, "delta": delta, "finish_reason": "tool_calls" if index == len(deltas) - 1 else None}]}
                    self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                return
            if text == "hold":
                gate.wait()
            response = "r" * 1_100_000 if isinstance(text, str) and text.startswith("large") else "OK"
            status = 200
            data = {"choices": [{"message": {"content": response}}]}
            if text == "reject-secret":
                status = 401
                data = {"error": {"message": "credential rejected: " + self.headers.get("Authorization", "").removeprefix("Bearer "),
                                  "code": "invalid_api_key"},
                        "choices": [{"message": {"content": "UNRELATED_PRIVATE_RESPONSE_BODY"}}]}
            payload = json.dumps(data).encode()
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    app = PluginApplicationHost(RuntimeRoots(distribution, user), "model-test", ToolRegistry())
    config = StoragePaths(user).plugin_data_for(SERVICE) / "config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"profiles": [{"profileId": "fixture", "base_url": f"http://127.0.0.1:{server.server_port}/v1", "api_key": "", "models": ["fixture"], "timeout_seconds": 5}]}), encoding="utf-8")
    try:
        app.start()
        assert app.wait_until_loaded(timeout=5)
        assert all(item["state"] == "active" for item in app.public_snapshot()["plugins"]), app.public_snapshot()
        yield app, requests, gate, config
    finally:
        gate.set()
        app.close()
        server.shutdown()
        server.server_close()
        worker.join(3)


def request(text):
    return {"messages": [{"role": "user", "content": text}]}


def test_large_input_and_output_use_artifacts_and_both_are_reclaimed(model_process):
    app, requests, _gate, _config = model_process
    text = "large" + "x" * 1_100_000
    app.call_service("fixture.model.a", "start", REF, request("large"))
    assert requests.get(timeout=3)["messages"][0]["content"] == text
    result = app.call_service("fixture.model.a", "wait")
    assert result["state"] == "completed" and result["length"] == 1_100_000, result
    assert app._host_services.artifact_count == 0


def test_one_consumer_cancel_and_exit_leave_shared_provider_and_other_consumer_usable(model_process):
    app, requests, gate, _config = model_process
    identity = app.service_identity(SERVICE)
    app.call_service("fixture.model.a", "start", REF, request("hold"))
    requests.get(timeout=3)
    app.call_service("fixture.model.a", "cancel")
    assert app.call_service("fixture.model.a", "wait")["state"] == "cancelled"
    app.call_service("fixture.model.b", "start", REF, request("hello"))
    assert app.call_service("fixture.model.b", "wait")["content"] == "OK"
    requests.get(timeout=3)
    app.call_service("fixture.model.a", "start", REF, request("hold"))
    requests.get(timeout=3)
    app.set_plugin_enabled("fixture.model.a", False)
    gate.set()
    assert app.service_identity(SERVICE) == identity
    app.call_service("fixture.model.b", "start", REF, request("again"))
    assert app.call_service("fixture.model.b", "wait")["content"] == "OK"


def test_unknown_begin_ack_is_not_replayed_and_late_begin_is_cancelled(model_process):
    app, requests, gate, _config = model_process
    app.call_service("fixture.model.a", "start", REF, request("hold"), True)
    assert app.call_service("fixture.model.a", "wait")["state"] == "failed"
    # A lost ACK can race HTTP dispatch; zero or one request is valid, never two.
    assert requests.qsize() <= 1
    app.call_service("fixture.model.a", "raw", "cancel", ["late"])
    with pytest.raises(Exception):
        app.call_service("fixture.model.a", "raw", "begin", [{"operationId": "late", "profileId": "fixture", "modelId": "fixture", "request": request("should-not-run")}])
    gate.set()
    app.call_service("fixture.model.b", "start", REF, request("works"))
    assert app.call_service("fixture.model.b", "wait")["content"] == "OK"


def test_operations_are_authenticated_by_consumer_scope(model_process):
    app, requests, gate, _config = model_process
    descriptor = {"operationId": "private-job", "profileId": "fixture", "modelId": "fixture", "request": request("hold")}
    app.call_service("fixture.model.a", "raw", "begin", [descriptor])
    requests.get(timeout=3)
    with pytest.raises(Exception):
        app.call_service("fixture.model.b", "raw", "poll", ["private-job"])
    app.call_service("fixture.model.b", "raw", "cancel", ["private-job"])
    assert app.call_service("fixture.model.a", "raw", "poll", ["private-job", 0, 0])["state"] == "running"
    app.call_service("fixture.model.a", "raw", "cancel", ["private-job"])
    app.call_service("fixture.model.a", "raw", "release", ["private-job"])
    gate.set()


def test_json_escaped_credentials_are_removed_before_model_failure_crosses_processes(model_process):
    from app.core.diagnostics import exception_diagnostics
    from app.plugins.runtime_v4 import PluginRuntimeError

    app, requests, _gate, config = model_process
    # This has no secret-shaped prefix and the provider JSON-escapes it. Raw
    # response.replace(key) cannot remove the returned representation.
    secret = 'OpaqueCredential-Q7-"slash\\tail'
    saved = json.loads(config.read_text(encoding="utf-8"))
    saved["profiles"][0]["api_key"] = secret
    config.write_text(json.dumps(saved), encoding="utf-8")
    app.reload_plugin(SERVICE)
    with pytest.raises(PluginRuntimeError) as caught:
        app.call_service("fixture.model.a", "complete", REF, request("reject-secret"))
    assert requests.get(timeout=3)["messages"][0]["content"] == "reject-secret"
    diagnostic = exception_diagnostics(caught.value, reason_code="MODEL_FAILED", stage="model")
    observed = repr((caught.value, caught.value.__cause__.diagnostics, diagnostic))
    assert secret not in observed
    assert json.dumps(secret)[1:-1] not in observed
    assert "OpaqueCredential-Q7" not in observed
    assert "UNRELATED_PRIVATE_RESPONSE_BODY" not in observed
    assert "credential rejected" in diagnostic["diagnostic"]
    assert "invalid_api_key" in diagnostic["diagnostic"]
    assert "[REDACTED]" in diagnostic["exception_stack"]


def test_saved_profiles_do_not_change_effective_generation_until_reload(model_process):
    app, _requests, _gate, config = model_process
    saved = json.loads(config.read_text(encoding="utf-8"))
    saved["profiles"][0]["models"] = ["replacement"]
    config.write_text(json.dumps(saved), encoding="utf-8")
    app.call_service("fixture.model.a", "start", REF, request("old-active"))
    assert app.call_service("fixture.model.a", "wait")["content"] == "OK"
    before = app.service_identity(SERVICE)
    app.reload_plugin(SERVICE)
    assert app.service_identity(SERVICE) != before
    with pytest.raises(Exception):
        app.call_service("fixture.model.a", "start", REF, request("invalid-old-model"))


@pytest.mark.parametrize("fault,count", [("cancel_ack_timeout", 33), ("release_not_sent", 1)])
def test_cleanup_releases_jobs_without_exhausting_owner_quota_or_cancelling_others(model_process, fault, count):
    app, requests, gate, config = model_process
    saved = json.loads(config.read_text(encoding="utf-8"))
    saved["profiles"][0]["timeout_seconds"] = 60
    config.write_text(json.dumps(saved), encoding="utf-8")
    app.reload_plugin(SERVICE)
    provider = app.service_identity(SERVICE)
    app.call_service("fixture.model.b", "start", REF, request("hold"))
    assert requests.get(timeout=3)["messages"][0]["content"] == "hold"
    for _ in range(count):
        app.call_service("fixture.model.a", "start", REF, request("hold"), False, fault)
        assert requests.get(timeout=3)["messages"][0]["content"] == "hold"
        app.call_service("fixture.model.a", "cancel")
        result = app.call_service("fixture.model.a", "wait")
        assert result["state"] == "cancelled", result
        jobs = app._manager.call_service("fixture.model.inspect", "wait_disposed", result["operationId"], timeout=5)
        assert all(job["owner"] != "fixture.model.a" for job in jobs), jobs
        assert [(job["owner"], job["state"]) for job in jobs] == [("fixture.model.b", "running")]
        assert result["cleanupCalls"] == (["release", "release"] if fault == "release_not_sent" else ["release"])
        assert result["pendingBeforeClose"] == int(fault == "release_not_sent")
        assert result["pendingAfterClose"] == 0
    assert app.service_identity(SERVICE) == provider
    gate.set()
    assert app.call_service("fixture.model.b", "wait")["state"] == "completed"
    assert app.call_service("fixture.model.a", "complete", REF, request("after-cancellations"))["message"]["content"] == "OK"
    assert app._host_services.artifact_count == 0


def test_streaming_message_and_tool_continuation_are_returned_on_the_next_request(model_process):
    app, requests, _gate, _config = model_process
    first = {**request("stream-tools"), "stream": True,
             "tools": [{"name": "lookup", "description": "查天气", "parameters": {"type": "object"}}]}
    response = app.call_service("fixture.model.a", "complete", REF, first)
    assert requests.get(timeout=3)["stream"] is True
    message = response["message"]
    assert response["finishReason"] == "tool_calls"
    assert message["providerData"] == {"reasoning_content": "先检查工具参数。", "opaque_continuation": {"signature": "opaque-1"}}
    call = message["toolCalls"][0]
    assert call == {"id": "call-stream-1", "name": "lookup", "arguments": '{"city":"杭州"}',
                    "providerData": {"extra_content": {"google": {"thought_signature": "tool-signature"}}}}
    app.call_service("fixture.model.a", "complete", REF, {"messages": [*first["messages"], message,
                     {"role": "tool", "toolCallId": call["id"], "content": "晴"}]})
    next_request = requests.get(timeout=3)
    assistant, tool_result = next_request["messages"][1:]
    assert assistant["reasoning_content"] == "先检查工具参数。"
    assert assistant["opaque_continuation"] == {"signature": "opaque-1"}
    assert assistant["tool_calls"] == [{"id": "call-stream-1", "type": "function", "function": {"name": "lookup", "arguments": '{"city":"杭州"}'},
                                         "extra_content": {"google": {"thought_signature": "tool-signature"}}}]
    assert tool_result["tool_call_id"] == "call-stream-1"


def test_provider_apply_reclaims_consumer_started_after_idle_preflight(model_process, monkeypatch):
    app, requests, gate, config = model_process
    provider = next(p for p in app.settings_snapshot()["plugins"] if p["pluginId"] == SERVICE)
    section = next(s for s in provider["sections"] if s["sectionId"] == "connections")
    draft = {"connections": section["values"]["connections"]}
    draft["connections"][0]["models"] = ["replacement"]
    previous = app.service_identity(SERVICE)
    preflight_done, reload_allowed = threading.Event(), threading.Event()
    results, errors = [], []
    apply_result = app._apply_settings_result

    def delayed_reload(plugin_id, result):
        assert result["applicationState"] == "restart_required"
        preflight_done.set()
        assert reload_allowed.wait(3)
        return apply_result(plugin_id, result)

    monkeypatch.setattr(app, "_apply_settings_result", delayed_reload)

    def apply():
        try:
            results.append(app.settings_save(SERVICE, "connections", draft))
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=apply)
    worker.start()
    try:
        assert preflight_done.wait(3), errors
        # This background consumer enters the old provider after the action's
        # idle check. It must fail when that instance exits, never replay on new.
        app.call_service("fixture.model.a", "start", REF, request("hold"))
        assert requests.get(timeout=3)["model"] == "fixture"
    finally:
        reload_allowed.set()
        worker.join(5)
    assert not worker.is_alive()
    assert errors == []
    assert results[0]["applicationState"] == "applied"
    assert app.service_identity(SERVICE) != previous
    result = app.call_service("fixture.model.a", "wait")
    assert result["state"] in {"cancelled", "failed"}, result
    assert "content" not in result
    gate.set()
    app.call_service("fixture.model.b", "start", {**REF, "modelId": "replacement"}, request("new-instance"))
    assert app.call_service("fixture.model.b", "wait")["content"] == "OK"
    assert requests.get(timeout=3)["model"] == "replacement"
    assert requests.empty()  # No replay of the cancelled request.
    assert app._host_services.artifact_count == 0


@pytest.mark.parametrize("action", ["test_connection", "list_models"])
def test_provider_settings_actions_use_the_real_bound_job_lifecycle(model_process, action):
    app, requests, _gate, config = model_process
    before = config.read_bytes()
    app.settings_action(SERVICE, "connections", "probe", {"probeRequest": {"operation": action,
        "requestId": "probe-test", "profileId": "fixture", "modelId": "fixture"}})
    requests.get(timeout=3)
    deadline = time.monotonic() + 3
    while True:
        row = next(item for item in app.settings_snapshot()["plugins"] if item["pluginId"] == SERVICE)
        status = next(section for section in row["sections"] if section["sectionId"] == "connections")["values"]["probeResult"]
        if status["state"] != "running":
            break
        assert time.monotonic() < deadline
    assert status["state"] == "completed", status
    assert config.read_bytes() == before
    if action == "list_models":
        assert "discovered" in [item["modelId"] for item in status["models"]]
        assert "discovered" not in [item["modelId"] for item in app.call_service(SERVICE, "catalog")[0]["models"]]


def test_provider_settings_discovers_large_model_list_and_reclaims_response_artifact(model_process):
    app, requests, _gate, config = model_process
    before = config.read_bytes()
    address = json.loads(before)["profiles"][0]["base_url"].replace("/v1", "/large/v1")
    app.settings_action(SERVICE, "connections", "probe", {"probeRequest": {
        "operation": "list_models", "requestId": "large-discovery", "profileId": "fixture", "base_url": address}})
    assert requests.get(timeout=3) == {"listModels": True}
    deadline = time.monotonic() + 3
    while True:
        row = next(item for item in app.settings_snapshot()["plugins"] if item["pluginId"] == SERVICE)
        status = next(section for section in row["sections"] if section["sectionId"] == "connections")["values"]["probeResult"]
        if status["state"] != "running":
            break
        assert time.monotonic() < deadline
    assert status["state"] == "completed", status
    assert status["requestId"] == "large-discovery"
    assert len(status["models"]) == 700
    assert status["models"][0]["modelId"] == "discovered-0000-" + "x" * 48
    assert app._host_services.artifact_count == 0
    assert config.read_bytes() == before
