from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from app.config.character_loader import CharacterRegistry


def _runtime_root(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    root.mkdir(parents=True)
    return root


def _write_character(root: Path, character_id: str = "sakura", display_name: str = "Sakura") -> Path:
    package_dir = root / "characters" / character_id
    (package_dir / "portraits").mkdir(parents=True)
    (package_dir / "voice" / "refs").mkdir(parents=True)
    (package_dir / "card.md").write_text("old card", encoding="utf-8")
    (package_dir / "portraits" / "default.png").write_bytes(b"png")
    (package_dir / "voice" / "refs" / "ref.txt").write_text("", encoding="utf-8")
    (package_dir / "character.json").write_text(
        json.dumps(
            {
                "id": character_id,
                "display_name": display_name,
                "initial_message": "hello",
                "card": "card.md",
                "portrait": {
                    "default": "portraits/default.png",
                    "expressions": {"开心": "portraits/default.png"},
                },
                "reply": {"tones": ["温柔"]},
                "theme": {
                    "source": "package",
                    "primary_color": "#112233",
                    "accent_color": "#445566",
                },
                "voice": {"tone_refs": "voice/refs/ref.txt", "ref_lang": "ja", "text_lang": "ja"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return package_dir


def test_character_studio_lists_characters_and_marks_current(tmp_path: Path) -> None:
    from app.config.character_studio import CharacterStudioService

    root = _runtime_root(tmp_path, "list")
    _write_character(root, "sakura", "Sakura")
    _write_character(root, "rin", "Rin")

    service = CharacterStudioService(root)
    items = service.list_characters(current_character_id="rin")

    assert [item["id"] for item in items] == ["rin", "sakura"]
    assert items[0]["is_current"] is True
    assert items[0]["display_name"] == "Rin"
    assert items[0]["has_voice"] is True
    assert items[0]["source"] == "installed"


def test_character_studio_open_uses_draft_without_touching_source(tmp_path: Path) -> None:
    from app.config.character_studio import CharacterStudioService

    root = _runtime_root(tmp_path, "draft_open")
    source = _write_character(root)

    service = CharacterStudioService(root)
    opened = service.open_character("sakura")
    draft_dir = Path(opened["package_dir"])
    assert draft_dir != source
    assert draft_dir.exists()
    assert opened["doc"]["id"] == "sakura"
    assert opened["doc"]["card_text"] == "old card"

    opened["doc"]["card_text"] = "draft only"
    service.save_draft(opened["doc"], draft_dir)

    assert (source / "card.md").read_text(encoding="utf-8") == "old card"
    assert (draft_dir / "card.md").read_text(encoding="utf-8") == "draft only"


def test_character_studio_create_import_portrait_and_save_new_character(tmp_path: Path) -> None:
    from app.config.character_studio import CharacterStudioService

    root = _runtime_root(tmp_path, "new_character")
    service = CharacterStudioService(root)
    portrait_source = root / "source.png"
    portrait_source.write_bytes(b"new portrait")

    created = service.create_character({"id": "new_role", "display_name": "新角色"})
    draft_dir = Path(created["package_dir"])
    portrait = service.import_portrait(draft_dir, portrait_source, label="default")
    doc = created["doc"]
    doc["card_text"] = "system prompt"
    doc["initial_message"] = "初次见面"
    doc["default_portrait"] = portrait["relative_path"]
    doc["reply_tones"] = ["沉稳", "轻快"]
    doc["theme"]["primary_color"] = "#223344"
    doc["theme"]["accent_color"] = "#556677"

    saved = service.save_character(doc, draft_dir, current_character_id="sakura")

    assert saved["saved_character_id"] == "new_role"
    assert saved["current_character_id"] == "sakura"
    profile = CharacterRegistry(root).get("new_role")
    assert profile.display_name == "新角色"
    assert profile.reply_tones == ["沉稳", "轻快"]
    assert profile.voice is None
    assert (profile.package_dir / "portraits" / "default.png").read_bytes() == b"new portrait"


def test_character_studio_save_existing_preserves_voice_and_exports_char(tmp_path: Path) -> None:
    from app.config.character_studio import CharacterStudioService

    root = _runtime_root(tmp_path, "save_existing")
    _write_character(root, "sakura", "Sakura")
    service = CharacterStudioService(root)
    opened = service.open_character("sakura")
    draft_dir = Path(opened["package_dir"])
    doc = opened["doc"]
    doc["display_name"] = "Sakura Edited"
    doc["card_text"] = "new card"
    doc["theme"]["primary_color"] = "#abcdef"

    saved = service.save_character(doc, draft_dir, current_character_id="sakura")

    profile = CharacterRegistry(root).get("sakura")
    assert saved["current_character_id"] == "sakura"
    assert profile.display_name == "Sakura Edited"
    assert profile.voice is not None
    assert (profile.package_dir / "card.md").read_text(encoding="utf-8") == "new card"
    manifest = json.loads((profile.package_dir / "character.json").read_text(encoding="utf-8"))
    assert manifest["theme"]["source"] == "package"
    assert manifest["theme"]["primary_color"] == "#abcdef"

    archive_path = root / "sakura.card.char"
    result = service.export_archive(draft_dir, archive_path, include_voice=False)
    assert result["output_path"] == str(archive_path)
    with zipfile.ZipFile(archive_path) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["character"]["id"] == "sakura"
        assert "voice" not in manifest["character"]


def test_character_studio_rejects_unsafe_ids_and_paths(tmp_path: Path) -> None:
    from app.config.character_studio import CharacterStudioService

    root = _runtime_root(tmp_path, "validation")
    service = CharacterStudioService(root)

    with pytest.raises(ValueError, match="角色 id"):
        service.create_character({"id": "../bad", "display_name": "Bad"})

    with pytest.raises(ValueError, match="角色 id"):
        service.open_character("../bad")

    outside = root.parent / "outside.png"
    outside.write_bytes(b"png")
    created = service.create_character({"id": "safe", "display_name": "Safe"})
    draft_dir = Path(created["package_dir"])
    with pytest.raises(ValueError, match="文件扩展名"):
        service.import_portrait(draft_dir, root / "bad.txt", label="default")

    assert outside.exists()
