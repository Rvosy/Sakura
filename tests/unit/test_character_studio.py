from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from app.config.character_loader import CharacterConfigError, CharacterRegistry
from app.config.character_studio import (
    CharacterStudioOperationCancelled,
    CharacterStudioService,
)
from app.storage.paths import sanitize_directory_component


def _write_character(root: Path, character_id: str = "sakura") -> Path:
    package = root / "characters" / character_id
    (package / "portraits").mkdir(parents=True)
    (package / "portraits" / "default.png").write_bytes(b"portrait")
    (package / "card.md").write_text("original card", encoding="utf-8")
    (package / "character.json").write_text(
        json.dumps(
            {
                "id": character_id,
                "display_name": "Sakura",
                "card": "card.md",
                "portrait": {
                    "default": "portraits/default.png",
                    "expressions": {"默认": "portraits/default.png"},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return package


def test_character_studio_creates_imports_saves_and_exports(tmp_path: Path) -> None:
    service = CharacterStudioService(tmp_path)
    portrait_source = tmp_path / "portrait.png"
    portrait_source.write_bytes(b"new portrait")

    created = service.create_character({"id": "new_role", "display_name": "新角色"})
    portrait = service.import_portrait(
        created["workspace_id"],
        portrait_source,
        label="default",
    )
    doc = created["doc"]
    doc["card_text"] = "system prompt"
    doc["default_portrait"] = portrait["relative_path"]
    doc["expressions"] = {"默认": portrait["relative_path"]}

    saved = service.save_character(doc, created["workspace_id"], current_character_id="sakura")
    archive = service.export_archive(
        created["workspace_id"],
        tmp_path / "new-role.char",
        include_voice=False,
    )

    assert saved["saved_character_id"] == "new_role"
    assert saved["current_character_id"] == "sakura"
    profile = CharacterRegistry(tmp_path).get("new_role")
    assert profile.display_name == "新角色"
    assert (profile.package_dir / portrait["relative_path"]).read_bytes() == b"new portrait"
    with zipfile.ZipFile(archive["output_path"]) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
    assert manifest["character"]["id"] == "new_role"


def test_character_studio_voice_assets_round_trip_through_the_draft(tmp_path: Path) -> None:
    service = CharacterStudioService(tmp_path)
    created = service.create_character({"id": "voice_role", "display_name": "Voice"})
    model_source = tmp_path / "model.ckpt"
    audio_source = tmp_path / "neutral.wav"
    model_source.write_bytes(b"model")
    audio_source.write_bytes(b"audio")
    model = service.import_voice_model(created["workspace_id"], model_source, model_type="gpt")
    audio = service.import_reference_audio(created["workspace_id"], audio_source)
    doc = created["doc"]
    doc["voice"] = {
        "tone_refs": "voice/refs/ref.txt",
        "gpt_model": model["relative_path"],
        "sovits_model": "",
        "ref_lang": "ja",
        "text_lang": "ja",
    }
    doc["reference_audios"] = [{
        "audio_path": audio["relative_path"],
        "ref_lang": "JA",
        "ref_text": "こんにちは",
        "tone": "温柔",
    }]

    saved = service.save_draft(doc, created["workspace_id"])
    preview = service.load_reference_audio_preview(
        created["workspace_id"],
        audio["relative_path"],
    )

    assert saved["doc"]["reply_tones"] == ["温柔"]
    assert saved["doc"]["voice"]["tone_refs"] == "voice/refs/ref.txt"
    assert preview["data_url"] == "data:audio/wav;base64,YXVkaW8="


def test_character_studio_uses_portable_directories_for_windows_trailing_dot_id(
    tmp_path: Path,
) -> None:
    service = CharacterStudioService(tmp_path)
    created = service.create_character({"id": "N.A.V.I.", "display_name": "N.A.V.I."})
    portrait = Path(created["package_dir"]) / "portraits" / "default.png"
    portrait.write_bytes(b"portrait")
    doc = created["doc"]
    doc["card_text"] = "system prompt"
    doc["default_portrait"] = "portraits/default.png"

    saved = service.save_character(doc, created["workspace_id"])

    assert created["workspace_id"] == "N.A.V.I."
    assert Path(created["package_dir"]).parent.name == sanitize_directory_component("N.A.V.I.")
    assert saved["saved_character_id"] == "N.A.V.I."
    profile = CharacterRegistry(tmp_path).get("N.A.V.I.")
    assert profile.package_dir.name == sanitize_directory_component("N.A.V.I.")


def test_invalid_published_save_preserves_the_original_character(tmp_path: Path) -> None:
    package = _write_character(tmp_path)
    manifest = package / "character.json"
    original_manifest = manifest.read_bytes()
    service = CharacterStudioService(tmp_path)
    opened = service.open_character("sakura")
    doc = opened["doc"]
    doc["default_portrait"] = "portraits/missing.png"

    with pytest.raises(CharacterConfigError, match="默认立绘不存在"):
        service.save_character(doc, opened["workspace_id"])

    assert manifest.read_bytes() == original_manifest
    assert (package / "portraits" / "default.png").read_bytes() == b"portrait"


def test_character_studio_rejects_unsafe_ids_and_external_workspaces(tmp_path: Path) -> None:
    service = CharacterStudioService(tmp_path)

    for unsafe_id in ("../bad", ".", ".."):
        with pytest.raises(ValueError, match="角色 id"):
            service.create_character({"id": unsafe_id, "display_name": "Bad"})

    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(ValueError, match="工作区"):
        service.save_draft({"id": "safe", "display_name": "Safe"}, outside)
    assert list(outside.iterdir()) == []


def test_character_studio_recovers_original_after_interrupted_directory_switch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _write_character(tmp_path)
    service = CharacterStudioService(tmp_path)
    opened = service.open_character("sakura")
    doc = opened["doc"]
    doc["card_text"] = "replacement card"
    original_replace = __import__("os").replace
    def interrupt_second_replace(source: Path, target: Path) -> None:
        if ".studio-staging-" in Path(source).name:
            raise SystemExit("simulated process exit")
        original_replace(source, target)

    monkeypatch.setattr("app.config.character_studio.os.replace", interrupt_second_replace)
    with pytest.raises(SystemExit, match="simulated process exit"):
        service.save_character(doc, opened["workspace_id"])
    monkeypatch.setattr("app.config.character_studio.os.replace", original_replace)

    assert not package.exists()
    recovered = CharacterStudioService(tmp_path)

    assert (package / "card.md").read_text(encoding="utf-8") == "original card"
    assert not recovered._publish_journal_path.exists()


def test_character_studio_recovers_when_publish_stops_before_first_rename(
    tmp_path: Path,
) -> None:
    package = _write_character(tmp_path)
    service = CharacterStudioService(tmp_path)
    opened = service.open_character("sakura")
    doc = opened["doc"]
    doc["card_text"] = "replacement card"

    def interrupt_before_rename() -> None:
        raise SystemExit("simulated process exit")

    with pytest.raises(SystemExit, match="simulated process exit"):
        service.save_character(
            doc,
            opened["workspace_id"],
            commit_started=interrupt_before_rename,
        )

    assert (package / "card.md").read_text(encoding="utf-8") == "original card"
    assert service._publish_journal_path.exists()

    recovered = CharacterStudioService(tmp_path)

    assert (package / "card.md").read_text(encoding="utf-8") == "original card"
    assert not recovered._publish_journal_path.exists()


def test_character_studio_rejects_symlinked_workspace_assets(tmp_path: Path) -> None:
    service = CharacterStudioService(tmp_path)
    created = service.create_character({"id": "linked", "display_name": "Linked"})
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    linked = Path(created["package_dir"]) / "portraits" / "linked.png"
    try:
        linked.symlink_to(outside)
    except OSError:
        pytest.skip("当前文件系统不支持符号链接")
    doc = created["doc"]
    doc["default_portrait"] = "portraits/linked.png"

    with pytest.raises(ValueError, match="符号链接"):
        service.save_character(doc, created["workspace_id"])


def test_cancelled_large_import_removes_partial_file(tmp_path: Path) -> None:
    service = CharacterStudioService(tmp_path)
    created = service.create_character({"id": "cancelled", "display_name": "Cancelled"})
    source = tmp_path / "large.ckpt"
    source.write_bytes(b"x" * (3 * 1024 * 1024))
    checkpoints = 0

    def cancel_during_copy() -> None:
        nonlocal checkpoints
        checkpoints += 1
        if checkpoints >= 3:
            raise CharacterStudioOperationCancelled()

    with pytest.raises(CharacterStudioOperationCancelled):
        service.import_voice_model(
            created["workspace_id"],
            source,
            model_type="gpt",
            cancel_check=cancel_during_copy,
        )

    model_dir = Path(created["package_dir"]) / "voice" / "models"
    assert not (model_dir / "large.ckpt").exists()
    assert list(model_dir.glob("*.partial")) == []
