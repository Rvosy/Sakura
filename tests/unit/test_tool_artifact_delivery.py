from __future__ import annotations

from pathlib import Path
import threading

import pytest

from app.core_host.plugin_artifacts import MAX_ARTIFACTS_PER_PLUGIN, PluginArtifactStore
from app.core_host.plugin_host_services import HostServiceError, _ArtifactsHostService, _ToolsHostService
from app.plugin_sdk.sakura_tools import ToolRegistry
from app.plugins.models import PluginSpec
from app.plugins.runtime_v4 import PluginRuntimeManager, _ServiceBinding
from app.plugins.sakura_plugin_sdk import PluginApiError
from app.storage.runtime_roots import RuntimeRoots


SOURCE, RECEIVER = "fixture.images", "fixture.assistant"


class Process:
    def __init__(self, scope_id):
        self.scope_id = scope_id
        self.closed = False

    def close(self, *, deadline):
        self.closed = True

    def wait_for_cleanup(self):
        assert self.closed

    def emit(self, name, payload):
        return None


class HostAdapter:
    def __init__(self, service):
        self.service = service

    def __getattr__(self, method):
        if method == "revoke_scope":
            return getattr(self.service, method, lambda _plugin_id: None)
        return lambda *args: self.service.call(method, args)


class Runtime:
    def __init__(self, tmp_path, callback):
        specs = [PluginSpec(entry="fixture:Plugin", plugin_id=plugin_id, name=plugin_id,
                            provides=(service,), requires=("sakura.host.artifacts", "sakura.host.tools"))
                 for plugin_id, service in [(SOURCE, "fixture.images"), (RECEIVER, "sakura.assistant")]]
        self.manager = PluginRuntimeManager(RuntimeRoots(tmp_path / "distribution", tmp_path), "artifact-delivery", specs)
        self.store = PluginArtifactStore(tmp_path, "artifact-delivery")
        self.artifacts = _ArtifactsHostService(self.store, self.manager.commit_plugin_scope)
        self.tools = _ToolsHostService(ToolRegistry(), callback, self.artifacts.consume_tool_result)
        self.manager.install_host_service("sakura.host.artifacts", HostAdapter(self.artifacts),
                                          exports=("resolve", "release_received", "allocate", "commit", "release"))
        self.manager.install_host_service("sakura.host.tools", HostAdapter(self.tools),
                                          exports=("register", "unregister", "catalog", "execute"))
        self.source = self.activate(SOURCE, "source-scope")
        self.receiver = self.activate(RECEIVER, "old-scope")
        self.call(SOURCE, self.source, "sakura.host.tools", "register",
                  {"name": "image", "description": "Fixture image", "parameters": {}}, "cb_" + "1" * 32)
        self.tool = self.call(RECEIVER, self.receiver, "sakura.host.tools", "catalog")[0]

    def activate(self, plugin_id, scope):
        process = Process(scope)
        record = self.manager._records[plugin_id]
        record.process, record.state = process, "active"
        self.manager._activation_order.append(plugin_id)
        self.manager._services[record.spec.provides[0]] = _ServiceBinding(plugin_id, frozenset(), process)
        return process

    def call(self, plugin_id, process, service, method, *args):
        return self.manager._handle_plugin_request(plugin_id, "service.call", {
            "serviceKey": service, "method": method, "args": list(args),
        }, calling_process=process)

    def image(self):
        allocation = self.store.allocate(SOURCE, {"mediaType": "image/png", "suffix": ".png"})
        path = Path(allocation["path"])
        path.write_bytes(b"fixture-image")
        return self.store.commit(SOURCE, allocation["artifactId"]), path

    def execute(self, process=None):
        return self.call(RECEIVER, process or self.receiver, "sakura.host.tools", "execute",
                         self.tool["registrationId"], "image", {})

    def stop(self, plugin_id, process):
        service = self.manager._records[plugin_id].spec.provides[0]
        self.manager.abort_bound_service(service, {"providerId": plugin_id, "scopeId": process.scope_id}, reason="FIXTURE_STOP")


def test_delivered_image_outlives_source_and_is_reclaimed_with_receiver(tmp_path):
    runtime = Runtime(tmp_path, lambda *_args, **_kwargs: {"content": "image", "artifact": descriptor})
    descriptor, path = runtime.image()
    result = runtime.execute()
    assert result["success"]
    assert runtime.store.resolve_committed_by_id(descriptor["artifactId"]).plugin_id == RECEIVER
    resolved = runtime.call(RECEIVER, runtime.receiver, "sakura.host.artifacts", "resolve", descriptor["artifactId"])
    assert Path(resolved["path"]).read_bytes() == b"fixture-image"
    runtime.stop(SOURCE, runtime.source)
    assert path.is_file(), "source shutdown cannot remove bytes handed to a live receiver"
    runtime.stop(RECEIVER, runtime.receiver)
    assert not path.exists()
    assert runtime.store.count == 0
    assert not runtime.artifacts._received


@pytest.mark.parametrize("replace_receiver", [False, True])
def test_late_tool_return_releases_old_artifact_without_granting_replacement(tmp_path, replace_receiver):
    entered, finish = threading.Event(), threading.Event()
    calls = 0
    descriptors = []

    def callback(*_args, **_kwargs):
        nonlocal calls
        index = calls
        calls += 1
        if index == 0:
            entered.set()
            assert finish.wait(5)
        return {"content": "image", "artifact": descriptors[index]}

    runtime = Runtime(tmp_path, callback)
    old_descriptor, old_path = runtime.image()
    descriptors.append(old_descriptor)
    results, errors = [], []

    def execute_old():
        try:
            results.append(runtime.execute())
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=execute_old)
    worker.start()
    try:
        assert entered.wait(3)
        runtime.stop(RECEIVER, runtime.receiver)
        assert old_path.exists(), "source still owns a callback result not yet delivered"
        if replace_receiver:
            new = runtime.activate(RECEIVER, "new-scope")
            new_descriptor, new_path = runtime.image()
            descriptors.append(new_descriptor)
            assert runtime.execute(new)["success"]
            assert new_path.exists()
        finish.set()
        worker.join(5)
        assert not worker.is_alive() and not errors
        assert len(results) == 1 and not results[0]["success"]
        assert not old_path.exists()
        if replace_receiver:
            assert runtime.artifacts._received == {new_descriptor["artifactId"]: (RECEIVER, "new-scope")}
            resolved = runtime.call(RECEIVER, new, "sakura.host.artifacts", "resolve", new_descriptor["artifactId"])
            assert Path(resolved["path"]).read_bytes() == b"fixture-image"
            with pytest.raises(PluginApiError, match="ARTIFACT_NOT_FOUND"):
                runtime.call(RECEIVER, new, "sakura.host.artifacts", "resolve", old_descriptor["artifactId"])
        else:
            assert not runtime.artifacts._received
            assert runtime.store.count == 0
    finally:
        finish.set()
        worker.join(5)
        runtime.manager.close()


def test_artifact_grant_cannot_be_used_with_same_id_and_different_scope(tmp_path):
    from app.plugins.host_services import HOST_CALLER, HOST_CALLER_SCOPE

    runtime = Runtime(tmp_path, lambda *_args, **_kwargs: {"content": "image", "artifact": descriptor})
    descriptor, _path = runtime.image()
    assert runtime.execute()["success"]
    caller_token = HOST_CALLER.set(RECEIVER)
    scope_token = HOST_CALLER_SCOPE.set("different-scope")
    try:
        with pytest.raises(HostServiceError, match="ARTIFACT_NOT_FOUND"):
            runtime.artifacts.call("resolve", [descriptor["artifactId"]])
        with pytest.raises(HostServiceError, match="ARTIFACT_NOT_FOUND"):
            runtime.artifacts.call("release_received", [descriptor["artifactId"]])
        assert runtime.store.count == 1
    finally:
        HOST_CALLER_SCOPE.reset(scope_token)
        HOST_CALLER.reset(caller_token)
        runtime.manager.close()


def test_receiver_capacity_failure_releases_only_the_undeliverable_source_file(tmp_path):
    runtime = Runtime(tmp_path, lambda *_args, **_kwargs: {"content": "image", "artifact": descriptor})
    for _ in range(MAX_ARTIFACTS_PER_PLUGIN):
        runtime.store.allocate(RECEIVER, {"mediaType": "application/json"})
    descriptor, path = runtime.image()
    try:
        assert not runtime.execute()["success"]
        assert not path.exists()
        assert runtime.store.count == MAX_ARTIFACTS_PER_PLUGIN
        assert not runtime.artifacts._received
    finally:
        runtime.manager.close()


def test_tool_cannot_redeliver_or_delete_an_artifact_already_owned_by_receiver(tmp_path):
    runtime = Runtime(tmp_path, lambda *_args, **_kwargs: {"content": "image", "artifact": descriptor})
    descriptor, path = runtime.image()
    try:
        assert runtime.execute()["success"]
        assert not runtime.execute()["success"]
        assert path.exists()
        assert runtime.store.count == 1
        runtime.call(RECEIVER, runtime.receiver, "sakura.host.artifacts", "release_received", descriptor["artifactId"])
        assert runtime.store.count == 0 and not runtime.artifacts._received
    finally:
        runtime.manager.close()
