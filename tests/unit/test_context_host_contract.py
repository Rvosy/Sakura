from __future__ import annotations

import pytest

from app.core_host.plugin_host_services import HostServiceError, _ContextHostService
from app.agent.context_orchestrator import ContextContributionError, ContextOrchestrator
from app.llm.prompts.types import ContextRequest
from app.plugins.host_services import HOST_CALLER


def _register(payload: object, **descriptor: object):
    service = _ContextHostService(
        lambda *_args: payload,
        lambda request: {"current_input": request.current_input},
        lambda _providers: None,
    )
    token = HOST_CALLER.set("example.actual-owner")
    try:
        registration = service.call("register", [
            {"providerId": "example.rules", **descriptor}, "cb_" + "1" * 32,
        ])
    finally:
        HOST_CALLER.reset(token)
    return service, registration, service.providers()[0]


def test_context_contract_preserves_content_scope_and_authenticated_owner() -> None:
    service, registration, provider = _register(
        [
            {"id": "rule", "content": "用简短句子回答。", "required": True},
            {"id": "reference", "content": "参考资料", "source": "host", "trust": "trusted"},
        ],
        scope="turn", failurePolicy="abort", pluginId="sakura.host",
    )
    capabilities = service.call("describe", [])
    assert capabilities["schemaVersion"] == 2
    assert "fragmentKinds" not in capabilities
    assert "turn" in capabilities["scopes"]
    assert "abort" in capabilities["failurePolicies"]
    assert provider.plugin_id == "example.actual-owner"
    assert provider.scope == "turn"
    assert provider.failure_policy == "abort"
    rule, reference = provider.build_context(ContextRequest(current_input="你好"))
    assert (rule.content, rule.required, rule.cache_scope) == ("用简短句子回答。", True, "turn")
    assert not hasattr(rule, "kind")
    assert not hasattr(reference, "trust")
    assert not reference.required
    assert reference.source == "plugin"
    assert service.call("unregister", [registration["registrationId"]]) == {"removed": True}
    assert service.providers() == []


@pytest.mark.parametrize("required", [False, True])
def test_context_host_transports_all_content_without_consumer_limits(required: bool) -> None:
    content = "  " + "x" * 9000 + "\n"
    _service, _registration, provider = _register([{"content": content, "required": required}] * 20)
    fragments = provider.build_context(ContextRequest())
    assert provider.scope == "step"
    assert provider.failure_policy == "skip"
    assert len(fragments) == 20
    assert all(
        fragment.required == required
        and fragment.cache_scope == "step" and fragment.content == content
        for fragment in fragments
    )


@pytest.mark.parametrize("fragment", [
    {"content": 1},
    {"content": "  "},
    {"required": 1, "content": "invalid boolean"},
])
def test_context_rejects_invalid_fragments(fragment: dict) -> None:
    _service, _registration, provider = _register([fragment])
    with pytest.raises(HostServiceError, match="CONTEXT_RESULT_INVALID"):
        provider.build_context(ContextRequest())


@pytest.mark.parametrize("kind", ["instruction", "data", "system", None])
def test_context_rejects_removed_classification_with_explicit_compatibility_error(kind) -> None:
    _service, _registration, provider = _register([{"kind": kind, "content": "旧插件内容"}])
    with pytest.raises(ContextContributionError) as raised:
        ContextOrchestrator().build_snapshot(ContextRequest(), providers=[provider])
    assert provider.failure_policy == "skip"
    assert isinstance(raised.value.__cause__, HostServiceError)
    assert raised.value.__cause__.code == "CONTEXT_SCHEMA_INCOMPATIBLE"
    assert raised.value.provider_id == provider.provider_id


@pytest.mark.parametrize("descriptor", [
    {"scope": "session"},
    {"failurePolicy": "retry"},
    {"order": float("nan")},
    {"order": True},
])
def test_context_rejects_unsupported_registration_policy(descriptor: dict) -> None:
    with pytest.raises(HostServiceError, match="CONTEXT_DESCRIPTOR_INVALID"):
        _register([], **descriptor)
