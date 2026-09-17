from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.plugin_sdk.sakura_tools import ToolRegistry
from app.core_host.plugin_application import PluginApplicationHost
from app.plugin_sdk.sakura_context import ContextRequest
from app.plugins.installer import LocalPluginInstaller
from app.plugins.inventory import PluginDesiredStateStore
from app.storage.paths import StoragePaths
from app.storage.runtime_roots import RuntimeRoots
from plugins.optional.context_rules.plugin import ContextRulesPlugin, DEFAULT_CONTENT, _config_values
from tools.release.package_optional_plugin import build


SOURCE_PLUGIN_ROOT = Path(__file__).resolve().parents[2] / "plugins/optional/context_rules"
PLUGIN_ID = "context_rules"


@pytest.mark.parametrize("legacy_config", [None, {"rules": " 原有要求\n", "reference": "\n原有文本 "}])
def test_optional_rules_install_configure_and_disable_through_v4(
    tmp_path: Path, legacy_config: dict[str, str] | None,
) -> None:
    package = tmp_path / "context_rules.sakplugin.zip"
    build(SOURCE_PLUGIN_ROOT, package)
    distribution = tmp_path / "distribution"
    user = tmp_path / "user"
    (distribution / "plugins/builtin").mkdir(parents=True)
    user.mkdir()
    roots = RuntimeRoots(distribution, user)
    installed = LocalPluginInstaller(roots).install(package.resolve(), "zip")
    config_path = StoragePaths(user).plugin_data_for(PLUGIN_ID) / "config.json"
    if legacy_config is not None:
        config_path.parent.mkdir(parents=True)
        config_path.write_text(json.dumps(legacy_config, ensure_ascii=False), encoding="utf-8")
    PluginDesiredStateStore(user).set(installed.plugin_id, True)
    registry = ToolRegistry()
    application = PluginApplicationHost(roots, "context-rules-test", registry)
    def catalog():
        return application.call_service("sakura.host.context", "catalog")

    def collect(provider, turn):
        return application.call_service("sakura.host.context", "collect", provider["registrationId"],
                                        asdict(ContextRequest(current_turn_id=turn)))
    try:
        application.start()
        record = next(
            item for item in application.public_snapshot()["plugins"]
            if item["pluginId"] == PLUGIN_ID
        )
        assert record["state"] == "active", record
        assert record["source"] == "user"
        provider, = catalog()
        assert provider["providerId"] == PLUGIN_ID
        assert provider["scope"] == "turn"
        assert provider["failurePolicy"] == "abort"
        contribution, = collect(provider, "initial")
        expected = DEFAULT_CONTENT if legacy_config is None else " 原有要求\n\n\n\n原有文本 "
        assert contribution["content"] == expected
        assert contribution["required"] is True
        if legacy_config is not None:
            assert json.loads(config_path.read_text(encoding="utf-8")) == legacy_config

        content = "用日语回答，先纠正明显的语法错误。\n\n今天练习介绍周末做过的事情。"
        assert application.settings_save(
            PLUGIN_ID, PLUGIN_ID, {"content": content},
        ) == {"saved": True, "applicationState": "applied", "reasonCode": "READY"}
        contribution, = collect(provider, "updated")
        assert contribution["content"] == content
        assert contribution["required"] is True

        application.set_enabled(installed.install_id, False)
        assert catalog() == []
        application.set_enabled(installed.install_id, True)
        reloaded, = catalog()
        contribution, = collect(reloaded, "reloaded")
        assert contribution["content"] == content
        application.settings_save(PLUGIN_ID, PLUGIN_ID, {"content": ""})
        assert collect(reloaded, "cleared") == []
        application.set_enabled(installed.install_id, False)
        application.set_enabled(installed.install_id, True)
        cleared, = catalog()
        assert collect(cleared, "cleared-reloaded") == []
    finally:
        application.close()


@pytest.mark.parametrize("capabilities", [
    None,
    {"schemaVersion": 1, "fragmentKinds": ["data", "instruction"], "scopes": ["turn"], "failurePolicies": ["abort"]},
    {"schemaVersion": 2, "scopes": ["step"], "failurePolicies": ["abort"]},
    {"schemaVersion": 2, "scopes": ["turn"], "failurePolicies": ["skip"]},
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


@pytest.mark.parametrize("saved, expected", [
    ({"rules": "", "reference": "仅保留文本"}, "仅保留文本"),
    ({"reference": "追加内容"}, DEFAULT_CONTENT + "\n\n追加内容"),
    ({"rules": "甲" * 4096, "reference": "乙" * 4096}, "甲" * 4096 + "\n\n" + "乙" * 4096),
    ({"rules": "旧内容", "reference": "旧文本", "content": "新内容"}, "新内容"),
    ({"rules": "旧内容", "reference": "旧文本", "content": ""}, ""),
], ids=["reference-only", "legacy-default", "legacy-maximum", "content-precedence", "cleared"])
def test_existing_config_content_is_preserved_without_resurrecting_cleared_text(
    saved: dict[str, str], expected: str,
) -> None:
    assert _config_values(saved) == {"content": expected}
