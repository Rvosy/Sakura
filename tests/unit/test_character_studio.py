from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

import pytest

from app.config.character_loader import CharacterConfigError, CharacterRegistry


def _runtime_root(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    root.mkdir(parents=True)
    return root


def _write_character(root: Path, character_id: str = "sakura", display_name: str = "Sakura") -> Path:
    package_dir = root / "characters" / character_id
    (package_dir / "portraits").mkdir(parents=True)
    (package_dir / "voice" / "refs" / "tone_refs").mkdir(parents=True)
    (package_dir / "card.md").write_text("old card", encoding="utf-8")
    (package_dir / "portraits" / "default.png").write_bytes(b"png")
    (package_dir / "voice" / "refs" / "tone_refs" / "neutral.wav").write_bytes(b"wav")
    (package_dir / "voice" / "refs" / "ref.txt").write_text(
        "voice/refs/tone_refs/neutral.wav|JA|こんにちは|温柔\n",
        encoding="utf-8",
    )
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


def test_character_studio_keeps_modified_installed_role_published_and_new_role_in_workspace(
    tmp_path: Path,
) -> None:
    from app.config.character_studio import CharacterStudioService

    root = _runtime_root(tmp_path, "list_sources")
    _write_character(root, "sakura", "Sakura")
    service = CharacterStudioService(root)
    opened = service.open_character("sakura")
    opened["doc"]["display_name"] = "Sakura Edited"
    service.save_workspace_draft(opened["workspace_id"], opened["doc"])
    service.create_character({"id": "new_role", "display_name": "New Role"})

    items = {item["id"]: item for item in service.list_characters()}

    assert items["sakura"]["is_installed"] is True
    assert items["sakura"]["is_dirty"] is True
    assert items["sakura"]["source"] == "draft"
    assert items["new_role"]["is_installed"] is False
    assert items["new_role"]["is_dirty"] is True
    assert items["new_role"]["draft_kind"] == "new"


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


@pytest.mark.skipif(os.name != "nt", reason="Windows 会规范化目录名末尾的点号")
def test_character_studio_preserves_trailing_dot_id_for_workspace_autosave(tmp_path: Path) -> None:
    from app.config.character_studio import CharacterStudioService

    root = _runtime_root(tmp_path, "trailing_dot_workspace")
    character_id = "N.A.V.I."
    _write_character(root, character_id, "N.A.V.I.")
    service = CharacterStudioService(root)

    opened = service.open_character(character_id)

    assert opened["workspace_id"] == character_id
    saved = service.save_workspace_draft(opened["workspace_id"], opened["doc"])
    assert saved["workspace_id"] == character_id


def test_character_studio_open_reads_reference_audio_rows(tmp_path: Path) -> None:
    from app.config.character_studio import CharacterStudioService

    root = _runtime_root(tmp_path, "reference_open")
    _write_character(root)

    opened = CharacterStudioService(root).open_character("sakura")

    assert opened["doc"]["reference_audios"] == [
        {
            "audio_path": "voice/refs/tone_refs/neutral.wav",
            "ref_lang": "JA",
            "ref_text": "こんにちは",
            "tone": "温柔",
        }
    ]


def test_character_studio_save_draft_writes_references_and_derives_reply_tones(tmp_path: Path) -> None:
    from app.config.character_studio import CharacterStudioService

    root = _runtime_root(tmp_path, "reference_save")
    service = CharacterStudioService(root)
    created = service.create_character({"id": "voice_role", "display_name": "Voice"})
    draft_dir = Path(created["package_dir"])
    ref_dir = draft_dir / "voice" / "refs" / "tone_refs"
    ref_dir.mkdir(parents=True)
    (ref_dir / "calm.wav").write_bytes(b"calm")
    (ref_dir / "calm-alt.wav").write_bytes(b"calm-alt")
    (ref_dir / "happy.wav").write_bytes(b"happy")
    doc = created["doc"]
    doc["voice"] = {
        "tone_refs": "voice/refs/custom.txt",
        "gpt_model": "",
        "sovits_model": "",
        "ref_lang": "ja",
        "text_lang": "zh",
    }
    doc["reply_tones"] = ["旧标签"]
    doc["reference_audios"] = [
        {
            "audio_path": "voice/refs/tone_refs/calm.wav",
            "ref_lang": "JA",
            "ref_text": "落ち着いて",
            "tone": "沉稳",
        },
        {
            "audio_path": "voice/refs/tone_refs/calm-alt.wav",
            "ref_lang": "JA",
            "ref_text": "ゆっくり",
            "tone": "沉稳",
        },
        {
            "audio_path": "voice/refs/tone_refs/happy.wav",
            "ref_lang": "ZH",
            "ref_text": "太好了",
            "tone": "开心",
        },
    ]

    saved = service.save_draft(doc, draft_dir)

    assert saved["doc"]["reply_tones"] == ["沉稳", "开心"]
    assert saved["doc"]["voice"]["tone_refs"] == "voice/refs/ref.txt"
    assert (draft_dir / "voice" / "refs" / "ref.txt").read_text(encoding="utf-8") == (
        "voice/refs/tone_refs/calm.wav|JA|落ち着いて|沉稳\n"
        "voice/refs/tone_refs/calm-alt.wav|JA|ゆっくり|沉稳\n"
        "voice/refs/tone_refs/happy.wav|ZH|太好了|开心\n"
    )
    manifest = json.loads((draft_dir / "character.json").read_text(encoding="utf-8"))
    assert manifest["reply"]["tones"] == ["沉稳", "开心"]


def test_character_studio_rejects_incomplete_reference_when_voice_enabled(tmp_path: Path) -> None:
    from app.config.character_studio import CharacterStudioService

    root = _runtime_root(tmp_path, "reference_validation")
    service = CharacterStudioService(root)
    created = service.create_character({"id": "voice_role", "display_name": "Voice"})
    doc = created["doc"]
    doc["voice"] = {
        "tone_refs": "voice/refs/ref.txt",
        "gpt_model": "",
        "sovits_model": "",
        "ref_lang": "ja",
        "text_lang": "ja",
    }
    doc["reference_audios"] = [
        {"audio_path": "", "ref_lang": "JA", "ref_text": "こんにちは", "tone": "温柔"}
    ]

    with pytest.raises(ValueError, match="参考语音第 1 条"):
        service.save_draft(doc, Path(created["package_dir"]))


def test_character_studio_disabling_voice_does_not_reload_stale_reference_file(tmp_path: Path) -> None:
    from app.config.character_studio import CharacterStudioService

    root = _runtime_root(tmp_path, "voice_disable")
    _write_character(root)
    service = CharacterStudioService(root)
    opened = service.open_character("sakura")
    doc = opened["doc"]
    doc["voice"] = None
    doc["reference_audios"] = []
    doc["reply_tones"] = []

    saved = service.save_character(doc, Path(opened["package_dir"]))

    assert saved["doc"]["voice"] is None
    assert saved["doc"]["reference_audios"] == []
    assert saved["doc"]["reply_tones"] == []
    manifest = json.loads(
        (root / "characters" / "sakura" / "character.json").read_text(encoding="utf-8")
    )
    assert "voice" not in manifest
    assert "reply" not in manifest


def test_character_studio_imports_voice_assets_into_workspace(tmp_path: Path) -> None:
    from app.config.character_studio import CharacterStudioService

    root = _runtime_root(tmp_path, "voice_import")
    service = CharacterStudioService(root)
    created = service.create_character({"id": "voice_role", "display_name": "Voice"})
    draft_dir = Path(created["package_dir"])
    gpt_source = root / "voice.ckpt"
    audio_source = root / "sample.wav"
    gpt_source.write_bytes(b"gpt")
    audio_source.write_bytes(b"audio")

    model = service.import_voice_model(draft_dir, gpt_source, model_type="gpt")
    audio = service.import_reference_audio(draft_dir, audio_source)

    assert model["relative_path"].startswith("voice/models/")
    assert audio["relative_path"].startswith("voice/refs/tone_refs/")
    assert (draft_dir / model["relative_path"]).read_bytes() == b"gpt"
    assert (draft_dir / audio["relative_path"]).read_bytes() == b"audio"


def test_character_studio_builds_reference_audio_preview_data_url(tmp_path: Path) -> None:
    from app.config.character_studio import CharacterStudioService

    root = _runtime_root(tmp_path, "voice_preview")
    service = CharacterStudioService(root)
    created = service.create_character({"id": "voice_role", "display_name": "Voice"})
    draft_dir = Path(created["package_dir"])
    audio_dir = draft_dir / "voice" / "refs" / "tone_refs"
    audio_dir.mkdir(parents=True)
    (audio_dir / "sample.wav").write_bytes(b"audio")

    result = service.load_reference_audio_preview(
        draft_dir,
        "voice/refs/tone_refs/sample.wav",
    )

    assert result["data_url"] == "data:audio/wav;base64,YXVkaW8="


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
    assert saved["message"] == "已发布角色「新角色」。"
    profile = CharacterRegistry(root).get("new_role")
    assert profile.display_name == "新角色"
    assert profile.reply_tones == ["沉稳", "轻快"]
    assert profile.voice is None
    assert (profile.package_dir / "portraits" / "default.png").read_bytes() == b"new portrait"


def test_character_studio_create_rejects_installed_character_id(tmp_path: Path) -> None:
    from app.config.character_studio import CharacterStudioService

    root = _runtime_root(tmp_path, "duplicate_character")
    _write_character(root, "sakura", "Sakura")
    service = CharacterStudioService(root)

    with pytest.raises(ValueError, match="角色 ID 已存在"):
        service.create_character({"id": "sakura", "display_name": "Replacement"})

    assert (root / "characters" / "sakura" / "voice" / "refs" / "ref.txt").exists()


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
    assert saved["message"] == "已保存角色「Sakura Edited」。"
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

    for unsafe_id in (".", ".."):
        with pytest.raises(ValueError, match="角色 id"):
            service.create_character({"id": unsafe_id, "display_name": "Bad"})

    outside = root.parent / "outside.png"
    outside.write_bytes(b"png")
    created = service.create_character({"id": "safe", "display_name": "Safe"})
    draft_dir = Path(created["package_dir"])
    with pytest.raises(ValueError, match="文件扩展名"):
        service.import_portrait(draft_dir, root / "bad.txt", label="default")

    assert outside.exists()


def test_character_studio_rejects_workspace_document_id_mismatch(tmp_path: Path) -> None:
    from app.config.character_studio import CharacterStudioService

    root = _runtime_root(tmp_path, "workspace_id_mismatch")
    service = CharacterStudioService(root)
    created = service.create_character({"id": "safe", "display_name": "Safe"})
    draft_dir = Path(created["package_dir"])
    payload = dict(created["doc"])
    payload["id"] = "other"

    with pytest.raises(ValueError, match="工作区不一致"):
        service.save_character(payload, draft_dir)

    assert not (root / "characters" / "other").exists()


def test_character_studio_persists_new_draft_under_data_and_resumes_it(tmp_path: Path) -> None:
    from app.config.character_studio import CharacterStudioService

    root = _runtime_root(tmp_path, "persistent_draft")
    service = CharacterStudioService(root)
    created = service.create_character({"id": "draft_role", "display_name": "Draft"})

    assert Path(created["package_dir"]) == (
        root / "data" / "character_studio" / "drafts" / "draft_role" / "package"
    )
    assert created["workspace_id"] == "draft_role"

    doc = created["doc"]
    doc["card_text"] = "unfinished card"
    service.save_workspace_draft("draft_role", doc)

    restarted = CharacterStudioService(root)
    resumed = restarted.create_character({"id": "draft_role", "display_name": "Ignored"})
    listed = restarted.list_characters()

    assert resumed["resumed"] is True
    assert resumed["doc"]["card_text"] == "unfinished card"
    assert listed[0]["id"] == "draft_role"
    assert listed[0]["has_draft"] is True
    assert listed[0]["draft_kind"] == "new"
    assert listed[0]["is_dirty"] is True


def test_character_studio_workspace_autosave_accepts_incomplete_voice_rows(tmp_path: Path) -> None:
    from app.config.character_studio import CharacterStudioService

    root = _runtime_root(tmp_path, "lenient_autosave")
    service = CharacterStudioService(root)
    created = service.create_character({"id": "voice_role", "display_name": "Voice"})
    doc = created["doc"]
    doc["voice"] = {
        "tone_refs": "voice/refs/ref.txt",
        "gpt_model": "",
        "sovits_model": "",
        "ref_lang": "ja",
        "text_lang": "zh",
    }
    doc["reference_audios"] = [
        {"audio_path": "", "ref_lang": "JA", "ref_text": "", "tone": ""}
    ]

    saved = service.save_workspace_draft(created["workspace_id"], doc)

    assert saved["doc"]["reference_audios"][0]["audio_path"] == ""
    assert saved["is_dirty"] is True
    with pytest.raises(ValueError, match="参考语音第 1 条"):
        service.save_character(doc, created["workspace_id"])


def test_character_studio_lenient_draft_save_does_not_publish_invalid_role(tmp_path: Path) -> None:
    from app.config.character_studio import CharacterStudioService

    root = _runtime_root(tmp_path, "lenient_draft_publish")
    service = CharacterStudioService(root)
    created = service.create_character({"id": "unfinished", "display_name": "Unfinished"})
    doc = created["doc"]
    doc["card_text"] = "仍在编辑"

    saved = service.save_workspace_draft(created["workspace_id"], doc)

    assert saved["is_dirty"] is True
    assert saved["doc"]["card_text"] == "仍在编辑"
    assert not (root / "characters" / "unfinished").exists()
    with pytest.raises(CharacterConfigError, match="default"):
        service.save_character(doc, created["workspace_id"])
    assert not (root / "characters" / "unfinished").exists()


def test_character_studio_invalid_published_save_preserves_original_role(tmp_path: Path) -> None:
    from app.config.character_studio import CharacterStudioService

    root = _runtime_root(tmp_path, "invalid_published_save")
    package_dir = _write_character(root, "sakura", "Sakura")
    manifest_path = package_dir / "character.json"
    original_manifest = manifest_path.read_bytes()
    service = CharacterStudioService(root)
    opened = service.open_character("sakura")
    doc = opened["doc"]
    doc["default_portrait"] = "portraits/missing.png"
    service.save_workspace_draft(opened["workspace_id"], doc)

    with pytest.raises(CharacterConfigError, match="默认立绘不存在"):
        service.save_character(doc, opened["workspace_id"])

    assert manifest_path.read_bytes() == original_manifest
    listed = {item["id"]: item for item in service.list_characters()}
    assert listed["sakura"]["is_installed"] is True
    assert listed["sakura"]["is_dirty"] is True


def test_character_studio_imports_portrait_folder_with_description_labels(tmp_path: Path) -> None:
    from app.config.character_studio import CharacterStudioService

    root = _runtime_root(tmp_path, "portrait_folder")
    source = root / "source"
    nested = source / "nested"
    nested.mkdir(parents=True)
    (source / "A010.png").write_bytes(b"a")
    (source / "happy.jpg").write_bytes(b"b")
    (nested / "ignored.png").write_bytes(b"ignored")
    (source / "立绘说明.txt").write_text("A010 中性直视\n", encoding="utf-8")
    service = CharacterStudioService(root)
    created = service.create_character({"id": "portrait_role", "display_name": "Portrait"})

    result = service.import_portrait_folder(created["workspace_id"], source)

    assert [(item["suggested_label"], Path(item["relative_path"]).name) for item in result["items"]] == [
        ("中性直视", "A010.png"),
        ("happy", "happy.jpg"),
    ]
    assert not any("ignored" in item["relative_path"] for item in result["items"])


def test_character_studio_imports_reference_audio_folder_without_parsing_ref_txt(tmp_path: Path) -> None:
    from app.config.character_studio import CharacterStudioService

    root = _runtime_root(tmp_path, "audio_folder")
    source = root / "source"
    source.mkdir()
    (source / "calm.wav").write_bytes(b"calm")
    (source / "happy.ogg").write_bytes(b"happy")
    (source / "ref.txt").write_text("anything|JA|text|tone\n", encoding="utf-8")
    service = CharacterStudioService(root)
    created = service.create_character({"id": "voice_role", "display_name": "Voice"})

    result = service.import_reference_audio_folder(
        created["workspace_id"],
        source,
        ref_lang="ZH",
    )

    assert result["items"] == [
        {
            "audio_path": "voice/refs/tone_refs/calm.wav",
            "ref_lang": "ZH",
            "ref_text": "",
            "tone": "",
        },
        {
            "audio_path": "voice/refs/tone_refs/happy.ogg",
            "ref_lang": "ZH",
            "ref_text": "",
            "tone": "",
        },
    ]


def test_character_studio_autosave_prunes_only_unreferenced_imported_assets(tmp_path: Path) -> None:
    from app.config.character_studio import CharacterStudioService

    root = _runtime_root(tmp_path, "asset_prune")
    service = CharacterStudioService(root)
    created = service.create_character({"id": "asset_role", "display_name": "Asset"})
    package_dir = Path(created["package_dir"])
    original = package_dir / "portraits" / "original.png"
    original.write_bytes(b"original")
    source = root / "selected.png"
    source.write_bytes(b"selected")
    imported = service.import_portrait(created["workspace_id"], source, label="selected")
    imported_path = package_dir / imported["relative_path"]
    assert imported_path.exists()

    service.save_workspace_draft(created["workspace_id"], created["doc"])

    assert not imported_path.exists()
    assert original.exists()


def test_character_studio_save_preserves_unknown_manifest_fields(tmp_path: Path) -> None:
    from app.config.character_studio import CharacterStudioService

    root = _runtime_root(tmp_path, "preserve_manifest")
    source = _write_character(root)
    manifest_path = source / "character.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["renderer"] = {"type": "mmd", "model": "models/demo.pmx"}
    manifest["backchannel"] = "backchannels/manifest.json"
    manifest["portrait"]["future_option"] = {"enabled": True}
    manifest["theme"]["future_theme_option"] = "keep"
    manifest["reply"]["future_reply_option"] = "keep"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    service = CharacterStudioService(root)
    opened = service.open_character("sakura")
    doc = opened["doc"]
    doc["display_name"] = "Edited"

    service.save_character(doc, opened["workspace_id"])
    saved_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert saved_manifest["renderer"] == {"type": "mmd", "model": "models/demo.pmx"}
    assert saved_manifest["backchannel"] == "backchannels/manifest.json"
    assert saved_manifest["portrait"]["future_option"] == {"enabled": True}
    assert saved_manifest["theme"]["future_theme_option"] == "keep"
    assert saved_manifest["reply"]["future_reply_option"] == "keep"
