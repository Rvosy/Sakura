from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.agent.tools import ToolRegistry
from app.core_host.plugin_application import PluginApplicationHost
from app.llm.prompts.types import ContextRequest
from app.plugins.installer import LocalPluginInstaller
from app.plugins.inventory import PluginDesiredStateStore
from app.plugins.models import ContextProviderContribution
from app.storage.runtime_roots import RuntimeRoots
from plugins.optional.context_rules.plugin import ContextRulesPlugin, DEFAULT_RULES
from tools.release.package_optional_plugin import build


SOURCE_PLUGIN_ROOT = Path(__file__).resolve().parents[2] / "plugins/optional/context_rules"
PLUGIN_ID = "context_rules"


class _ContextConsumer:
    def __init__(self) -> None:
        self.providers: list[ContextProviderContribution] = []

    def set_context_providers(self, providers: list[ContextProviderContribution]) -> None:
        self.providers = list(providers)


def test_optional_rules_install_configure_and_disable_through_v4(tmp_path: Path) -> None:
    package = tmp_path / "context_rules.sakplugin.zip"
    build(SOURCE_PLUGIN_ROOT, package)
    distribution = tmp_path / "distribution"
    user = tmp_path / "user"
    (distribution / "plugins/builtin").mkdir(parents=True)
    user.mkdir()
    roots = RuntimeRoots(distribution, user)
    installed = LocalPluginInstaller(roots).install(package.resolve(), "zip")
    PluginDesiredStateStore(user).set(installed.plugin_id, True)
    registry = ToolRegistry()
    application = PluginApplicationHost(roots, "context-rules-test", registry)
    consumer = _ContextConsumer()
    try:
        application.start()
        record = next(
            item for item in application.public_snapshot()["plugins"]
            if item["pluginId"] == PLUGIN_ID
        )
        assert record["state"] == "active", record
        assert record["source"] == "user"
        application.application.bind_runtime(registry, consumer)
        provider, = consumer.providers
        assert provider.provider_id == PLUGIN_ID
        assert provider.scope == "turn"
        assert provider.failure_policy == "abort"
        instruction, = provider.build_context(ContextRequest(current_turn_id="initial"))
        assert instruction.content == DEFAULT_RULES
        assert instruction.kind == "instruction"
        assert instruction.required is True

        updated_rules = "用日语回答，先纠正明显的语法错误。"
        reference = "今天练习介绍周末做过的事情。"
        assert application.settings_save(
            PLUGIN_ID, PLUGIN_ID, {"rules": updated_rules, "reference": reference},
        ) == {"saved": True, "applicationState": "applied", "reasonCode": "READY"}
        instruction, data = provider.build_context(ContextRequest(current_turn_id="updated"))
        assert instruction.content == updated_rules
        assert instruction.kind == "instruction"
        assert instruction.required is True
        assert data.content == reference
        assert data.kind == "data"
        assert data.required is False

        application.set_enabled(installed.install_id, False)
        assert consumer.providers == []
        application.set_enabled(installed.install_id, True)
        reloaded, = consumer.providers
        instruction, data = reloaded.build_context(ContextRequest(current_turn_id="reloaded"))
        assert instruction.content == updated_rules
        assert data.content == reference

        application.settings_save(PLUGIN_ID, PLUGIN_ID, {"rules": ""})
        data, = reloaded.build_context(ContextRequest(current_turn_id="reference-only"))
        assert data.kind == "data"
        assert data.content == reference
        application.settings_save(PLUGIN_ID, PLUGIN_ID, {"reference": ""})
        assert reloaded.build_context(ContextRequest(current_turn_id="cleared")) == ()
    finally:
        application.close()


@pytest.mark.parametrize("capabilities", [
    None,
    {"schemaVersion": 1, "fragmentKinds": ["data"], "scopes": ["turn"], "failurePolicies": ["abort"]},
    {"schemaVersion": 1, "fragmentKinds": ["data", "instruction"], "scopes": ["step"], "failurePolicies": ["abort"]},
    {"schemaVersion": 1, "fragmentKinds": ["data", "instruction"], "scopes": ["turn"], "failurePolicies": ["skip"]},
])
def test_rules_refuse_hosts_without_required_context_capabilities(capabilities: object) -> None:
    register = Mock()
    service = SimpleNamespace(register=register)
    if capabilities is not None:
        service.describe = lambda: capabilities
    context = SimpleNamespace(get=lambda _key: service)
    with pytest.raises(RuntimeError, match="CONTEXT_RULES_HOST_UNSUPPORTED"):
        ContextRulesPlugin().setup(context)
    register.assert_not_called()
