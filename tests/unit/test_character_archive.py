from __future__ import annotations

import json
import uuid
import zipfile
from pathlib import Path

import pytest

from app.config.character_archive import (
    ARCHIVE_FORMAT,
    ARCHIVE_VERSION,
    VOICE_ARCHIVE_FORMAT,
    VOICE_ARCHIVE_VERSION,
    CharacterArchiveError,
    export_character_archive,
    export_character_voice_archive,
    import_character_archive,
    import_character_voice_archive,
)
from app.config.character_loader import (
    THEME_SOURCE_PACKAGE,
    CharacterRegistry,
    save_character_theme,
)
from app.config.models import DEFAULT_THEME_SETTINGS, ThemeSettings
from app.storage.paths import sanitize_directory_component


def test_character_archive_export_then_import_roundtrip() -> None:
    root = _runtime_root("roundtrip")
    source_root = root / "source"
    profile = _build_character_package(source_root)
    archive_path = root / "demo.char"

    export_character_archive(profile, archive_path)
    result = import_character_archive(archive_path, source_root)

    assert result.character_id == "demo_1"
    assert result.display_name == "Demo（1）"

    imported = CharacterRegistry(source_root).get(result.character_id)
    assert imported.display_name == "Demo（1）"
    assert imported.initial_message == "hello"
    assert imported.card_path.read_text(encoding="utf-8") == "system prompt"
    assert imported.default_portrait_path.name == "default.png"
    assert imported.expression_portraits["开心"].name == "happy.png"
    assert imported.reply_tones == ["中性", "开心"]
    assert imported.voice is not None
    assert imported.voice.gpt_model_path is not None
    assert imported.voice.sovits_model_path is not None
    assert imported.voice.gpt_model_path.is_file()
    assert imported.voice.sovits_model_path.is_file()
    assert imported.voice.tone_ref_path.read_text(encoding="utf-8").strip().endswith("|中性")
    assert (imported.package_dir / "voice" / "refs" / "tone_refs" / "neutral.wav").is_file()
    imported_manifest = json.loads(
        (imported.package_dir / "character.json").read_text(encoding="utf-8")
    )
    assert imported_manifest["extensions"]["sakura.tts"] == {
        "enabled": True,
        "provider": "sakura.tts.gpt-sovits",
    }
    assert imported_manifest["extensions"]["sakura.tts.gpt-sovits"]["gptModel"] == (
        "voice/models/gpt.ckpt"
    )


def test_character_archive_uses_portable_directory_and_manifest_id_uniqueness() -> None:
    root = _runtime_root("portable_character_directory")
    archive_path = _build_minimal_character_archive(root, "N.A.V.I.")

    first = import_character_archive(archive_path, root)
    second = import_character_archive(archive_path, root)

    assert first.character_id == "N.A.V.I."
    assert first.package_dir.name == sanitize_directory_component("N.A.V.I.")
    assert not first.package_dir.name.endswith((".", " "))
    assert second.character_id == "N.A.V.I._1"
    assert second.package_dir.name == "N.A.V.I._1"
    assert {profile.id for profile in CharacterRegistry(root).all()} == {
        "N.A.V.I.",
        "N.A.V.I._1",
    }


def test_character_archive_manifest_uses_sakura_format() -> None:
    root = _runtime_root("manifest")
    profile = _build_character_package(root / "source")
    archive_path = root / "demo.char"

    export_character_archive(profile, archive_path)

    with zipfile.ZipFile(archive_path, "r") as zf:
        manifest = json.loads(zf.read("manifest.json"))
        names = set(zf.namelist())

    assert manifest["format"] == ARCHIVE_FORMAT
    assert manifest["version"] == ARCHIVE_VERSION
    assert manifest["character"]["card"] == "character/card.md"
    assert manifest["character"]["portrait"]["default"] == "character/portraits/default.png"
    assert manifest["character"]["voice"]["tone_refs"] == "character/voice/refs/ref.txt"
    assert "character/voice/models/gpt.ckpt" in names
    assert "character/voice/refs/tone_refs/neutral.wav" in names


def test_character_archive_roundtrips_opaque_plugin_extensions() -> None:
    root = _runtime_root("opaque_extensions")
    profile = _build_character_package(root / "source")
    manifest_path = profile.package_dir / "character.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {
        "com.example.unknown": {
            "nested": {"revision": 3},
            "items": ["alpha", 2, False, None],
        },
        "sakura.tts": {"enabled": True, "provider": "com.example.unknown"},
    }
    manifest["extensions"] = expected
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    archive_path = root / "opaque.char"

    export_character_archive(CharacterRegistry(root / "source").get("demo"), archive_path)
    result = import_character_archive(archive_path, root / "source")

    imported = json.loads(
        (result.package_dir / "character.json").read_text(encoding="utf-8")
    )
    for plugin_id, value in expected.items():
        assert imported["extensions"][plugin_id] == value
    with zipfile.ZipFile(archive_path, "r") as zf:
        public_manifest = json.loads(zf.read("manifest.json"))
        package_manifest = json.loads(zf.read("character/character.json"))
    assert public_manifest["character"]["extensions"] == expected
    assert package_manifest["extensions"] == expected


def test_character_archive_roundtrips_runtime_fields_and_extension_voice_resources() -> None:
    root = _runtime_root("runtime_fields")
    source_root = root / "source"
    profile = _build_character_package(source_root)
    package = profile.package_dir
    (package / "backchannel.json").write_text('{"version":1}', encoding="utf-8")
    (package / "voice" / "models" / "extension.ckpt").write_bytes(b"extension-gpt")
    manifest_path = package / "character.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "renderer": {"kind": "live2d", "futureRendererField": 3},
            "backchannel": "backchannel.json",
            "futureTop": {"enabled": True},
            "extensions": {
                "sakura.tts": {
                    "enabled": True,
                    "provider": "sakura.tts.gpt-sovits",
                },
                "sakura.tts.gpt-sovits": {
                    "toneRefs": "voice/refs/ref.txt",
                    "gptModel": "voice/models/extension.ckpt",
                    "sovitsModel": "voice/models/sovits.pth",
                    "refLang": "ja",
                    "textLang": "ja",
                },
            },
        }
    )
    manifest["reply"]["futureMode"] = "keep"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    archive_path = root / "runtime-fields.char"

    export_character_archive(CharacterRegistry(source_root).get("demo"), archive_path)
    imported = import_character_archive(archive_path, source_root)

    imported_manifest = json.loads(
        (imported.package_dir / "character.json").read_text(encoding="utf-8")
    )
    assert imported_manifest["renderer"] == manifest["renderer"]
    assert imported_manifest["backchannel"] == "backchannel.json"
    assert imported_manifest["futureTop"] == {"enabled": True}
    assert imported_manifest["reply"]["futureMode"] == "keep"
    assert imported_manifest["extensions"]["sakura.tts.genie"] == {}
    assert (
        imported.package_dir / imported_manifest["extensions"]["sakura.tts.gpt-sovits"]["gptModel"]
    ).read_bytes() == b"extension-gpt"
    with zipfile.ZipFile(archive_path) as bundle:
        public_manifest = json.loads(bundle.read("manifest.json"))["character"]
        names = set(bundle.namelist())
    assert public_manifest["renderer"] == manifest["renderer"]
    assert public_manifest["backchannel"] == "character/backchannel.json"
    assert "character/voice/models/extension.ckpt" in names


def test_extension_voice_export_accepts_a_relative_package_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _runtime_root("relative_extension_voice")
    source_root = root / "source"
    profile = _build_character_package(source_root)
    manifest_path = profile.package_dir / "character.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["extensions"] = {
        "sakura.tts": {
            "enabled": True,
            "provider": "sakura.tts.gpt-sovits",
        },
        "sakura.tts.gpt-sovits": {
            "toneRefs": "voice/refs/ref.txt",
            "gptModel": "voice/models/gpt.ckpt",
            "sovitsModel": "voice/models/sovits.pth",
        },
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.chdir(root.parent)
    relative_root = Path(root.name) / "source"
    relative_profile = CharacterRegistry(relative_root).get("demo")
    archive_path = Path(root.name) / "relative.char"

    export_character_archive(relative_profile, archive_path)

    with zipfile.ZipFile(archive_path) as bundle:
        assert "character/voice/refs/tone_refs/neutral.wav" in bundle.namelist()


def test_character_archive_export_can_cancel_during_large_file_compression() -> None:
    root = _runtime_root("cancel_export")
    profile = _build_character_package(root)
    (profile.package_dir / "large-resource.bin").write_bytes(b"x" * (3 * 1024 * 1024))
    output = root / "cancelled.char"
    checkpoints = 0

    def cancel() -> None:
        nonlocal checkpoints
        checkpoints += 1
        if checkpoints >= 5:
            raise RuntimeError("cancel export")

    with pytest.raises(RuntimeError, match="cancel export"):
        export_character_archive(profile, output, cancel_check=cancel)

    assert not output.exists()
    assert not output.with_name(f".{output.name}.tmp").exists()



def test_character_archive_export_only_includes_referenced_voice_files() -> None:
    root = _runtime_root("referenced_voice_export")
    profile = _build_character_package(root / "source")
    (profile.package_dir / "voice" / "models" / "old.ckpt").write_bytes(b"old-gpt")
    (profile.package_dir / "voice" / "refs" / "tone_refs" / "old.wav").write_bytes(b"old-wav")
    archive_path = root / "demo.char"

    export_character_archive(profile, archive_path)

    with zipfile.ZipFile(archive_path, "r") as zf:
        names = set(zf.namelist())

    assert "character/voice/models/gpt.ckpt" in names
    assert "character/voice/models/sovits.pth" in names
    assert "character/voice/refs/ref.txt" in names
    assert "character/voice/refs/tone_refs/neutral.wav" in names
    assert "character/voice/models/old.ckpt" not in names
    assert "character/voice/refs/tone_refs/old.wav" not in names


def test_character_voice_archive_export_only_includes_referenced_files() -> None:
    root = _runtime_root("referenced_voice_only_export")
    profile = _build_character_package(root / "source")
    (profile.package_dir / "voice" / "models" / "old.ckpt").write_bytes(b"old-gpt")
    (profile.package_dir / "voice" / "refs" / "tone_refs" / "old.wav").write_bytes(b"old-wav")
    archive_path = root / "demo.voice"

    export_character_voice_archive(profile, archive_path)

    with zipfile.ZipFile(archive_path, "r") as zf:
        names = set(zf.namelist())

    assert "voice/models/gpt.ckpt" in names
    assert "voice/models/sovits.pth" in names
    assert "voice/refs/ref.txt" in names
    assert "voice/refs/tone_refs/neutral.wav" in names
    assert "voice/models/old.ckpt" not in names
    assert "voice/refs/tone_refs/old.wav" not in names

def test_character_archive_card_only_export_excludes_voice() -> None:
    root = _runtime_root("card_only_export")
    profile = _build_character_package(root / "source")
    archive_path = root / "demo.card.char"

    export_character_archive(profile, archive_path, include_voice=False)

    with zipfile.ZipFile(archive_path, "r") as zf:
        manifest = json.loads(zf.read("manifest.json"))
        packaged_character = json.loads(zf.read("character/character.json"))
        names = set(zf.namelist())

    assert "voice" not in manifest["character"]
    assert "voice" not in packaged_character
    assert not any(name.startswith("character/voice/") for name in names)

    result = import_character_archive(archive_path, root)
    imported = CharacterRegistry(root).get(result.character_id)
    assert imported.voice is None


def test_character_archive_imports_voice_less_package_with_default_theme() -> None:
    root = _runtime_root("voice_less_default_theme")
    archive_path = root / "voice-less.char"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format": ARCHIVE_FORMAT,
                    "version": ARCHIVE_VERSION,
                    "character": {
                        "id": "voice-less",
                        "display_name": "Voice-less",
                        "initial_message": "hello",
                        "card": "character/card.md",
                        "portrait": {"default": "character/portrait.png"},
                    },
                },
                ensure_ascii=False,
            ),
        )
        zf.writestr("character/card.md", "system prompt")
        zf.writestr("character/portrait.png", b"portrait")

    result = import_character_archive(archive_path, root)
    imported = CharacterRegistry(root).get(result.character_id)
    manifest = json.loads((imported.package_dir / "character.json").read_text(encoding="utf-8"))

    assert imported.voice is None
    assert imported.theme_settings == DEFAULT_THEME_SETTINGS
    assert imported.theme_source == THEME_SOURCE_PACKAGE
    assert "voice" not in manifest
    assert manifest["theme"]["source"] == THEME_SOURCE_PACKAGE
    assert manifest["theme"]["primary_color"] == DEFAULT_THEME_SETTINGS.primary_color


def test_character_archive_preserves_packaged_theme_on_import_and_export() -> None:
    root = _runtime_root("packaged_theme")
    archive_path = root / "themed.char"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format": ARCHIVE_FORMAT,
                    "version": ARCHIVE_VERSION,
                    "character": {
                        "id": "themed",
                        "display_name": "Themed",
                        "card": "character/card.md",
                        "portrait": {"default": "character/portrait.png"},
                        "theme": {
                            "primary_color": "#112233",
                            "accent_color": "#445566",
                            "source": THEME_SOURCE_PACKAGE,
                        },
                    },
                },
                ensure_ascii=False,
            ),
        )
        zf.writestr("character/card.md", "system prompt")
        zf.writestr("character/portrait.png", b"portrait")

    result = import_character_archive(archive_path, root)
    imported = CharacterRegistry(root).get(result.character_id)
    exported_path = root / "exported.char"
    export_character_archive(imported, exported_path)

    with zipfile.ZipFile(exported_path, "r") as zf:
        exported_manifest = json.loads(zf.read("manifest.json"))

    assert imported.theme_source == THEME_SOURCE_PACKAGE
    assert imported.theme_settings.primary_color == "#112233"
    assert imported.theme_settings.accent_color == "#445566"
    assert exported_manifest["character"]["theme"]["source"] == THEME_SOURCE_PACKAGE
    assert exported_manifest["character"]["theme"]["primary_color"] == "#112233"


def test_character_archive_ignores_legacy_theme_source_on_import() -> None:
    root = _runtime_root("legacy_theme_source")
    archive_path = root / "legacy-themed.char"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format": ARCHIVE_FORMAT,
                    "version": ARCHIVE_VERSION,
                    "character": {
                        "id": "legacy-themed",
                        "display_name": "Legacy themed",
                        "card": "character/card.md",
                        "portrait": {"default": "character/portrait.png"},
                        "theme": {
                            "primary_color": "#112233",
                            "accent_color": "#445566",
                            "source": "compat_default",
                        },
                    },
                },
                ensure_ascii=False,
            ),
        )
        zf.writestr("character/card.md", "system prompt")
        zf.writestr("character/portrait.png", b"portrait")

    result = import_character_archive(archive_path, root)
    imported = CharacterRegistry(root).get(result.character_id)
    manifest = json.loads((imported.package_dir / "character.json").read_text(encoding="utf-8"))

    assert imported.theme_settings.primary_color == "#112233"
    assert imported.theme_settings.accent_color == "#445566"
    assert imported.theme_source == THEME_SOURCE_PACKAGE
    assert manifest["theme"]["source"] == THEME_SOURCE_PACKAGE


def test_character_registry_uses_current_default_for_optional_theme() -> None:
    root = _runtime_root("optional_theme_read")
    profile = _build_voice_less_character(root)
    manifest = json.loads((profile.package_dir / "character.json").read_text(encoding="utf-8"))

    assert profile.theme_settings == DEFAULT_THEME_SETTINGS
    assert profile.theme_source == THEME_SOURCE_PACKAGE
    assert "theme" not in manifest


def test_save_character_theme_writes_package_theme_to_manifest() -> None:
    root = _runtime_root("save_character_theme")
    profile = _build_voice_less_character(root)
    settings = ThemeSettings(primary_color="#112233", accent_color="#445566")

    save_character_theme(profile, settings)

    manifest = json.loads((profile.package_dir / "character.json").read_text(encoding="utf-8"))
    saved_theme = manifest["theme"]
    assert saved_theme["source"] == THEME_SOURCE_PACKAGE
    assert saved_theme["primary_color"] == "#112233"
    assert saved_theme["accent_color"] == "#445566"
    assert "ai_enabled" not in saved_theme


@pytest.mark.parametrize("existing_extensions", [False, True])
def test_character_voice_archive_imports_to_selected_character(existing_extensions: bool) -> None:
    root = _runtime_root("voice_import")
    profile = _build_voice_less_character(root)
    if existing_extensions:
        manifest_path = profile.package_dir / "character.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["extensions"] = {
            "sakura.tts": {"enabled": True, "provider": "sakura.tts.genie"},
            "sakura.tts.gpt-sovits": {"gptModel": "old.ckpt", "sovitsModel": "old.pth", "custom": 7},
            "sakura.tts.genie": {"refLang": "zh", "remoteCharacterName": "explicit"},
        }
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    archive_path = _build_voice_archive(root)

    result = import_character_voice_archive(archive_path, root, "demo")
    imported = CharacterRegistry(root).get(result.character_id)
    manifest = json.loads((imported.package_dir / "character.json").read_text(encoding="utf-8"))

    assert imported.voice is not None
    assert imported.voice.gpt_model_path is not None
    assert imported.voice.sovits_model_path is not None
    assert imported.voice.gpt_model_path.read_bytes() == b"gpt-new"
    assert imported.voice.sovits_model_path.read_bytes() == b"sovits-new"
    assert imported.voice.tone_ref_path.read_text(encoding="utf-8").strip().endswith("|开心")
    assert manifest["voice"]["tone_refs"] == "voice/refs/ref.txt"
    assert manifest["voice"]["ref_lang"] == "ja"
    assert manifest["extensions"]["sakura.tts"]["enabled"] is True
    assert manifest["extensions"]["sakura.tts.gpt-sovits"]["sovitsModel"] == (
        "voice/models/sovits.pth"
    )
    if existing_extensions:
        from plugins.builtin.sakura_genie.plugin import _effective_voice_extension
        assert manifest["extensions"]["sakura.tts"]["provider"] == "sakura.tts.genie"
        genie = manifest["extensions"]["sakura.tts.genie"]
        assert genie == {"refLang": "zh", "remoteCharacterName": "explicit"}
        assert _effective_voice_extension(manifest, genie)["gptModel"] == "voice/models/gpt.ckpt"
        assert manifest["extensions"]["sakura.tts.gpt-sovits"]["custom"] == 7


def test_character_voice_archive_export_can_be_imported() -> None:
    root = _runtime_root("voice_export")
    source_root = root / "source"
    target_root = root / "target"
    profile = _build_character_package(source_root)
    _build_voice_less_character(target_root)
    archive_path = root / "demo.voice"

    export_character_voice_archive(profile, archive_path)

    with zipfile.ZipFile(archive_path, "r") as zf:
        manifest = json.loads(zf.read("manifest.json"))
        names = set(zf.namelist())

    assert manifest["format"] == VOICE_ARCHIVE_FORMAT
    assert manifest["version"] == VOICE_ARCHIVE_VERSION
    assert manifest["voice"]["tone_refs"] == "voice/refs/ref.txt"
    assert "voice/models/gpt.ckpt" in names
    assert "voice/refs/tone_refs/neutral.wav" in names
    assert not any(name.startswith("character/") for name in names)

    result = import_character_voice_archive(archive_path, target_root, "demo")
    imported = CharacterRegistry(target_root).get(result.character_id)
    assert imported.voice is not None
    assert imported.voice.gpt_model_path.read_bytes() == b"gpt"


def test_character_voice_archive_export_requires_voice() -> None:
    root = _runtime_root("voice_export_missing")
    profile = _build_voice_less_character(root)

    with pytest.raises(CharacterArchiveError, match="没有可导出的语音包"):
        export_character_voice_archive(profile, root / "demo.voice")


def test_character_voice_archive_failure_keeps_existing_voice() -> None:
    root = _runtime_root("voice_import_rollback")
    profile = _build_character_package(root)
    archive_path = root / "bad.voice"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format": VOICE_ARCHIVE_FORMAT,
                    "version": VOICE_ARCHIVE_VERSION,
                    "voice": {
                        "tone_refs": "voice/refs/ref.txt",
                        "gpt_model": "voice/models/missing.ckpt",
                    },
                }
            ),
        )
        zf.writestr("voice/refs/ref.txt", "voice/refs/tone_refs/new.wav|JA|hello|中性\n")
        zf.writestr("voice/refs/tone_refs/new.wav", b"wav-new")

    original_manifest = (profile.package_dir / "character.json").read_text(encoding="utf-8")
    original_gpt = (profile.package_dir / "voice" / "models" / "gpt.ckpt").read_bytes()

    with pytest.raises(CharacterArchiveError):
        import_character_voice_archive(archive_path, root, "demo")

    assert (profile.package_dir / "character.json").read_text(encoding="utf-8") == original_manifest
    assert (profile.package_dir / "voice" / "models" / "gpt.ckpt").read_bytes() == original_gpt


def test_character_voice_archive_rejects_unsafe_zip_and_missing_target() -> None:
    root = _runtime_root("voice_import_bad")
    _build_voice_less_character(root)
    archive_path = root / "bad.voice"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("../evil.txt", "evil")
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format": VOICE_ARCHIVE_FORMAT,
                    "version": VOICE_ARCHIVE_VERSION,
                    "voice": {"tone_refs": "voice/refs/ref.txt"},
                }
            ),
        )

    with pytest.raises(CharacterArchiveError):
        import_character_voice_archive(archive_path, root, "demo")
    with pytest.raises(CharacterArchiveError, match="目标角色不存在"):
        import_character_voice_archive(_build_voice_archive(root), root, "missing")

    assert not (root / "evil.txt").exists()


def test_character_archive_rejects_resource_limit_violations(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.config.character_archive as archive_module

    root = _runtime_root("resource_limits")
    archive_path = root / "too-many.char"
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("manifest.json", "{}")
        zf.writestr("character/a.txt", "a")
        zf.writestr("character/b.txt", "b")
    monkeypatch.setattr(archive_module, "MAX_ARCHIVE_MEMBERS", 2)

    with pytest.raises(CharacterArchiveError, match="文件数量过多"):
        import_character_archive(archive_path, root)


def test_character_archive_rejects_extreme_compression_ratio(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.config.character_archive as archive_module

    root = _runtime_root("compression_ratio")
    archive_path = root / "bomb.char"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", "{}")
        zf.writestr("character/bomb.bin", b"0" * (2 * 1024 * 1024))
    monkeypatch.setattr(archive_module, "MAX_ARCHIVE_COMPRESSION_RATIO", 2)

    with pytest.raises(CharacterArchiveError, match="压缩比异常"):
        import_character_archive(archive_path, root)


def test_character_archive_rejects_non_sakura_format() -> None:
    root = _runtime_root("non_sakura")
    archive_path = root / "legacy.char"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"format": "shinsekai.character"}))
        zf.writestr("character/card.md", "legacy")

    with pytest.raises(CharacterArchiveError, match="不支持"):
        import_character_archive(archive_path, root)

    assert not list((root / "characters").glob("*/character.json"))


def test_character_archive_rejects_zip_path_traversal() -> None:
    root = _runtime_root("zip_traversal")
    archive_path = root / "bad.char"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("../evil.txt", "evil")
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format": ARCHIVE_FORMAT,
                    "version": ARCHIVE_VERSION,
                    "character": {
                        "id": "bad",
                        "display_name": "Bad",
                        "card": "character/card.md",
                        "portrait": {"default": "character/portrait.png"},
                    },
                }
            ),
        )

    with pytest.raises(CharacterArchiveError):
        import_character_archive(archive_path, root)

    assert not (root / "evil.txt").exists()
    assert not list((root / "characters").glob("*/character.json"))


def test_character_archive_rejects_unsafe_manifest_resource_path() -> None:
    root = _runtime_root("bad_manifest")
    archive_path = root / "bad_manifest.char"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format": ARCHIVE_FORMAT,
                    "version": ARCHIVE_VERSION,
                    "character": {
                        "id": "bad",
                        "display_name": "Bad",
                        "card": "character/card.md",
                        "portrait": {"default": "character/../portrait.png"},
                    },
                }
            ),
        )
        zf.writestr("character/card.md", "prompt")
        zf.writestr("character/portrait.png", b"png")

    with pytest.raises(CharacterArchiveError):
        import_character_archive(archive_path, root)

    assert not list((root / "characters").glob("*/character.json"))


def _runtime_root(name: str) -> Path:
    root = (
        Path(__file__).resolve().parents[2]
        / "temp"
        / "test_runtime"
        / uuid.uuid4().hex
        / "character_archive"
        / name
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def _build_voice_less_character(root: Path):
    character_dir = root / "characters" / "demo"
    character_dir.mkdir(parents=True)
    (character_dir / "card.md").write_text("system prompt", encoding="utf-8")
    (character_dir / "portrait.png").write_bytes(b"portrait")
    (character_dir / "character.json").write_text(
        json.dumps(
            {
                "id": "demo",
                "display_name": "Demo",
                "initial_message": "hello",
                "card": "card.md",
                "portrait": {
                    "default": "portrait.png",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return CharacterRegistry(root).get("demo")


def _build_voice_archive(root: Path) -> Path:
    archive_path = root / f"demo_{uuid.uuid4().hex}.voice"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format": VOICE_ARCHIVE_FORMAT,
                    "version": VOICE_ARCHIVE_VERSION,
                    "voice": {
                        "tone_refs": "voice/refs/ref.txt",
                        "gpt_model": "voice/models/gpt.ckpt",
                        "sovits_model": "voice/models/sovits.pth",
                        "ref_lang": "ja",
                        "text_lang": "ja",
                    },
                },
                ensure_ascii=False,
            ),
        )
        zf.writestr("voice/models/gpt.ckpt", b"gpt-new")
        zf.writestr("voice/models/sovits.pth", b"sovits-new")
        zf.writestr("voice/refs/tone_refs/happy.wav", b"wav-new")
        zf.writestr("voice/refs/ref.txt", "voice/refs/tone_refs/happy.wav|JA|hello|开心\n")
    return archive_path


def _build_minimal_character_archive(root: Path, character_id: str) -> Path:
    archive_path = root / f"minimal_{uuid.uuid4().hex}.char"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format": ARCHIVE_FORMAT,
                    "version": ARCHIVE_VERSION,
                    "character": {
                        "id": character_id,
                        "display_name": character_id,
                        "card": "character/card.md",
                        "portrait": {"default": "character/portrait.png"},
                    },
                }
            ),
        )
        zf.writestr("character/card.md", "system prompt")
        zf.writestr("character/portrait.png", b"portrait")
    return archive_path


def _build_character_package(root: Path):
    character_dir = root / "characters" / "demo"
    (character_dir / "portraits").mkdir(parents=True)
    (character_dir / "voice" / "models").mkdir(parents=True)
    (character_dir / "voice" / "refs" / "tone_refs").mkdir(parents=True)
    (character_dir / "card.md").write_text("system prompt", encoding="utf-8")
    (character_dir / "portraits" / "default.png").write_bytes(b"default")
    (character_dir / "portraits" / "happy.png").write_bytes(b"happy")
    (character_dir / "voice" / "models" / "gpt.ckpt").write_bytes(b"gpt")
    (character_dir / "voice" / "models" / "sovits.pth").write_bytes(b"sovits")
    (character_dir / "voice" / "refs" / "tone_refs" / "neutral.wav").write_bytes(b"wav")
    (character_dir / "voice" / "refs" / "ref.txt").write_text(
        "voice/refs/tone_refs/neutral.wav|JA|hello|中性\n",
        encoding="utf-8",
    )
    (character_dir / "character.json").write_text(
        json.dumps(
            {
                "id": "demo",
                "display_name": "Demo",
                "initial_message": "hello",
                "card": "card.md",
                "portrait": {
                    "default": "portraits/default.png",
                    "expressions": {
                        "站立待机": "portraits/default.png",
                        "开心": "portraits/happy.png",
                    },
                },
                "voice": {
                    "gpt_model": "voice/models/gpt.ckpt",
                    "sovits_model": "voice/models/sovits.pth",
                    "tone_refs": "voice/refs/ref.txt",
                    "ref_lang": "ja",
                    "text_lang": "ja",
                },
                "reply": {
                    "tones": ["中性", "开心"],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return CharacterRegistry(root).get("demo")
