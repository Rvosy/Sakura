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
        for portrait, tone, expected in [("开心", "不满", "开心"), ("unknown", "不满", "不满"), ("", "unknown", "__default__")]:
            assert binding.parse_control(None, legacy={"portrait": portrait, "tone": tone}).control["state"] == {"key": expected}
        assert binding.parse_control({"version": 1, "resourceId": "portrait-default", "payload": {"key": "unknown"}}, segment={"tone": "不满"}).control["state"] == {"key": "不满"}
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
        request("studio.visual.export", {"workspaceId": "a", "resourceId": "portrait-default", "path": str(component)})
        b = request("studio.character.open", {"characterId": "b"})
        after = request("studio.visual.import", {"workspaceId": "b", "path": str(component)})["doc"]
        assert after["id"] == b["doc"]["id"]
        assert after["voice"] == b["doc"]["voice"]
        assert after["cardText"] == b["doc"]["cardText"]
        assert after["visuals"]["default"] != "portrait-default"
        assert (user / "characters/b/character.json").read_bytes() == before
        request("studio.draft.discard", {"workspaceId": "b"})
        assert (user / "characters/b/character.json").read_bytes() == before
    finally:
        application.close()
