from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml

from app.agent.tools import ToolRegistry
from app.config.character_resources import CharacterVisualResource
from app.core_host.plugin_application import PluginApplicationHost
from app.core_host.visual_host import VisualHostError
from app.storage.runtime_roots import RuntimeRoots


_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/visual_numeric"
_PLUGIN = (_FIXTURE / "plugin.py").read_text(encoding="utf-8")



@contextmanager
def numeric_application(tmp_path: Path, plugin_code: str = _PLUGIN):
    distribution, user = tmp_path / "distribution", tmp_path / "user"
    plugin = distribution / "plugins/builtin/numeric"
    plugin.mkdir(parents=True)
    (plugin / "plugin.py").write_text(plugin_code, encoding="utf-8")
    (plugin / "renderer.js").write_text((_FIXTURE / "frontend/renderer.js").read_text(encoding="utf-8"), encoding="utf-8")
    (plugin / "editor.js").write_text((_FIXTURE / "frontend/editor.js").read_text(encoding="utf-8"), encoding="utf-8")
    (plugin / "plugin.yaml").write_text(yaml.safe_dump({
        "api": 4, "id": "fixture.numeric", "entry": "plugin:Plugin",
        "provides": ["fixture.numeric.control"], "requires": ["sakura.host.character"],
        "visuals": [{"type": "fixture.numeric@1", "service": "fixture.numeric.control", "contract": 1, "renderer": "renderer.js", "editor": "editor.js"}],
    }), encoding="utf-8")
    package = user / "characters/character"
    model = package / "numeric"
    model.mkdir(parents=True)
    (model / "resource.json").write_text('{"maxAngle": 20}', encoding="utf-8")
    (package / "card.md").write_text("角色人设", encoding="utf-8")
    (package / "character.json").write_text(json.dumps({
        "id": "character", "display_name": "角色", "card": "card.md",
        "visuals": {"resources": [{"id": "numeric-1", "type": "fixture.numeric@1", "root": "numeric", "entry": "resource.json"}], "default": "numeric-1"},
    }), encoding="utf-8")
    roots = RuntimeRoots(distribution, user)
    application = PluginApplicationHost(roots, "visual-test-generation", ToolRegistry())
    application.start()
    resource = CharacterVisualResource("numeric-1", "fixture.numeric@1", "numeric", "resource.json")
    try:
        yield application, package, resource
    finally:
        application.close()


@pytest.fixture
def visual_application(tmp_path):
    with numeric_application(tmp_path) as fixture:
        yield fixture


def _control(resource, payload):
    return {"version": 1, "resourceId": resource.id, "payload": payload}


@pytest.mark.parametrize("cover", ["cover.webp", None, "missing.png", "../outside.png", "model.json"])
def test_static_model_cover_needs_no_renderer_or_editor(tmp_path, cover):
    from app.core_host.character_studio import CharacterStudioBoundary
    plugin_code = _PLUGIN.replace("            def describe(self, request):", "            def previewImage(self, resource, raw):\n                return (raw or {}).get('cover')\n\n            def describe(self, request):")
    plugin_code = plugin_code.replace('"exportResource"))', '"exportResource", "previewImage"))')
    with numeric_application(tmp_path, plugin_code) as (application, package, resource):
        (package / "numeric/cover.webp").write_bytes(b"static model cover")
        (package / "numeric/model.json").write_text("{}")
        (package / "outside.png").write_bytes(b"outside resource")
        (package / "numeric/resource.json").write_text(json.dumps({"maxAngle": 20, "cover": cover}), encoding="utf-8")
        boundary = CharacterStudioBoundary("g", "c", package.parents[1], plugin_application_provider=lambda: application)
        boundary._dispatch("studio.character.open", {"characterId": "character"})
        calls = []
        runtime = application.application.visuals._runtime
        original = runtime.call_service
        def call(service, method, *args, **kwargs):
            calls.append(method)
            return original(service, method, *args, **kwargs)
        runtime.call_service = call
        item = boundary._dispatch("studio.visual.previews", {"workspaceId": "character"})["items"][0]
        assert calls == ["previewImage"]
        assert item["resourceId"] == resource.id
        if cover == "cover.webp":
            assert item["relativePath"] == cover
            assert item["mediaType"] == "image/webp"
            assert Path(item["sourcePath"]).read_bytes() == b"static model cover"
        else:
            assert item == {"resourceId": resource.id, "relativePath": None}


def test_model_without_optional_cover_service_still_opens(visual_application):
    from app.core_host.character_studio import CharacterStudioBoundary
    application, package, resource = visual_application
    boundary = CharacterStudioBoundary("g", "c", package.parents[1], plugin_application_provider=lambda: application)
    boundary._dispatch("studio.character.open", {"characterId": "character"})
    assert boundary._dispatch("studio.visual.previews", {"workspaceId": "character"})["items"] == [{"resourceId": resource.id, "relativePath": None}]
    assert boundary._dispatch("studio.visual.open", {"workspaceId": "character", "resourceId": resource.id})["data"] == {"maxAngle": 20}


def test_studio_can_repair_missing_resource_entry(visual_application):
    from app.core_host.character_studio import CharacterStudioBoundary
    application, package, resource = visual_application
    (package / resource.root / resource.entry).unlink()
    boundary = CharacterStudioBoundary("g", "c", package.parents[1], plugin_application_provider=lambda: application)
    opened = boundary._dispatch("studio.character.open", {"characterId": "character"})
    editor = boundary._dispatch("studio.visual.open", {"workspaceId": "character", "resourceId": resource.id})
    assert editor["data"] == {"maxAngle": 20}
    opened["doc"]["visualData"] = {resource.id: {"maxAngle": 25}}
    boundary._dispatch("studio.character.publish", {"workspaceId": "character", "doc": opened["doc"]})
    assert json.loads((package / resource.root / resource.entry).read_text())["maxAngle"] == 25


def test_settings_visual_selection_is_personal_and_rebinds_control(visual_application):
    from app.config.settings_service import AppSettingsService
    from app.core_host.character_settings import CharacterSettingsBoundary, CharacterSettingsError
    application, package, resource = visual_application
    path = package / "character.json"
    manifest = json.loads(path.read_text())
    manifest["visuals"]["resources"][0]["name"] = "日常形态"
    manifest["visuals"]["resources"].append({**resource.to_mapping(), "id": "numeric-2", "name": "另一种形态"})
    path.write_text(json.dumps(manifest), encoding="utf-8")
    before = path.read_bytes()
    settings = CharacterSettingsBoundary("g", "c", package.parents[1], plugin_application_provider=lambda: application)
    settings.select("character")
    application.bind_character_presentation("character")
    old_binding = application.application._visual_binding
    service_identity = application.application.service_identity(old_binding.capability.service)
    assert settings.visual_snapshot("character")["preferenceResourceId"] is None
    receipt = settings.select("character", {"character": "numeric-2"})
    assert receipt["changePlan"] == "visual_rebind"
    assert application.application.service_identity(old_binding.capability.service) == service_identity
    assert application.visual_presentation()["visual"]["resourceId"] == "numeric-2"
    assert old_binding.parse_control(_control(resource, {"angle": 1})).control is None
    reopened = CharacterSettingsBoundary("g", "c", package.parents[1], plugin_application_provider=lambda: application)
    assert reopened.visual_snapshot("character")["preferenceResourceId"] == "numeric-2"
    assert path.read_bytes() == before
    install_id = settings.visual_snapshot("character")["resources"][0]["installId"]
    application.set_enabled(install_id, False)
    assert all(item["reasonCode"] == "PLUGIN_DISABLED" for item in settings.visual_snapshot("character")["resources"])
    with pytest.raises(CharacterSettingsError, match="所选形态无法使用"):
        settings.select("character", {"character": "numeric-1"})
    assert AppSettingsService(package.parents[1]).load_visual_selections() == {"character": "numeric-2"}
    application.set_enabled(install_id, True)
    settings.select("character", {"character": None})
    assert application.visual_presentation()["visual"]["resourceId"] == "numeric-1"
    assert path.read_bytes() == before


def test_visual_folder_import_preserves_model_references_and_cleans_cancelled_batch(visual_application, tmp_path, monkeypatch):
    from app.core_host.character_studio import CharacterStudioBoundary
    from app.config.character_studio import CharacterStudioOperationCancelled, _copy_file_cancellable
    application, package, resource = visual_application
    boundary = CharacterStudioBoundary("g", "c", package.parents[1], plugin_application_provider=lambda: application)
    boundary._dispatch("studio.character.open", {"characterId": "character"})
    source = tmp_path / "model-source"
    (source / "textures").mkdir(parents=True)
    (source / "motions").mkdir()
    files = {"model.json": b'{"texture":"textures/Face.PNG","motion":"motions/idle.json"}', "textures/Face.PNG": b"texture", "motions/idle.json": b"{}"}
    for name, content in files.items(): (source / name).write_bytes(content)
    result = boundary._service.import_visual_asset("character", source, resource.id)
    assert len(result["items"]) == 3
    entry = next(item for item in result["items"] if item["name"] == "model.json")
    imported = boundary._service._workspace_package("character") / entry["relative_path"]
    for name, content in files.items(): assert (imported.parent / name).read_bytes() == content
    editor = boundary._dispatch("studio.visual.open", {"workspaceId": "character", "resourceId": resource.id})
    assert Path(editor["assetRootPath"]) == boundary._service._workspace_package("character") / resource.root
    before = boundary._service._read_state("character")["imported_assets"]
    folders = set(imported.parent.parent.iterdir())
    count = 0
    def cancel_second(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2: raise CharacterStudioOperationCancelled()
        return _copy_file_cancellable(*args, **kwargs)
    monkeypatch.setattr("app.config.character_studio._copy_file_cancellable", cancel_second)
    with pytest.raises(CharacterStudioOperationCancelled): boundary._service.import_visual_asset("character", source, resource.id)
    assert boundary._service._read_state("character")["imported_assets"] == before
    assert set(imported.parent.parent.iterdir()) == folders


def test_visual_selection_rejects_removed_resource_without_partial_save(visual_application):
    from app.config.settings_service import AppSettingsService
    from app.core_host.character_settings import CharacterSettingsBoundary, CharacterSettingsError
    application, package, _ = visual_application
    settings = CharacterSettingsBoundary("g", "c", package.parents[1], plugin_application_provider=lambda: application)
    with pytest.raises(CharacterSettingsError) as error:
        settings.select("character", {"character": "removed"})
    assert error.value.code == "VISUAL_RESOURCE_NOT_FOUND"
    assert not AppSettingsService(package.parents[1]).characters_config_path.exists()


def test_incompatible_plugin_preserves_resources_when_saving_public_fields(visual_application):
    from app.core_host.character_studio import CharacterStudioBoundary
    application, package, resource = visual_application
    manifest_path = package.parents[2] / "distribution/plugins/builtin/numeric/plugin.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["api"] = 5
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    boundary = CharacterStudioBoundary("g", "c", package.parents[1], plugin_application_provider=lambda: application)
    opened = boundary._dispatch("studio.character.open", {"characterId": "character"})
    opened["doc"]["displayName"] = "新名字"
    before = (package / resource.root / resource.entry).read_bytes()
    boundary._dispatch("studio.character.publish", {"workspaceId": "character", "doc": opened["doc"]})
    assert json.loads((package / "character.json").read_text(encoding="utf-8"))["display_name"] == "新名字"
    assert (package / resource.root / resource.entry).read_bytes() == before


def test_editor_scope_changes_on_reload_and_is_unavailable_when_disabled(visual_application):
    application, package, resource = visual_application
    host = application.application.visuals
    editor = host.editor(resource, {"maxAngle": 20})
    scope = editor["providerScopeId"]
    assert host.catalog()[0]["scopeId"] == scope
    application.application.reload_plugin("fixture.numeric")
    assert host.catalog()[0]["scopeId"] != scope
    application.application.set_plugin_enabled("fixture.numeric", False)
    assert host.catalog()[0]["scopeId"] is None


def test_numeric_studio_private_draft_publish_full_archive_and_component_roundtrip(visual_application, tmp_path):
    from app.config.character_loader import CharacterRegistry
    from app.core_host.character_studio import CharacterStudioBoundary
    from app.config.character_archive import export_character_archive, import_character_archive
    application, package, _ = visual_application
    root = package.parents[1]
    boundary = CharacterStudioBoundary("g", "c", root, plugin_application_provider=lambda: application)
    def request(name, payload):
        return boundary._dispatch(name, payload)
    opened = request("studio.character.open", {"characterId": "character"})
    descriptor = request("studio.visual.open", {"workspaceId": opened["workspaceId"], "resourceId": "numeric-1"})
    assert descriptor["data"] == {"maxAngle": 20}
    assert descriptor["presentation"]["visual"]["editor"] == "editor.js"
    doc = opened["doc"]
    doc["visualData"] = {"numeric-1": {"maxAngle": 28, "privatePascalCase": {"Keep_This": True}}}
    saved = request("studio.draft.save", {"workspaceId": opened["workspaceId"], "doc": doc})
    assert saved["doc"]["visualData"]["numeric-1"]["privatePascalCase"] == {"Keep_This": True}
    reopened = request("studio.character.open", {"characterId": "character"})
    assert reopened["doc"]["visualData"] == doc["visualData"]
    request("studio.character.publish", {"workspaceId": opened["workspaceId"], "doc": doc})
    profile = CharacterRegistry(root).get("character")
    binding = application.application.visuals.bind(profile.id, profile.package_dir, profile.current_visual_resource)
    assert binding.description["rendererData"]["maxAngle"] == 28
    assert binding.parse_control(_control(profile.current_visual_resource, {"angle": 27})).control["state"]["angle"] == 27
    archive = tmp_path / "model.char"
    export_character_archive(profile, archive, include_voice=False)
    imported = import_character_archive(archive, tmp_path / "imported")
    assert not (imported.package_dir / "default.png").exists()
    assert json.loads((imported.package_dir / "numeric/resource.json").read_text())["privatePascalCase"] == {"Keep_This": True}
    component = tmp_path / "component.char"
    opened = request("studio.character.open", {"characterId": "character"})
    request("studio.visual.export", {"workspaceId": opened["workspaceId"], "resourceId": "numeric-1", "path": str(component)})
    other = request("studio.character.create", {"doc": {"id": "other", "displayName": "另一个角色"}})
    other["doc"]["cardText"] = "B 的人格"
    request("studio.draft.save", {"workspaceId": "other", "doc": other["doc"]})
    result = request("studio.visual.import", {"workspaceId": "other", "path": str(component)})
    assert result["doc"]["id"] == "other"
    assert result["doc"]["cardText"] == "B 的人格"
    assert result["doc"]["visuals"]["default"] != "numeric-1"
    # Import is draft-only and reversible until the normal publish transaction.
    assert "other" not in CharacterRegistry(root).profiles
    request("studio.draft.discard", {"workspaceId": "other"})
    assert "other" not in CharacterRegistry(root).profiles


def test_unbinding_chat_keeps_a_fresh_independent_visual_presentation(visual_application):
    application, _, _ = visual_application
    application.bind_character_presentation("character")
    before = application.visual_presentation()["visual"]["bindingId"]
    runtime = application.application
    runtime.unbind_session()
    after = application.visual_presentation()
    assert after["visualReasonCode"] == "READY"
    assert after["visual"]["bindingId"] != before
    runtime.set_plugin_enabled("fixture.numeric", False)
    assert application.visual_presentation()["visual"] is None


def test_large_optional_controls_cannot_discard_text_history(tmp_path):
    from app.core_host.real_chat import _project_reply
    from app.llm.chat_reply import ChatReply, ChatSegment
    from app.storage.timeline import NewTimelineEntry, TimelineKind, TimelineStore
    control = {"version": 1, "resourceId": "numeric-1", "bindingId": "a" * 32, "state": {"data": "x" * 60000}}
    reply = ChatReply([ChatSegment(text="你好", translation="", tone="中性", control=control) for _ in range(5)])
    projected = _project_reply(reply)
    assert all(item["text"] == "你好" and "control" not in item for item in projected)
    store = TimelineStore(tmp_path / "timeline.sqlite3")
    store.initialize()
    store.append(NewTimelineEntry(entry_id="assistant-1", turn_id="turn-1", character_id="character", kind=TimelineKind.ASSISTANT, origin="chat", created_at="2026-09-11T12:00:00+08:00", payload={"segments": projected}))
    assert store.read_all("character")[0].payload["segments"] == projected


@pytest.mark.parametrize("event", [False, True])
@pytest.mark.parametrize("payload,expected", [({"angle": 10, "wave": True}, True), ({"angle": 100}, False)])
def test_numeric_controls_follow_real_chat_and_event_pipeline_into_history(visual_application, tmp_path, event, payload, expected):
    from unittest.mock import MagicMock
    from app.agent.runtime import AgentRuntime
    from app.agent.actions import AgentEvent
    from app.core.chat_pipeline import ChatPipeline
    from app.core_host.real_chat import _project_reply
    from app.llm.api_client import OpenAICompatibleClient, ChatCompletionTurn
    from app.llm.chat_reply import parse_chat_reply
    from app.storage.timeline import NewTimelineEntry, TimelineKind, TimelineStore

    application, package, resource = visual_application
    binding = application.application.visuals.bind("character", package, resource)
    raw = json.dumps({"segments": [{"ja": "こんにちは", "zh": "你好", "tone": "中性", "control": _control(resource, payload)}]})
    client = MagicMock(spec=OpenAICompatibleClient)
    client.complete_with_tools.return_value = ChatCompletionTurn(content=raw, tool_calls=[], message={"role": "assistant", "content": raw})
    client.chat.return_value = parse_chat_reply(raw)
    client.resolve_dialogue_params.return_value = (0.8, {})
    runtime = AgentRuntime(client, "测试角色", character_id="character")
    runtime.set_visual_binding(binding)
    assert 'numeric-1' in runtime._build_tool_system_prompt()
    assert 'maxAngle' not in runtime._build_tool_system_prompt()  # no private parser snapshot
    pipeline = ChatPipeline(runtime)
    result = pipeline.run_event(AgentEvent("reminder_due", {"message": "你好"})) if event else pipeline.run_user_message([{"role": "user", "content": "你好"}])
    projected = _project_reply(result.reply)
    assert projected[0]["text"] == "こんにちは"
    assert projected[0]["translation"] == "你好"
    assert projected[0]["suppressTts"] is False
    assert ("control" in projected[0]) is expected
    if expected:
        assert projected[0]["control"]["state"] == {"angle": 10}
        assert projected[0]["control"]["actions"] == [{"wave": True}]
    store = TimelineStore(tmp_path / "timeline.sqlite3")
    store.initialize()
    store.append(NewTimelineEntry(entry_id="assistant-1", turn_id="turn-1", character_id="character", kind=TimelineKind.ASSISTANT, origin="chat", created_at="2026-09-11T12:00:00+08:00", payload={"segments": projected}))
    application.application.visuals.clear()
    assert store.read_all("character")[0].payload["segments"] == projected
    assert runtime.reply_visual is None
    # A retained response can still be read, but its target can no longer parse.
    assert binding.parse_control(_control(resource, payload)).control is None


def test_real_plugin_contributes_numeric_schema_and_parses_state_and_one_shot_action(visual_application) -> None:
    application, package, resource = visual_application
    host = application.application.visuals
    assert host.candidates(resource.type)[0]["reasonCode"] == "READY"
    binding = host.bind("character", package, resource)
    description = binding.description
    assert description["outputSchema"]["properties"]["angle"]["maximum"] == 20
    assert description["rendererData"] == {"maxAngle": 20}
    assert "20" in description["prompt"]
    # An explicit new envelope takes precedence over incompatible legacy data.
    result = binding.parse_control(_control(resource, {"angle": 12.5, "wave": True}), legacy={"portrait": "happy"})
    assert result.reason_code == "READY"
    assert result.control == {
        "version": 1, "resourceId": resource.id, "bindingId": binding.id,
        "state": {"angle": 12.5}, "actions": [{"wave": True}],
    }
    description["rendererData"]["maxAngle"] = 100
    assert binding.description["rendererData"]["maxAngle"] == 20
    assert binding.parse_control(_control(resource, {"angle": 21})).reason_code == "VISUAL_CONTROL_REJECTED"
    assert binding.parse_control(None, legacy={"portrait": "happy"}).reason_code == "VISUAL_CONTROL_REJECTED"
    assert binding.parse_control(_control(resource, {"angle": 0})).control["state"] == {"angle": 0}
    (package / "numeric/resource.json").write_text('{"maxAngle": 30}', encoding="utf-8")
    # Prompt and parser continue using the same snapshot until the host rebinds.
    assert binding.parse_control(_control(resource, {"angle": 25})).reason_code == "VISUAL_CONTROL_REJECTED"
    replacement = host.bind("character", package, resource)
    assert replacement.description["outputSchema"]["properties"]["angle"]["maximum"] == 30
    assert replacement.parse_control(_control(resource, {"angle": 25})).control["state"] == {"angle": 25}


def test_invalid_envelopes_are_isolated_and_old_bindings_expire_on_switch_reload_and_disable(visual_application) -> None:
    application, package, resource = visual_application
    runtime = application.application
    host = runtime.visuals
    binding = host.bind("character", package, resource)
    for envelope in [
        {"version": True, "resourceId": resource.id, "payload": {}},
        {"version": 2, "resourceId": resource.id, "payload": {}},
        {"version": 1, "resourceId": "other", "payload": {}},
        {"version": 1, "resourceId": resource.id, "payload": {}, "pluginId": "other"},
        _control(resource, {"angle": float("nan")}),
        _control(resource, {"angle": "x" * 65536}),
    ]:
        result = binding.parse_control(envelope)
        assert result.control is None
        assert result.reason_code == "VISUAL_CONTROL_INVALID"
    next_binding = host.bind("character", package, resource)
    assert binding.parse_control(_control(resource, {})).reason_code == "VISUAL_BINDING_EXPIRED"
    runtime.reload_plugin("fixture.numeric")
    assert next_binding.parse_control(_control(resource, {})).reason_code == "VISUAL_BINDING_EXPIRED"
    current = host.bind("character", package, resource)
    runtime.unbind_session()
    assert current.parse_control(_control(resource, {})).reason_code == "VISUAL_BINDING_EXPIRED"
    current = host.bind("character", package, resource)
    application.set_enabled(host.candidates(resource.type)[0]["installId"], False)
    assert current.parse_control(_control(resource, {})).reason_code == "VISUAL_BINDING_EXPIRED"
    assert host.candidates(resource.type)[0]["reasonCode"] == "PLUGIN_DISABLED"
    with pytest.raises(VisualHostError, match="PLUGIN_DISABLED"):
        host.bind("character", package, resource, provider_id="fixture.numeric")


def test_missing_provider_and_unknown_resource_keep_files_and_never_activate_fallback(visual_application) -> None:
    application, package, resource = visual_application
    host = application.application.visuals
    original = (package / "character.json").read_text(encoding="utf-8")
    with pytest.raises(VisualHostError, match="VISUAL_PROVIDER_MISSING"):
        host.bind("character", package, resource, provider_id="missing.plugin")
    unknown = CharacterVisualResource("other", "fixture.unknown@1", resource.root, resource.entry)
    assert host.candidates(unknown.type) == []
    with pytest.raises(VisualHostError, match="VISUAL_PROVIDER_MISSING"):
        host.bind("character", package, unknown)
    assert (package / "character.json").read_text(encoding="utf-8") == original
    binding = host.bind("character", package, resource)
    application.close()
    assert binding.parse_control(_control(resource, {})).reason_code == "VISUAL_BINDING_EXPIRED"
    with pytest.raises(VisualHostError, match="VISUAL_BINDING_EXPIRED"):
        host.bind("character", package, resource)


@pytest.mark.parametrize("method,signature,stage", [
    ("describe", "request", "visual.bind"),
    ("parseControl", "request, config, payload, legacy", "visual.parse_control"),
    ("editorData", "resource, raw", "studio.visual.open"),
    ("previewImage", "resource, raw", "studio.visual.previews"),
])
def test_visual_plugin_failures_retain_remote_diagnostics(tmp_path, method, signature, stage):
    import io
    from app.config.character_loader import CharacterRegistry
    from app.core.chat_pipeline import _visual_reply
    from app.core_host.character_studio import CharacterStudioBoundary
    from app.core_host.runtime_logging import install_runtime_logging, CORE_BRIDGE_PREFIX
    from app.llm.chat_reply import ChatReply, ChatSegment

    code = _PLUGIN
    if method == "previewImage":
        code = code.replace("            def describe(self, request):", "            def previewImage(self, resource, raw):\n                return None\n\n            def describe(self, request):")
        code = code.replace('"exportResource"))', '"exportResource", "previewImage"))')
    declaration = f"            def {method}(self, {signature}):\n"
    code = code.replace(declaration, declaration + '                raise TypeError("visual model decoder failed token=private-value")\n')
    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    diagnostic = None
    try:
        with numeric_application(tmp_path, code) as (application, package, resource):
            profile = CharacterRegistry(package.parents[1]).get("character")
            boundary = CharacterStudioBoundary("g", "0123456789abcdef0123456789abcdef", package.parents[1], plugin_application_provider=lambda: application)
            if method == "describe":
                application.application.bind_visual_character(profile)
                for _ in range(3):
                    assert application.application.visual_presentation()["visual"] is None
            elif method == "parseControl":
                binding = application.application.visuals.bind(profile.id, package, resource)
                reply = _visual_reply(ChatReply([ChatSegment(text="still chatting", translation="", tone="", control=_control(resource, {"angle": 1}))]), binding)
                assert reply.segments[0].text == "still chatting"
                assert reply.segments[0].control is None
                binding.close()
                _visual_reply(reply, binding)  # Expired results stay quiet.
            else:
                boundary._dispatch("studio.character.open", {"characterId": "character"})
                if method == "previewImage":
                    assert boundary._dispatch("studio.visual.previews", {"workspaceId": "character"})["items"] == [{"resourceId": resource.id, "relativePath": None}]
                else:
                    result = boundary.handle({"protocolMajor": 2, "protocolMinor": 2, "kind": "request", "id": "test-editor", "name": "studio.visual.open", "generationId": "g", "generationCredential": "0123456789abcdef0123456789abcdef", "priority": "interactive", "deadlineMs": 3000, "payload": {"workspaceId": "character", "resourceId": resource.id}})
                    assert not result["ok"]
                    diagnostic = result["error"]["details"]["diagnostics"]
    finally:
        bridge.close()
    if diagnostic is None:
        records = [json.loads(line[len(CORE_BRIDGE_PREFIX):]) for line in stream.getvalue().splitlines() if line.startswith(CORE_BRIDGE_PREFIX)]
        failures = [record for record in records if record.get("attributes", {}).get("stage") == stage]
        assert len(failures) == 1
        diagnostic = failures[0]["attributes"]
    assert diagnostic["cause_type"] == "TypeError"
    assert diagnostic["stage"] == stage
    assert "visual model decoder failed" in diagnostic["diagnostic"]
    assert "Remote:" in diagnostic["exception_stack"]
    assert method in diagnostic["exception_stack"]
    assert "private-value" not in json.dumps(diagnostic)
