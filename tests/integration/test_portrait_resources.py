from __future__ import annotations

import base64
import json
import shutil
from pathlib import Path

import pytest

from app.agent.tools import ToolRegistry
from app.config.character_loader import CharacterRegistry
from app.core_host.plugin_application import PluginApplicationHost
from app.core_host.visual_host import VisualHostError
from app.storage.runtime_roots import RuntimeRoots


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLttAAAAABJRU5ErkJggg==")


@pytest.mark.parametrize("default_path", ["default.png", "./default.png", "portraits\\..\\default.png"])
def test_legacy_portrait_business_is_owned_by_replaceable_plugin(tmp_path: Path, default_path: str) -> None:
    distribution, user = tmp_path / "distribution", tmp_path / "user"
    shutil.copytree(Path(__file__).resolve().parents[2] / "plugins/builtin/sakura_portrait", distribution / "plugins/builtin/sakura_portrait")
    package = user / "characters/demo"
    package.mkdir(parents=True)
    (package / "portraits").mkdir()
    (package / "card.md").write_text("demo", encoding="utf-8")
    for name in ("default.png", "happy.png", "angry.png"):
        (package / name).write_bytes(PNG)
    manifest = {"id": "demo", "display_name": "Demo", "card": "card.md", "portrait": {"default": default_path, "expressions": {"开心": "happy.png", "不满": "angry.png"}}}
    (package / "character.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    before = (package / "character.json").read_text(encoding="utf-8")
    profile = CharacterRegistry(user).get("demo")
    assert profile.current_visual_resource.name == "立绘1"
    assert not hasattr(profile, "default_portrait_path")
    application = PluginApplicationHost(RuntimeRoots(distribution, user), "portraits", ToolRegistry())
    application.start()
    try:
        assert application.application.public_snapshot()["plugins"][0]["state"] == "active", application.application.public_snapshot()
        host = application.application.visuals
        binding = host.bind(profile.id, profile.package_dir, profile.current_visual_resource)
        assert binding.description["assets"] == {"__default__": "default.png", "开心": "happy.png", "不满": "angry.png"}
        for portrait, tone, expected in [("开心", "不满", "开心"), ("", "不满", "不满"), ("", "unknown", "__default__")]:
            assert binding.parse_control(None, legacy={"portrait": portrait, "tone": tone}).control["state"] == {"key": expected}
        assert binding.parse_control({"version": 1, "resourceId": "portrait-default", "payload": {"key": "开心"}}, segment={"tone": "不满"}).control["state"] == {"key": "开心"}
        rejected = binding.parse_control(None, legacy={"portrait": "unknown", "tone": "不满"})
        assert rejected.control is None
        assert rejected.reason_code == "VISUAL_CONTROL_REJECTED"
        for payload in (None, {}, {"key": 123}, {"key": "unknown"}, {"key": "开心", "extra": True}):
            rejected = binding.parse_control({"version": 1, "resourceId": "portrait-default", "payload": payload}, segment={"tone": "不满"})
            assert rejected.control is None, payload
            assert rejected.reason_code == "VISUAL_CONTROL_REJECTED", payload
        application.set_enabled(host.candidates(profile.current_visual_resource.type)[0]["installId"], False)
        assert CharacterRegistry(user).get("demo").id == "demo"
        with pytest.raises(VisualHostError, match="PLUGIN_DISABLED"):
            host.bind(profile.id, package, profile.current_visual_resource)
    finally:
        application.close()
    assert (package / "character.json").read_text(encoding="utf-8") == before
    from app.config.character_archive import export_character_archive, import_character_archive
    target = tmp_path / "legacy.char"
    export_character_archive(profile, target)
    imported = import_character_archive(target, user)
    assert (imported.package_dir / "default.png").read_bytes() == PNG


def test_profile_with_unknown_visual_and_no_images_remains_loadable(tmp_path: Path) -> None:
    package = tmp_path / "characters/numeric"
    package.mkdir(parents=True)
    (package / "card.md").write_text("numeric", encoding="utf-8")
    manifest = {"id": "numeric", "display_name": "Numeric", "card": "card.md", "visuals": {"resources": [{"id": "model", "type": "example.numeric@1", "root": "model", "entry": "resource.json"}], "default": "model"}, "renderer": {"legacy": "ignored"}}
    (package / "character.json").write_text(json.dumps(manifest), encoding="utf-8")
    registry = CharacterRegistry(tmp_path)
    assert registry.load_errors == ()
    assert registry.get("numeric").current_visual_resource.type == "example.numeric@1"
    manifest["visuals"]["resources"][0]["entry"] = "../escape.json"
    (package / "character.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert CharacterRegistry(tmp_path).profiles == {}


def test_portrait_editor_and_component_preserve_target_identity_and_voice(tmp_path):
    from app.core_host.character_studio import CharacterStudioBoundary
    distribution, user = tmp_path / "distribution", tmp_path / "user"
    shutil.copytree(Path(__file__).resolve().parents[2] / "plugins/builtin/sakura_portrait", distribution / "plugins/builtin/sakura_portrait")
    for name in ("a", "b"):
        package = user / "characters" / name
        (package / "voice/refs").mkdir(parents=True)
        (package / "card.md").write_text(f"{name} 的人格", encoding="utf-8")
        (package / "default#1%.png").write_bytes(PNG)
        (package / "voice/refs/ref.txt").write_text("", encoding="utf-8")
        (package / "character.json").write_text(json.dumps({"id": name, "display_name": name, "card": "card.md", "portrait": {"default": "default#1%.png", "expressions": {}}, "voice": {"tone_refs": "voice/refs/ref.txt"}, "unknown": {"Keep_Me": 1}}), encoding="utf-8")
    before = (user / "characters/b/character.json").read_bytes()
    application = PluginApplicationHost(RuntimeRoots(distribution, user), "g", ToolRegistry())
    application.start()
    try:
        boundary = CharacterStudioBoundary("g", "c", user, plugin_application_provider=lambda: application)
        request = boundary._dispatch
        a = request("studio.character.open", {"characterId": "a"})
        previews = request("studio.visual.previews", {"workspaceId": "a"})
        assert previews["items"][0]["relativePath"] == "default#1%.png"
        assert Path(previews["items"][0]["sourcePath"]).read_bytes() == PNG
        editor = request("studio.visual.open", {"workspaceId": "a", "resourceId": "portrait-default"})
        assert editor["data"]["default"] == "default#1%.png"
        component = tmp_path / "portrait.char"
        exported = request("studio.visual.export", {"workspaceId": "a", "resourceId": "portrait-default", "path": str(component)})
        assert Path(exported["outputPath"]) == component.with_suffix(".visual")
        component = Path(exported["outputPath"])
        b = request("studio.character.open", {"characterId": "b"})
        after = request("studio.visual.import", {"workspaceId": "b", "path": str(component)})["doc"]
        assert after["id"] == b["doc"]["id"]
        assert after["voice"] == b["doc"]["voice"]
        assert after["cardText"] == b["doc"]["cardText"]
        assert after["visuals"]["default"] == "portrait-default"
        assert len(after["visuals"]["resources"]) == 2
        reopened = request("studio.character.open", {"characterId": "b"})
        assert reopened["doc"]["visuals"]["default"] == "portrait-default"
        assert (user / "characters/b/character.json").read_bytes() == before
        request("studio.draft.discard", {"workspaceId": "b"})
        assert (user / "characters/b/character.json").read_bytes() == before
    finally:
        application.close()


@pytest.mark.parametrize("autosave", [False, True])
@pytest.mark.parametrize("finish", ["publish", "export"])
def test_v110_unpublished_portrait_draft_survives_editor_and_save(tmp_path, autosave, finish):
    from app.config.character_studio import CharacterStudioService
    from app.core_host.character_studio import CharacterStudioBoundary

    distribution, user = tmp_path / "distribution", tmp_path / "user"
    shutil.copytree(Path(__file__).resolve().parents[2] / "plugins/builtin/sakura_portrait", distribution / "plugins/builtin/sakura_portrait")
    package = user / "characters/demo"
    package.mkdir(parents=True)
    (package / "card.md").write_text("demo", encoding="utf-8")
    (package / "old.png").write_bytes(PNG)
    manifest = {"id": "demo", "display_name": "Demo", "card": "card.md", "portrait": {"default": "old.png", "expressions": {"旧标签": "old.png"}, "custom": 42}}
    original = json.dumps(manifest, ensure_ascii=False)
    (package / "character.json").write_text(original, encoding="utf-8")
    service = CharacterStudioService(user)
    opened = service.open_character("demo")
    draft_package = Path(opened["package_dir"])
    (draft_package / "new.png").write_bytes(PNG)
    # v1.1.0 autosave stores pending edits in draft.json, not character.json.
    state_path = service._state_path("demo")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["dirty"] = True
    state["doc"].pop("visuals", None)
    state["doc"].pop("visual_data", None)
    state["doc"].update(default_portrait="new.png", expressions={"新标签": "new.png"})
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    application = PluginApplicationHost(RuntimeRoots(distribution, user), "g", ToolRegistry())
    application.start()
    try:
        boundary = CharacterStudioBoundary("g", "c", user, plugin_application_provider=lambda: application)
        request = boundary._dispatch
        doc = request("studio.character.open", {"characterId": "demo"})["doc"]
        if autosave:
            doc["displayName"] = "改名"
            doc = request("studio.draft.save", {"workspaceId": "demo", "doc": doc})["doc"]
        previews = request("studio.visual.previews", {"workspaceId": "demo"})
        assert previews["items"][0]["relativePath"] == "new.png"
        editor = request("studio.visual.open", {"workspaceId": "demo", "resourceId": "portrait-default"})
        assert editor["data"] == {"default": "new.png", "expressions": {"新标签": "new.png"}, "custom": 42}
        assert (package / "character.json").read_text(encoding="utf-8") == original
        if finish == "export":
            import zipfile
            component = tmp_path / "pending.visual"
            exported = request("studio.visual.export", {"workspaceId": "demo", "resourceId": "portrait-default", "path": str(component)})
            component = Path(exported["outputPath"])
            with zipfile.ZipFile(component) as archive:
                assert json.loads(archive.read("resource/resource.json"))["default"] == "new.png"
                assert archive.read("resource/new.png") == PNG
            assert (package / "character.json").read_text(encoding="utf-8") == original
            return
        request("studio.character.publish", {"workspaceId": "demo", "doc": doc})
        profile = CharacterRegistry(user).get("demo")
        binding = application.application.visuals.bind(profile.id, package, profile.current_visual_resource)
        assert binding.description["assets"] == {"__default__": "new.png", "新标签": "new.png"}
    finally:
        application.close()


@pytest.mark.parametrize("failure", ["json", "png", "missing"])
def test_portrait_resource_error_logs_target_and_original_cause(tmp_path, failure):
    import io
    from app.core_host.runtime_logging import install_runtime_logging, CORE_BRIDGE_PREFIX
    distribution, user = tmp_path / "distribution", tmp_path / "user"
    shutil.copytree(Path(__file__).resolve().parents[2] / "plugins/builtin/sakura_portrait", distribution / "plugins/builtin/sakura_portrait")
    package = user / "characters/demo"
    (package / "visual").mkdir(parents=True)
    (package / "card.md").write_text("demo", encoding="utf-8")
    (package / "character.json").write_text(json.dumps({"id": "demo", "display_name": "Demo", "card": "card.md", "visuals": {"resources": [{"id": "portrait", "type": "sakura.visual.portrait@1", "root": "visual", "entry": "resource.json"}], "default": "portrait"}}), encoding="utf-8")
    config = "{broken json" if failure == "json" else json.dumps({"default": "default.png", "expressions": {"开心": "happy.png"}}, ensure_ascii=False)
    (package / "visual/resource.json").write_text(config, encoding="utf-8")
    (package / "visual/default.png").write_bytes(PNG)
    if failure == "png":
        (package / "visual/happy.png").write_bytes(b"broken PNG" * 5)
    stream = io.BytesIO()
    bridge = install_runtime_logging(stream)
    application = PluginApplicationHost(RuntimeRoots(distribution, user), "g", ToolRegistry())
    try:
        application.start()
        application.application.bind_visual_character(CharacterRegistry(user).get("demo"))
        assert application.application.visual_presentation()["visualReasonCode"] == "VISUAL_RESOURCE_INVALID"
    finally:
        application.close()
        bridge.close()
    records = [json.loads(line[len(CORE_BRIDGE_PREFIX):]) for line in stream.getvalue().splitlines() if line.startswith(CORE_BRIDGE_PREFIX)]
    record = next(record for record in records if record.get("plugin_id") == "sakura.portrait" and record.get("attributes", {}).get("stage") == "visual.describe")
    if failure == "json":
        assert record["attributes"]["error_type"] == "JSONDecodeError"
        assert "Expecting property name" in record["attributes"]["diagnostic"]
        assert record["attributes"]["path"] == "visual/resource.json"
    else:
        assert "开心" in record["message"]
        assert record["attributes"]["path"] == "visual/happy.png"
        if failure == "png":
            assert "PNG/IHDR" in record["attributes"]["diagnostic"]
        else:
            assert "FileNotFoundError" in record["attributes"]["exception_chain"]
    assert "describe" in record["attributes"]["exception_stack"]


def test_portrait_encoded_size_does_not_limit_small_decoded_image(tmp_path: Path) -> None:
    import importlib.util
    source = Path(__file__).resolve().parents[2] / "plugins/builtin/sakura_portrait/plugin.py"
    spec = importlib.util.spec_from_file_location("portrait_size_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    image = tmp_path / "portrait.png"
    byte_length = 16 * 1024 * 1024 + 1
    with image.open("wb") as stream:
        stream.write(PNG)
        stream.truncate(byte_length)
    assert module.inspect_png(image) == {"width": 1, "height": 1, "byteLength": byte_length}


def test_large_portrait_collection_publishes_and_binds_without_truncation(tmp_path):
    from app.core_host.character_studio import CharacterStudioBoundary
    distribution, user = tmp_path / "distribution", tmp_path / "user"
    shutil.copytree(Path(__file__).resolve().parents[2] / "plugins/builtin/sakura_portrait", distribution / "plugins/builtin/sakura_portrait")
    package = user / "characters/demo"
    package.mkdir(parents=True)
    (package / "card.md").write_text("demo")
    (package / "default.png").write_bytes(PNG)
    expressions = {f"表情{i}-" + "长标签" * 30: "default.png" for i in range(300)}
    (package / "character.json").write_text(json.dumps({"id": "demo", "display_name": "Demo", "card": "card.md", "portrait": {"default": "default.png", "expressions": expressions}}, ensure_ascii=False))
    application = PluginApplicationHost(RuntimeRoots(distribution, user), "large-portraits", ToolRegistry())
    application.start()
    try:
        boundary = CharacterStudioBoundary("g", "c", user, plugin_application_provider=lambda: application)
        opened = boundary._dispatch("studio.character.open", {"characterId": "demo"})
        boundary._dispatch("studio.character.publish", {"workspaceId": "demo", "doc": opened["doc"]})
        profile = CharacterRegistry(user).get("demo")
        binding = application.application.visuals.bind(profile.id, profile.package_dir, profile.current_visual_resource)
        assert set(binding.description["assets"]) == {"__default__", *expressions}
        assert len(json.dumps(binding.description, ensure_ascii=False).encode()) > 65536
        key = next(reversed(expressions))
        result = binding.parse_control({"version": 1, "resourceId": profile.current_visual_resource.id, "payload": {"key": key}})
        assert result.control["state"] == {"key": key}
    finally:
        application.close()
