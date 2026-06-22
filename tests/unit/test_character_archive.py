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
    THEME_SOURCE_COMPAT_DEFAULT,
    THEME_SOURCE_PACKAGE,
    CharacterRegistry,
    save_character_theme,
)
from app.ui.theme import DEFAULT_THEME_SETTINGS, ThemeSettings


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


def test_character_archive_imports_voice_less_legacy_package_with_default_theme() -> None:
    root = _runtime_root("legacy_no_voice")
    archive_path = root / "legacy.char"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format": ARCHIVE_FORMAT,
                    "version": ARCHIVE_VERSION,
                    "character": {
                        "id": "legacy",
                        "display_name": "Legacy",
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
    assert imported.theme_source == THEME_SOURCE_COMPAT_DEFAULT
    assert "voice" not in manifest
    assert manifest["theme"]["source"] == THEME_SOURCE_COMPAT_DEFAULT
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


def test_character_registry_does_not_rewrite_legacy_theme_on_load() -> None:
    root = _runtime_root("legacy_theme_read")
    profile = _build_voice_less_character(root)
    manifest = json.loads((profile.package_dir / "character.json").read_text(encoding="utf-8"))

    assert profile.theme_settings == DEFAULT_THEME_SETTINGS
    assert profile.theme_source == THEME_SOURCE_COMPAT_DEFAULT
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


def test_character_voice_archive_imports_to_selected_character() -> None:
    root = _runtime_root("voice_import")
    _build_voice_less_character(root)
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
        / "character_archive"
        / name
        / uuid.uuid4().hex
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
