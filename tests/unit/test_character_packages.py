from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from app.config.character_loader import CharacterRegistry
from app.config.character_packages import ensure_legacy_voice_extensions, repair_character_packages


def test_repair_character_packages_upgrades_legacy_voice_manifest(tmp_path: Path) -> None:
    package = _write_package(tmp_path, "legacy", "legacy", with_voice=True)
    issues: list[tuple[object, ...]] = []

    repairs = repair_character_packages(
        tmp_path,
        issue_sink=lambda *args: issues.append(args),
    )

    manifest = json.loads((package / "character.json").read_text(encoding="utf-8"))
    assert "sakura.tts" not in manifest["extensions"]
    assert manifest["extensions"]["sakura.tts.gpt-sovits"]["toneRefs"] == (
        "voice/refs/ref.txt"
    )
    assert (package / "character.json.bak").is_file()
    assert repairs[0].repaired_voice_extension is True
    assert issues[0][2]["reason_code"] == "CHARACTER_LEGACY_VOICE_UPGRADED"


def test_startup_migrates_legacy_voice_choices_without_overwriting_local_settings(tmp_path):
    from app.plugins.sakura_plugin_sdk import PluginConfig
    from plugins.builtin.sakura_tts_hub.plugin import SakuraTTSHub

    old_choices = {
        "enabled": {"enabled": True, "provider": "sakura.tts.genie"},
        "disabled": {"enabled": False, "provider": "sakura.tts.gpt-sovits"},
        "local": {"enabled": True, "provider": "sakura.tts.genie"},
    }
    originals = {}
    for character_id, choice in old_choices.items():
        package = _write_package(tmp_path, character_id, character_id, with_voice=False)
        path = package / "character.json"
        manifest = json.loads(path.read_text())
        manifest["extensions"] = {"sakura.tts": choice}
        path.write_text(json.dumps(manifest), encoding="utf-8")
        originals[path] = path.read_bytes()
    _write_package(tmp_path, "no-choice", "no-choice", with_voice=True)
    config = PluginConfig("sakura.tts", tmp_path / "plugin", tmp_path / "data/plugins/sakura.tts", lambda callback: callback)
    config.update({"custom": 42, "selections": {"local": {"enabled": False, "provider": "example.other"}}})

    repair_character_packages(tmp_path)
    hub = SakuraTTSHub(None, config)
    assert hub.status("enabled")["enabled"] is True
    assert hub.status("enabled")["providerId"] == "sakura.tts.genie"
    assert hub.status("disabled")["enabled"] is False
    assert hub.status("disabled")["providerId"] == "sakura.tts.gpt-sovits"
    assert hub.status("local")["providerId"] == "example.other"
    assert hub.status("local")["enabled"] is False
    assert hub.status("no-choice")["providerId"] is None
    assert config.get()["custom"] == 42
    for path, original in originals.items():
        assert path.read_bytes() == original

    hub.configure("enabled", {"enabled": False, "provider": "sakura.tts.genie"})
    before = (tmp_path / "data/plugins/sakura.tts/config.json").read_bytes()
    repair_character_packages(tmp_path)
    assert (tmp_path / "data/plugins/sakura.tts/config.json").read_bytes() == before
    assert hub.status("enabled")["enabled"] is False


@pytest.mark.parametrize("contents", ['{invalid', '{"selections": []}'])
def test_voice_selection_migration_keeps_invalid_local_config(tmp_path, contents):
    package = _write_package(tmp_path, "demo", "demo", with_voice=False)
    path = package / "character.json"
    manifest = json.loads(path.read_text())
    manifest["extensions"] = {"sakura.tts": {"enabled": True, "provider": "example.voice"}}
    path.write_text(json.dumps(manifest), encoding="utf-8")
    original = path.read_bytes()
    target = tmp_path / "data/plugins/sakura.tts/config.json"
    target.parent.mkdir(parents=True)
    target.write_text(contents, encoding="utf-8")
    issues = []
    repair_character_packages(tmp_path, issue_sink=lambda *args: issues.append(args))
    assert target.read_text() == contents
    assert path.read_bytes() == original
    assert any(event[2].get("stage") == "tts_selection" for event in issues)


def test_voice_selection_migration_write_failure_can_resume(tmp_path, monkeypatch):
    from app.config import character_packages as module
    package = _write_package(tmp_path, "demo", "demo", with_voice=False)
    path = package / "character.json"
    manifest = json.loads(path.read_text())
    manifest["extensions"] = {"sakura.tts": {"enabled": True, "provider": "example.voice"}}
    path.write_text(json.dumps(manifest), encoding="utf-8")
    original = path.read_bytes()
    target = tmp_path / "data/plugins/sakura.tts/config.json"
    with monkeypatch.context() as patch:
        def fail_write(*args, **kwargs):
            raise PermissionError("fixture: local config unavailable")
        patch.setattr(module, "atomic_write_text", fail_write)
        repair_character_packages(tmp_path)
    assert not target.exists()
    assert path.read_bytes() == original
    repair_character_packages(tmp_path)
    assert json.loads(target.read_text())["selections"]["demo"] == manifest["extensions"]["sakura.tts"]


@pytest.mark.parametrize("explicit_genie", [None, {"gptModel": "custom.ckpt", "remoteCharacterName": "remote"}])
def test_partial_voice_migration_preserves_provider_settings_and_is_idempotent(tmp_path: Path, explicit_genie) -> None:
    extensions = {
        "sakura.tts": {"enabled": False, "provider": "sakura.tts.genie"},
        "sakura.tts.gpt-sovits": {"gptModel": "current.ckpt", "toneRefs": "refs.txt"},
        "example.other": {"keep": True},
    }
    if explicit_genie is not None:
        extensions["sakura.tts.genie"] = explicit_genie
    manifest = {"extensions": dict(extensions)}
    changed = ensure_legacy_voice_extensions(manifest, tmp_path)
    assert changed is (explicit_genie is None)
    for key, value in extensions.items():
        assert manifest["extensions"][key] == value
    assert manifest["extensions"]["sakura.tts.genie"] == (explicit_genie or {})
    assert ensure_legacy_voice_extensions(manifest, tmp_path) is False


@pytest.mark.skipif(os.name != "nt", reason="Windows trailing-dot directory semantics")
def test_repair_character_packages_moves_trailing_dot_and_resolves_duplicate_id(
    tmp_path: Path,
) -> None:
    characters = tmp_path / "characters"
    characters.mkdir()
    _write_package(tmp_path, "N.A.V.I_2", "N.A.V.I.")
    _write_package(tmp_path, "N.A.V.I._1", "N.A.V.I._1")
    ghost = Path(_verbatim(characters / "N.A.V.I."))
    try:
        _write_package_at(ghost, "N.A.V.I.", with_voice=True)
        assert "N.A.V.I." in {entry.name for entry in os.scandir(characters)}

        repairs = repair_character_packages(tmp_path, issue_sink=lambda *_args: None)

        names = {entry.name for entry in os.scandir(characters)}
        assert all(not name.endswith((".", " ")) for name in names)
        moved = next(repair for repair in repairs if repair.source_name == "N.A.V.I.")
        assert moved.target_name == "N.A.V.I._2"
        assert moved.character_id == "N.A.V.I._2"
        repaired_manifest = json.loads(
            (characters / moved.target_name / "character.json").read_text(encoding="utf-8")
        )
        assert repaired_manifest["id"] == "N.A.V.I._2"
        assert "sakura.tts" not in repaired_manifest["extensions"]
        assert {profile.id for profile in CharacterRegistry(tmp_path).all()} == {
            "N.A.V.I.",
            "N.A.V.I._1",
            "N.A.V.I._2",
        }
    finally:
        if os.path.exists(ghost):
            shutil.rmtree(ghost)


def _write_package(
    root: Path,
    directory_name: str,
    character_id: str,
    *,
    with_voice: bool = False,
) -> Path:
    package = root / "characters" / directory_name
    _write_package_at(package, character_id, with_voice=with_voice)
    return package


def _write_package_at(package: Path, character_id: str, *, with_voice: bool) -> None:
    (package / "portraits").mkdir(parents=True)
    (package / "portraits" / "default.png").write_bytes(b"portrait")
    (package / "card.md").write_text("system prompt", encoding="utf-8")
    manifest: dict[str, object] = {
        "id": character_id,
        "display_name": character_id,
        "card": "card.md",
        "portrait": {"default": "portraits/default.png"},
    }
    if with_voice:
        (package / "voice" / "refs").mkdir(parents=True)
        (package / "voice" / "refs" / "ref.txt").write_text("", encoding="utf-8")
        manifest["voice"] = {
            "tone_refs": "voice/refs/ref.txt",
            "ref_lang": "ja",
            "text_lang": "ja",
        }
    (package / "character.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _verbatim(path: Path) -> str:
    text = str(path.absolute())
    return text if text.startswith("\\\\?\\") else "\\\\?\\" + text
