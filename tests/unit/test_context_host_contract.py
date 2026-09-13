from __future__ import annotations

import pytest

from app.core_host.plugin_host_services import HostServiceError, _ContextHostService
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


def test_context_contract_preserves_purpose_scope_and_authenticated_owner() -> None:
    service, registration, provider = _register(
        [
            {"id": "rule", "kind": "instruction", "content": "用简短句子回答。", "required": True},
            {"id": "reference", "content": "参考资料", "source": "host", "trust": "trusted"},
        ],
        scope="turn", failurePolicy="abort", pluginId="sakura.host",
    )
    capabilities = service.call("describe", [])
    assert capabilities["schemaVersion"] == 1
    assert "instruction" in capabilities["fragmentKinds"]
    assert "turn" in capabilities["scopes"]
    assert "abort" in capabilities["failurePolicies"]
    assert provider.plugin_id == "example.actual-owner"
    assert provider.scope == "turn"
    assert provider.failure_policy == "abort"
    rule, reference = provider.build_context(ContextRequest(current_input="你好"))
    assert (rule.kind, rule.required, rule.cache_scope) == ("instruction", True, "turn")
    assert rule.trust == "trusted"
    assert (reference.kind, reference.required, reference.trust) == ("data", False, "untrusted")
    assert reference.source == "plugin"
    assert service.call("unregister", [registration["registrationId"]]) == {"removed": True}
    assert service.providers() == []


def test_legacy_context_defaults_and_bounded_data_remain_compatible() -> None:
    _service, _registration, provider = _register([{"content": "x" * 9000}] * 20)
    fragments = provider.build_context(ContextRequest())
    assert provider.scope == "step"
    assert provider.failure_policy == "skip"
    assert len(fragments) == 16
    assert all(
        fragment.kind == "data" and not fragment.required
        and fragment.cache_scope == "step" and fragment.content == "x" * 8192
        for fragment in fragments
    )


@pytest.mark.parametrize("fragment", [
    {"kind": "instruction", "content": "x" * 8193},
    {"required": True, "content": "x" * 8193},
    {"kind": "system", "content": "invalid purpose"},
    {"required": 1, "content": "invalid boolean"},
])
def test_context_rejects_invalid_or_incomplete_new_fragments(fragment: dict) -> None:
    _service, _registration, provider = _register([fragment])
    with pytest.raises(HostServiceError, match="CONTEXT_RESULT_INVALID"):
        provider.build_context(ContextRequest())


@pytest.mark.parametrize("tail", [
    {"kind": "instruction", "content": "不可静默丢弃的规则"},
    {"required": True, "content": "不可静默丢弃的必要资料"},
])
def test_context_rejects_result_when_fragment_limit_would_lose_a_rule(tail: dict) -> None:
    _service, _registration, provider = _register([{"content": "可选资料"}] * 16 + [tail])
    with pytest.raises(HostServiceError, match="CONTEXT_RESULT_INVALID"):
        provider.build_context(ContextRequest())


@pytest.mark.parametrize("descriptor", [
    {"scope": "session"},
    {"failurePolicy": "retry"},
    {"order": float("nan")},
    {"order": True},
])
def test_context_rejects_unsupported_registration_policy(descriptor: dict) -> None:
    with pytest.raises(HostServiceError, match="CONTEXT_DESCRIPTOR_INVALID"):
        _register([], **descriptor)
