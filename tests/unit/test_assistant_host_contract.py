from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core_host.plugin_host_services import HostServiceError, _ContextHostService, _ToolsHostService
from app.plugin_sdk.sakura_tools import Tool, ToolRegistry
from app.plugins.sakura_plugin_sdk import _HostRegistrationProxy


def test_tool_catalog_does_not_rebind_an_old_turn_to_a_same_name_replacement(monkeypatch):
    calls = []
    old = Tool("fixture", "old", {}, handler=lambda _: calls.append("old") or "old")
    replacement = Tool("fixture", "new", {}, handler=lambda _: calls.append("new") or "new")
    registry = ToolRegistry([old])
    host = _ToolsHostService(registry, lambda *_args, **_kwargs: None)
    captured = host.call("catalog", [])[0]
    execute = registry.execute

    def replace_before_execution(name, arguments, *, expected):
        registry.register(replacement)
        return execute(name, arguments, expected=expected)

    monkeypatch.setattr(registry, "execute", replace_before_execution)
    assert host.call("execute", [captured["registrationId"], "fixture", {}])["content"] == "old"
    with pytest.raises(HostServiceError, match="TOOL_REGISTRATION_EXPIRED"):
        host.call("execute", [captured["registrationId"], "fixture", {}])
    assert calls == ["old"]


def test_context_catalog_does_not_rebind_an_old_turn_to_a_same_name_replacement():
    calls = []
    host = _ContextHostService(lambda handle, *_args: calls.append(handle) or [{"content": handle}],
                               lambda request: {}, lambda _providers: None)
    descriptor = {"providerId": "fixture.context", "scope": "turn"}
    old = host.call("register", [descriptor, "cb_" + "1" * 32])["registrationId"]
    host.call("unregister", [old])
    replacement = host.call("register", [descriptor, "cb_" + "2" * 32])["registrationId"]
    with pytest.raises(HostServiceError, match="CONTEXT_REGISTRATION_EXPIRED"):
        host.call("collect", [old, {}])
    assert not calls
    assert host.call("collect", [replacement, {}])[0]["content"] == "cb_" + "2" * 32


def test_advertised_long_tool_deadline_reaches_both_rpc_and_callback_without_replay():
    from app.plugins.plugin_runner_v4 import PluginRunner

    deadlines = []
    registry = ToolRegistry()
    def callback(_handle, _method, _arguments, *, timeout):
        deadlines.append(("callback", timeout))
        return "done"
    host = _ToolsHostService(registry, callback)
    host.call("register", [{"name": "long_tool", "description": "Long running tool", "timeoutSeconds": 90}, "cb_" + "3" * 32])
    row = host.call("catalog", [])[0]
    def remote(name, payload, *, timeout):
        deadlines.append(("rpc", timeout))
        assert name == "service.call"
        return host.call(payload["method"], payload["args"])
    runner = SimpleNamespace(_peer=SimpleNamespace(request=remote))
    context = SimpleNamespace(_remote_request=lambda name, payload: PluginRunner._call_remote_request(runner, name, payload))
    proxy = _HostRegistrationProxy(context, "sakura.host.tools", "tools.handler")
    assert proxy.execute(row["registrationId"], "long_tool", {}, timeout_seconds=row["timeoutSeconds"])["content"] == "done"
    assert deadlines == [("rpc", 92.0), ("callback", 90.0)]

    def fail_request(*args, **kwargs):
        deadlines.append(("failure", kwargs["timeout"]))
        raise TimeoutError("side effect acknowledgement was lost")
    runner._peer.request = fail_request
    with pytest.raises(TimeoutError):
        proxy.execute(row["registrationId"], "long_tool", {}, timeout_seconds=row["timeoutSeconds"])
    assert deadlines[-1] == ("failure", 92.0)
    assert len(deadlines) == 3
