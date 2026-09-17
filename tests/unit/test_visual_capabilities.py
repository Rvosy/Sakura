from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.config.character_resources import CharacterVisualResource
from app.core_host.visual_host import VisualHost, VisualHostError
from app.plugins.discovery import PluginDiscovery
from app.plugins.installer import LocalPluginInstaller
from app.plugins.inventory import PluginDesiredStateStore, PluginInventory, RuntimePluginSpec
from app.plugins.runtime_v4 import PluginRuntimeError
from app.storage.runtime_roots import RuntimeRoots


def _plugin(root: Path, declaration: dict | None = None) -> Path:
    directory = root / "plugins" / "user" / "visual"
    directory.mkdir(parents=True)
    (directory / "plugin.py").write_text("raise RuntimeError('discovery must not run this')", encoding="utf-8")
    (directory / "renderer.js").write_text("export function createRenderer() {}", encoding="utf-8")
    (directory / "editor.mjs").write_text("export function createEditor() {}", encoding="utf-8")
    manifest = {
        "api": 4, "id": "example.visual", "entry": "plugin:Plugin",
        "provides": ["example.visual.control"],
        "visuals": [declaration or {
            "type": "example.parameters@1", "service": "example.visual.control", "contract": 1,
            "renderer": "renderer.js", "editor": "editor.mjs",
        }],
    }
    (directory / "plugin.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return directory


def test_disabled_visual_declaration_survives_discovery_and_startup_serialization(tmp_path: Path) -> None:
    _plugin(tmp_path)
    desired = PluginDesiredStateStore(tmp_path)
    desired.set("example.visual", False)
    inventory = PluginInventory(tmp_path).scan()
    record = inventory.records[0]
    assert record.desired_enabled is False
    assert record.runtime_eligible is True
    assert record.visuals[0].resource_type == "example.parameters@1"
    spec = inventory.runtime_specs[0]
    assert spec.to_plugin_spec(tmp_path).visuals == record.visuals
    assert PluginDiscovery(tmp_path).discover()[0].visuals == record.visuals
    before = inventory.revision
    manifest = tmp_path / "plugins/user/visual/plugin.yaml"
    raw = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    raw["visuals"][0]["contract"] = 2
    manifest.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert PluginInventory(tmp_path).scan().revision != before
    assert PluginInventory(tmp_path).scan().records[0].visuals[0].contract == 2


@pytest.mark.parametrize("field,value", [
    ("renderer", "../outside.js"), ("renderer", "https://example.com/module.js"),
    ("renderer", "C:/outside.js"), ("renderer", "C:outside.js"),
    ("renderer", "missing.js"), ("renderer", "renderer.js:stream"),
    ("editor", None), ("editor", "editor.txt"),
    ("type", "example.unversioned"), ("contract", True), ("contract", 0),
    ("service", "example.undeclared"),
])
def test_invalid_optional_declarations_do_not_disable_other_plugin_services(tmp_path: Path, field: str, value: object) -> None:
    declaration = {"type": "example.parameters@1", "service": "example.visual.control", "contract": 1, "renderer": "renderer.js"}
    declaration[field] = value
    plugin_root = _plugin(tmp_path, declaration)
    record = PluginInventory(tmp_path).scan().records[0]
    assert record.reason_code == "READY"
    assert record.runtime_spec() is not None
    assert record.provides == ("example.visual.control",)
    assert len(record.capability_issues) == 1
    assert len(record.visuals) == (1 if field == "editor" else 0)
    if field == "editor":
        assert record.visuals[0].renderer == "renderer.js"
        assert record.visuals[0].editor is None
    assert PluginDiscovery(tmp_path).discover()[0].visuals == record.visuals
    assert LocalPluginInstaller(tmp_path)._validated_spec(plugin_root).visuals == record.visuals



def test_resource_reference_needs_no_portrait_and_never_writes_the_package(tmp_path: Path) -> None:
    root = tmp_path / "model"
    root.mkdir()
    entry = root / "model.json"
    content = '{"parameters": {"angle": [-30, 30]}, "actions": ["wave"]}'
    entry.write_text(content, encoding="utf-8")
    resource = CharacterVisualResource.from_mapping({"id": "model-1", "type": "example.parameters@1", "root": "model", "entry": "model.json"})
    resource.validate_paths(tmp_path)
    assert entry.read_text(encoding="utf-8") == content
    assert resource.to_mapping()["type"] == "example.parameters@1"


@pytest.mark.parametrize("field,value", [
    ("root", "../escape"), ("root", "/absolute"), ("root", "C:relative"),
    ("root", "\\\\server\\share"), ("entry", "../model.json"), ("entry", "model.json:stream"),
    ("id", "../id"), ("type", "unknown"),
])
def test_unknown_resource_types_do_not_bypass_path_validation(field: str, value: object) -> None:
    raw = {"id": "model", "type": "example.unknown@1", "root": ".", "entry": "model.json"}
    raw[field] = value
    with pytest.raises(ValueError):
        CharacterVisualResource.from_mapping(raw)


def test_resource_entry_must_stay_inside_its_own_resource_root(tmp_path: Path) -> None:
    package = tmp_path / "package"
    resource_root = package / "model"
    resource_root.mkdir(parents=True)
    outside = package / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    try:
        (resource_root / "model.json").symlink_to(outside)
    except OSError:
        pytest.skip("host does not allow creating symlinks")
    resource = CharacterVisualResource("model", "example.parameters@1", "model", "model.json")
    with pytest.raises(ValueError, match="VISUAL_RESOURCE_INVALID"):
        resource.validate_paths(package)


class _Runtime:
    identity = {"providerId": "example.visual", "scopeId": "scope-1"}
    description = {"prompt": "numeric", "outputSchema": {}, "rendererData": {}, "parserData": {}}
    before_result = None
    parsed = {"state": {"angle": 1}, "actions": []}

    def service_identity(self, _service):
        if self.identity is None:
            raise PluginRuntimeError("SERVICE_MISSING")
        return dict(self.identity)

    def call_service(self, _service, method, *args):
        if self.before_result is not None:
            self.before_result()
        return self.description if method == "describe" else self.parsed


@pytest.fixture
def binding_host(tmp_path: Path):
    plugin = _plugin(tmp_path)
    (tmp_path / "resource.json").write_text("{}", encoding="utf-8")
    resource = CharacterVisualResource("model", "example.parameters@1", ".", "resource.json")
    runtime = _Runtime()
    inventory = PluginInventory(RuntimeRoots(tmp_path, tmp_path))
    return VisualHost(runtime, inventory=inventory.scan), runtime, resource, plugin, tmp_path


def test_in_flight_describe_and_parse_are_discarded_after_invalidation(binding_host) -> None:
    host, runtime, resource, _plugin_root, package = binding_host
    runtime.before_result = host.clear
    with pytest.raises(VisualHostError, match="VISUAL_BINDING_EXPIRED"):
        host.bind("character", package, resource)
    runtime.before_result = None
    binding = host.bind("character", package, resource)
    runtime.before_result = host.clear
    result = binding.parse_control({"version": 1, "resourceId": resource.id, "payload": {}})
    assert result.control is None
    assert result.reason_code == "VISUAL_BINDING_EXPIRED"


def test_preparing_a_binding_keeps_the_previous_presentation_until_commit(binding_host) -> None:
    host, runtime, resource, _plugin_root, package = binding_host
    previous = host.bind("character", package, resource)
    observed = []
    runtime.before_result = lambda: observed.append(host.presentation()["bindingId"])
    replacement = host.bind("character", package, resource)
    assert observed == [previous.id]
    assert host.presentation()["bindingId"] == replacement.id
    assert previous.parse_control({"version": 1, "resourceId": resource.id, "payload": {}}).reason_code == "VISUAL_BINDING_EXPIRED"


def test_resource_candidates_report_contract_failure_and_require_provider_selection(binding_host) -> None:
    host, runtime, resource, plugin, package = binding_host
    manifest = plugin / "plugin.yaml"
    raw = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    raw["visuals"][0]["contract"] = 2
    manifest.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert host.candidates(resource.type)[0]["reasonCode"] == "VISUAL_CONTRACT_UNSUPPORTED"
    with pytest.raises(VisualHostError, match="VISUAL_CONTRACT_UNSUPPORTED"):
        host.bind("character", package, resource)
    raw["visuals"][0]["contract"] = 1
    manifest.write_text(yaml.safe_dump(raw), encoding="utf-8")
    runtime.identity = None
    assert host.candidates(resource.type)[0]["reasonCode"] == "VISUAL_SERVICE_UNAVAILABLE"
    runtime.identity = {"providerId": "another.plugin", "scopeId": "scope-2"}
    assert host.candidates(resource.type)[0]["reasonCode"] == "VISUAL_PROVIDER_MISMATCH"
    alternate = package / "plugins/user/alternate"
    alternate.mkdir()
    for name in ("plugin.py", "renderer.js", "editor.mjs"):
        (alternate / name).write_text((plugin / name).read_text(encoding="utf-8"), encoding="utf-8")
    raw["id"] = "alternate.visual"
    raw["enabled"] = False
    (alternate / "plugin.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(VisualHostError, match="VISUAL_PROVIDER_SELECTION_REQUIRED"):
        host.bind("character", package, resource)
    with pytest.raises(VisualHostError, match="PLUGIN_DISABLED"):
        host.bind("character", package, resource, provider_id="alternate.visual")


@pytest.mark.parametrize("parsed", [None, {}, {"actions": "wave"}])
def test_provider_cannot_return_invalid_or_rerouted_control_data(binding_host, parsed) -> None:
    host, runtime, resource, _plugin_root, package = binding_host
    binding = host.bind("character", package, resource)
    runtime.parsed = parsed
    result = binding.parse_control({"version": 1, "resourceId": resource.id, "payload": {}})
    assert result.control is None
    assert result.reason_code == "VISUAL_CONTROL_INVALID"


def test_revocation_during_identity_read_cannot_publish_control(binding_host) -> None:
    host, runtime, resource, _plugin_root, package = binding_host
    binding = host.bind("character", package, resource)
    original = runtime.service_identity
    def identity(service):
        result = original(service)
        host.clear()
        return result
    runtime.service_identity = identity
    assert binding.parse_control({"version": 1, "resourceId": resource.id, "payload": {}}).reason_code == "VISUAL_BINDING_EXPIRED"


@pytest.mark.parametrize("error", [
    ValueError("invalid service state"),
    VisualHostError("VISUAL_DESCRIPTION_INVALID"),
    PluginRuntimeError("SERVICE_INTERNAL_ERROR", "service lookup failed"),
])
def test_visual_prompt_errors_are_not_mistaken_for_absent_capabilities(binding_host, monkeypatch, error):
    from sakura_assistant.agent.runtime import AgentRuntime

    host, provider, resource, _plugin_root, package = binding_host
    binding = host.bind("character", package, resource)
    runtime = AgentRuntime(None, "test character")
    runtime.set_visual_binding(binding)

    def fail(_service):
        raise error

    monkeypatch.setattr(provider, "service_identity", fail)
    with pytest.raises(type(error)) as caught:
        runtime._build_tool_system_prompt()
    assert caught.value is error


@pytest.mark.parametrize("invalidate", ["close", "missing", "replaced"])
def test_retired_visual_prompt_is_omitted_without_hiding_other_errors(binding_host, invalidate):
    from sakura_assistant.agent.runtime import AgentRuntime

    host, provider, resource, _plugin_root, package = binding_host
    binding = host.bind("character", package, resource)
    runtime = AgentRuntime(None, "test character")
    runtime.set_visual_binding(binding)
    assert runtime.reply_visual["resourceId"] == resource.id
    if invalidate == "close":
        binding.close()
    elif invalidate == "missing":
        provider.identity = None
    else:
        provider.identity = {"providerId": "example.visual", "scopeId": "replacement"}
    assert runtime.reply_visual is None


def test_conflicting_user_install_does_not_hide_inventory_bundled_winner(binding_host) -> None:
    host, runtime, resource, plugin, package = binding_host
    bundled = package / "plugins/builtin/visual"
    bundled.mkdir(parents=True)
    for name in ("plugin.yaml", "plugin.py", "renderer.js", "editor.mjs"):
        (bundled / name).write_text((plugin / name).read_text(encoding="utf-8"), encoding="utf-8")
    assert {item["reasonCode"] for item in host.candidates(resource.type)} == {"READY", "PLUGIN_ID_CONFLICT"}
    assert host.bind("character", package, resource).presentation()["installId"].startswith("pi_bundled_")
    assert host.bind("character", package, resource, provider_id="example.visual").presentation()["installId"].startswith("pi_bundled_")


def test_optional_editor_failure_keeps_renderer_and_reports_editor_unavailable(binding_host):
    host, _runtime, resource, plugin, package = binding_host
    (plugin / "editor.mjs").unlink()
    assert host.bind("character", package, resource).presentation()["renderer"] == "renderer.js"
    assert host.resource_choice(resource)["reasonCode"] == "READY"
    assert host.catalog()[0]["reasonCode"] == "VISUAL_MODULE_INVALID"
    with pytest.raises(VisualHostError, match="VISUAL_MODULE_INVALID"):
        host.editor(resource, {})


def test_bad_renderer_preserves_other_capabilities_and_reports_the_failed_type(binding_host):
    host, _runtime, resource, plugin, _package = binding_host
    manifest = plugin / "plugin.yaml"
    raw = yaml.safe_load(manifest.read_text())
    raw["visuals"].append({**raw["visuals"][0], "type": "example.other@1", "renderer": "missing.js"})
    raw["visuals"][0]["displayDescription"] = "额外展示信息"
    manifest.write_text(yaml.safe_dump(raw))
    snapshot = PluginInventory(plugin.parents[2]).scan()
    assert snapshot.records[0].runtime_eligible
    assert len(snapshot.runtime_specs[0].visuals) == 1
    assert host.resource_choice(resource)["reasonCode"] == "READY"
    missing = CharacterVisualResource("other", "example.other@1", ".", "resource.json")
    assert host.resource_choice(missing)["reasonCode"] == "VISUAL_MODULE_INVALID"
    with pytest.raises(VisualHostError, match="VISUAL_MODULE_INVALID"):
        host._select(missing.type)


def test_plugin_module_accepts_unicode_names_but_not_symlinks_outside_installation(tmp_path):
    plugin = _plugin(tmp_path)
    manifest = plugin / "plugin.yaml"
    raw = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    (plugin / "角色 渲染.js").write_text("export function mount() {}", encoding="utf-8")
    raw["visuals"][0]["renderer"] = "角色 渲染.js"
    manifest.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    assert PluginInventory(tmp_path).scan().records[0].visuals[0].renderer == "角色 渲染.js"
    outside = tmp_path / "outside.js"
    outside.write_text("export function mount() {}")
    (plugin / "角色 渲染.js").unlink()
    try:
        (plugin / "角色 渲染.js").symlink_to(outside)
    except OSError:
        pytest.skip("host does not allow creating symlinks")
    record = PluginInventory(tmp_path).scan().records[0]
    assert record.runtime_eligible and not record.visuals
    assert record.capability_issues[0]["reasonCode"] == "VISUAL_MODULE_INVALID"


def test_control_projection_preserves_host_routing_and_all_actions(binding_host):
    host, runtime, resource, _plugin_root, package = binding_host
    binding = host.bind("character", package, resource)
    runtime.parsed = {"bindingId": "other", "resourceId": "other", "actions": [{}] * 40}
    result = binding.parse_control({"version": 1, "resourceId": resource.id, "payload": {}})
    assert result.control["bindingId"] == binding.id
    assert result.control["resourceId"] == resource.id
    assert len(result.control["actions"]) == 40
