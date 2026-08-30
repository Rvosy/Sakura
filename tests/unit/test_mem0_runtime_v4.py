from __future__ import annotations

import ast
import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import yaml

from app.agent.tools import ToolRegistry
from app.core_host.plugin_runtime_application import PluginRuntimeApplication
from app.plugins.dependencies import PluginDependencyRoots
from app.plugins.inventory import PluginInventory
from app.storage.runtime_roots import RuntimeRoots


def _roots(tmp_path: Path) -> RuntimeRoots:
    repository = Path(__file__).parents[2]
    distribution = tmp_path / "distribution"
    bundled = distribution / "plugins" / "builtin"
    bundled.mkdir(parents=True)
    shutil.copytree(
        repository / "plugins" / "builtin" / "sakura_mem0",
        bundled / "sakura_mem0",
    )
    user = tmp_path / "user"
    _write_character_and_config(user)
    _write_third_party_memory(user / "plugins" / "user" / "third_party_memory")
    _prepare_mem0_dependency_root(distribution, user, bundled / "sakura_mem0")
    return RuntimeRoots(distribution, user)


def _write_character_and_config(user: Path) -> None:
    config = user / "config"
    config.mkdir(parents=True)
    (config / "characters.yaml").write_text(
        json.dumps({"current_character_id": "sakura"}),
        encoding="utf-8",
    )
    (config / "system_config.yaml").write_text(
        json.dumps({
            "config_version": 1,
            "memory_curation": {
                "enabled": True,
                "trigger_turns": 8,
                "backfill_limit": 200,
            },
        }),
        encoding="utf-8",
    )
    (config / "api.yaml").write_text(
        json.dumps({
            "api_profiles": [{
                "id": "fixture",
                "alias": "Fixture",
                "base_url": "https://example.invalid/v1",
                "api_key": "PRIVATE_MODEL_SECRET",
                "models": [{"name": "curator"}],
            }],
            "model_slots": {
                "chat": {"profile_id": "fixture", "model": "curator"},
            },
        }),
        encoding="utf-8",
    )
    character = user / "characters" / "sakura"
    character.mkdir(parents=True)
    (character / "card.md").write_text("Sakura system prompt", encoding="utf-8")
    (character / "portrait.png").write_bytes(b"portrait")
    (character / "character.json").write_text(
        json.dumps({
            "id": "sakura",
            "display_name": "Sakura",
            "card": "card.md",
            "portrait": {"default": "portrait.png"},
        }),
        encoding="utf-8",
    )


def _prepare_mem0_dependency_root(
    distribution: Path,
    user: Path,
    plugin_root: Path,
) -> None:
    dependencies = PluginDependencyRoots(user, distribution_root=distribution)
    declaration = dependencies.declaration(plugin_root)
    assert declaration is not None
    dependency_root = distribution / "plugins" / "dependencies" / "sakura.memory.mem0"
    dependency_root.mkdir(parents=True)
    yaml_package = Path(yaml.__file__).resolve().parent
    shutil.copytree(yaml_package, dependency_root / "yaml")
    (dependency_root / ".sakura-dependencies.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "kind": declaration.kind,
            "fingerprint": declaration.fingerprint,
            "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        }),
        encoding="utf-8",
    )


def _write_third_party_memory(root: Path) -> None:
    root.mkdir(parents=True)
    (root / "plugin.yaml").write_text(
        """api: 4
id: third.party.memory
name: Third-party Memory
version: 1.0.0
entry: plugin:Plugin
enabled: true
provides: []
requires:
  - sakura.host.context
  - sakura.host.tools
  - sakura.host.settings
  - sakura.host.settings.surface-v0
""",
        encoding="utf-8",
    )
    (root / "plugin.py").write_text(
        """class Plugin:
    def setup(self, context):
        context.get("sakura.host.context").register(
            {
                "providerId": "third.party.memory.recall",
                "description": "Third-party memory contribution",
                "order": 70,
            },
            lambda request: [{
                "id": "third-party-memory",
                "content": "third-party:" + request["current_input"],
                "priority": 45,
                "budgetHint": 64,
            }],
        )
        context.get("sakura.host.tools").register(
            {
                "name": "third_party_memory_search",
                "description": "Search the third-party memory implementation.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
                "group": "plugin",
                "risk": "low",
            },
            lambda arguments: {"result": arguments["query"]},
        )
        context.get("sakura.host.tools").register(
            {
                "name": "third_party_memory_remember",
                "description": "Store a memory with the third-party implementation.",
                "parameters": {
                    "type": "object",
                    "properties": {"content": {"type": "string"}},
                    "required": ["content"],
                    "additionalProperties": False,
                },
                "group": "plugin",
                "risk": "low",
            },
            lambda arguments: {"remembered": arguments["content"]},
        )
        context.get("sakura.host.settings").register(
            {
                "sectionId": "third-party-memory",
                "title": "Third-party Memory",
                "order": 50,
                "fields": [{
                    "key": "enabled",
                    "label": "Enabled",
                    "type": "boolean",
                    "default": True,
                }],
            },
            load=lambda: {"enabled": True},
            save=lambda values: {"applicationState": "applied"},
        )
        context.get("sakura.host.settings.surface-v0").register(
            "third-party-memory",
            "memory",
        )
""",
        encoding="utf-8",
    )


def _core_imports(plugin_root: Path) -> list[str]:
    imports: list[str] = []
    for path in plugin_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(name.name for name in node.names if name.name == "app" or name.name.startswith("app."))
            elif isinstance(node, ast.ImportFrom) and (
                node.module == "app" or str(node.module).startswith("app.")
            ):
                imports.append(str(node.module))
    return imports


def test_mem0_v4_isolated_process_and_replaceable_contributions(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    inventory = PluginInventory(roots).scan()
    records = {record.plugin_id: record for record in inventory.records}
    assert records["sakura.memory.mem0"].source == "bundled"
    assert records["third.party.memory"].source == "user"
    assert _core_imports(
        roots.distribution_root / "plugins" / "builtin" / "sakura_mem0"
    ) == []

    registry = ToolRegistry()
    context_providers = []
    runtime = SimpleNamespace(
        character_id="sakura",
        set_context_providers=lambda values: (
            context_providers.clear(),
            context_providers.extend(values),
        ),
    )
    session = SimpleNamespace(
        character=SimpleNamespace(id="sakura"),
        runtime=runtime,
    )
    application = PluginRuntimeApplication(
        roots,
        "generation-mem0-v4",
        registry,
        inventory.runtime_specs,
        call_timeout=1.0,
    )
    try:
        application.start()
        application.bind_runtime(registry, runtime, session=session)
        active = {
            item["pluginId"]: item
            for item in application.public_snapshot()["plugins"]
        }
        assert set(active) == {"sakura.memory.mem0", "third.party.memory"}
        assert all(item["state"] == "active" for item in active.values())
        assert len({item["pid"] for item in active.values()}) == 2
        assert os.getpid() not in {item["pid"] for item in active.values()}

        assert {provider.provider_id for provider in context_providers} == {
            "sakura.memory.mem0.recall",
            "third.party.memory.recall",
        }
        assert registry.get("memory_search") is not None
        assert registry.get("third_party_memory_search") is not None
        assert registry.get("third_party_memory_remember") is not None

        settings = {
            item["pluginId"]: item
            for item in application.settings_sections("memory")
        }
        assert set(settings) == {"sakura.memory.mem0", "third.party.memory"}
        mem0_snapshot = next(
            item
            for item in application.settings_snapshot()["plugins"]
            if item["pluginId"] == "sakura.memory.mem0"
        )
        sections = {item["sectionId"]: item for item in mem0_snapshot["sections"]}
        assert set(sections) == {
            "memory",
            "memory_embedding_component",
            "memory_management",
        }
        assert all(item["reasonCode"] == "READY" for item in sections.values())
        assert "PRIVATE_MODEL_SECRET" not in str(mem0_snapshot)
        assert [item["collectionId"] for item in sections["memory_management"]["collections"]] == [
            "memories"
        ]
        slots = application.model_slots()
        assert [(item["ownerId"], item["slotId"]) for item in slots] == [
            ("sakura.memory.mem0", "curation")
        ]

        disabled = application.set_plugin_enabled("sakura.memory.mem0", False)
        disabled_records = {item["pluginId"]: item for item in disabled["plugins"]}
        assert disabled_records["sakura.memory.mem0"]["state"] == "disabled"
        assert disabled_records["third.party.memory"]["state"] == "active"
        assert {provider.provider_id for provider in context_providers} == {
            "third.party.memory.recall"
        }
        assert registry.get("memory_search") is None
        third_party_tool = registry.get("third_party_memory_search")
        assert third_party_tool is not None
        assert registry.execute(
            "third_party_memory_search",
            {"query": "replacement"},
        ).content == {"result": "replacement"}
        assert registry.execute(
            "third_party_memory_remember",
            {"content": "replacement write"},
        ).content == {"remembered": "replacement write"}
        assert [
            item["pluginId"] for item in application.settings_sections("memory")
        ] == ["third.party.memory"]
        assert application.model_slots() == []
    finally:
        application.close()
