from __future__ import annotations

import json
import os
import shutil
import time
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


def test_repeated_publish_keeps_only_two_recent_complete_backups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _write_character(tmp_path)
    model = package / "voice" / "models" / "model.ckpt"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"model" * 1024)
    service = CharacterStudioService(tmp_path)
    opened = service.open_character("sakura")
    # Exercise timestamp collisions: UUID suffixes must not decide recency.
    strftime = time.strftime
    monkeypatch.setattr("app.config.character_studio.time.strftime", lambda fmt, *args:
                        "20260905-120000" if fmt == "%Y%m%d-%H%M%S" else strftime(fmt, *args))
    for index in range(8):
        opened["doc"]["card_text"] = f"revision {index}"
        service.save_character(opened["doc"], opened["workspace_id"])
        backups = list(service.backup_root.iterdir())
        assert len(backups) == min(index + 1, 2)
        assert all((backup / "voice/models/model.ckpt").read_bytes() == model.read_bytes()
                   for backup in backups)
    assert {(backup / "card.md").read_text(encoding="utf-8") for backup in backups} == {
        "revision 5", "revision 6",
    }
    assert (package / "card.md").read_text(encoding="utf-8") == "revision 7"


def test_unchanged_publish_does_not_copy_or_backup_and_detects_asset_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _write_character(tmp_path)
    service = CharacterStudioService(tmp_path)
    opened = service.open_character("sakura")
    saved = service.save_character(opened["doc"], opened["workspace_id"])
    backups = list(service.backup_root.iterdir())
    original_manifest_stat = (package / "character.json").stat()

    def unexpected_copy(*args, **kwargs):
        pytest.fail("unchanged publish must not copy the package")

    with monkeypatch.context() as patch:
        patch.setattr("app.config.character_studio._copytree_cancellable", unexpected_copy)
        for _ in range(3):
            saved = service.save_character(saved["doc"], opened["workspace_id"])
            assert saved["changed"] is False
            assert saved["is_dirty"] is False
    assert list(service.backup_root.iterdir()) == backups
    assert (package / "character.json").stat().st_mtime_ns == original_manifest_stat.st_mtime_ns

    # Same path, size and mtime can still hide changed bytes (e.g. an external edit).
    portrait = Path(opened["package_dir"]) / "portraits/default.png"
    original_stat = portrait.stat()
    portrait.write_bytes(b"modified")
    os.utime(portrait, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    saved = service.save_character(saved["doc"], opened["workspace_id"])
    assert saved["changed"] is True
    assert (package / "portraits/default.png").read_bytes() == b"modified"


def test_autosave_reuses_one_draft_and_preserves_unpublished_work(tmp_path: Path) -> None:
    _write_character(tmp_path)
    service = CharacterStudioService(tmp_path)
    opened = service.open_character("sakura")
    for index in range(20):
        opened["doc"]["card_text"] = f"draft {index}"
        service.save_workspace_draft(opened["workspace_id"], opened["doc"])
    assert list(service.backup_root.iterdir()) == []
    assert len(list(service.workspace_characters_dir.iterdir())) == 1
    assert service.release_workspace("sakura")["released"] is False
    restarted = CharacterStudioService(tmp_path)
    assert restarted.open_character("sakura")["doc"]["card_text"] == "draft 19"
    restarted.save_character(opened["doc"], opened["workspace_id"])
    assert restarted.release_workspace("sakura")["released"] is True
    assert list(restarted.workspace_characters_dir.iterdir()) == []


def test_unchanged_publish_can_be_cancelled_during_large_file_comparison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_character(tmp_path)
    service = CharacterStudioService(tmp_path)
    opened = service.open_character("sakura")
    portrait = Path(opened["package_dir"]) / "portraits/default.png"
    portrait.write_bytes(b"p" * (3 * 1024 * 1024))
    saved = service.save_character(opened["doc"], opened["workspace_id"])
    backups = list(service.backup_root.iterdir())
    checkpoints = 0
    comparing_portrait = False
    from app.config.character_studio import _files_equal_cancellable as original_compare

    def compare(source, target, *, cancel_check):
        nonlocal comparing_portrait
        comparing_portrait = source == portrait
        return original_compare(source, target, cancel_check=cancel_check)

    monkeypatch.setattr("app.config.character_studio._files_equal_cancellable", compare)

    def cancel_comparison() -> None:
        nonlocal checkpoints
        if comparing_portrait:
            checkpoints += 1
            if checkpoints == 2:
                raise CharacterStudioOperationCancelled()

    with pytest.raises(CharacterStudioOperationCancelled):
        service.save_character(saved["doc"], opened["workspace_id"], cancel_check=cancel_comparison)
    assert list(service.backup_root.iterdir()) == backups
    assert not service._publish_journal_path.exists()
    assert service._read_state("sakura")["dirty"] is True


def test_successful_save_prunes_legacy_backups_only_for_the_matching_role(tmp_path: Path) -> None:
    package = _write_character(tmp_path)
    other = _write_character(tmp_path, "sakura-extra")
    service = CharacterStudioService(tmp_path)
    opened = service.open_character("sakura")
    saved = service.save_character(opened["doc"], opened["workspace_id"])
    legacy = []
    for index in range(5):
        path = service.backup_root / f"sakura-20200101-00000{index}"
        shutil.copytree(package, path)
        legacy.append(path)
    unrelated = service.backup_root / "sakura-extra-20200101-000000"
    shutil.copytree(other, unrelated)
    wrong_id = service.backup_root / "sakura-20200101-000010"
    shutil.copytree(other, wrong_id)
    manual = service.backup_root / "sakura-manual"
    shutil.copytree(package, manual)
    damaged = service.backup_root / "sakura-20200101-000011"
    damaged.mkdir()
    (damaged / "character.json").write_text("broken json", encoding="utf-8")
    service.save_character(saved["doc"], opened["workspace_id"])
    assert [path for path in legacy if path.exists()] == legacy[-1:]
    assert all(path.exists() for path in (unrelated, wrong_id, manual, damaged))


def test_backup_cleanup_failure_does_not_rollback_a_committed_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _write_character(tmp_path)
    model = package / "voice/models/model.ckpt"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"locked model")
    service = CharacterStudioService(tmp_path)
    opened = service.open_character("sakura")
    strftime = time.strftime
    monkeypatch.setattr("app.config.character_studio.time.strftime", lambda fmt, *args:
                        "20260905-120000" if fmt == "%Y%m%d-%H%M%S" else strftime(fmt, *args))
    for index in range(2):
        opened["doc"]["card_text"] = f"revision {index}"
        service.save_character(opened["doc"], opened["workspace_id"])
    original_rmtree = shutil.rmtree

    def fail_backup_cleanup(path, *args, **kwargs):
        if Path(path).name == "voice" and Path(path).parent.parent == service.backup_root:
            assert not service._publish_journal_path.exists()
            raise PermissionError("backup locked")
        return original_rmtree(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr("app.config.character_studio.shutil.rmtree", fail_backup_cleanup)
        opened["doc"]["card_text"] = "committed revision"
        saved = service.save_character(opened["doc"], opened["workspace_id"])
    assert (package / "card.md").read_text(encoding="utf-8") == "committed revision"
    assert saved["is_dirty"] is False
    assert "备份" in saved["message"]
    assert len(list(service.backup_root.iterdir())) == 3
    assert all((path / "character.json").is_file() for path in service.backup_root.iterdir())
    # A later no-op save retries cleanup without generating another backup.
    service.save_character(saved["doc"], opened["workspace_id"])
    assert len(list(service.backup_root.iterdir())) == 2
    assert {(path / "card.md").read_text(encoding="utf-8")
            for path in service.backup_root.iterdir()} == {"revision 0", "revision 1"}
    assert all((path / "portraits/default.png").read_bytes() == b"portrait"
               for path in service.backup_root.iterdir())


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
    from app.config.character_studio import rename_with_retry as original_rename

    def interrupt_second_replace(source: Path, target: Path, *args, **kwargs) -> None:
        if Path(source).name == "staging":
            raise SystemExit("simulated process exit")
        original_rename(source, target, *args, **kwargs)

    monkeypatch.setattr("app.config.character_studio.rename_with_retry", interrupt_second_replace)
    with pytest.raises(SystemExit, match="simulated process exit"):
        service.save_character(doc, opened["workspace_id"])
    monkeypatch.setattr("app.config.character_studio.rename_with_retry", original_rename)

    assert not package.exists()
    recovered = CharacterStudioService(tmp_path)

    assert (package / "card.md").read_text(encoding="utf-8") == "original card"
    assert not recovered._publish_journal_path.exists()


def test_character_studio_recovery_survives_a_second_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _write_character(tmp_path)
    service = CharacterStudioService(tmp_path)
    opened = service.open_character("sakura")
    doc = opened["doc"]
    doc["card_text"] = "earlier revision"
    service.save_character(doc, opened["workspace_id"])
    doc["card_text"] = "original card"
    service.save_character(doc, opened["workspace_id"])
    assert len(list(service.backup_root.iterdir())) == 2
    doc["card_text"] = "replacement card"
    from app.config.character_studio import rename_with_retry as original_rename

    def interrupt_after_backup_move(source: Path, target: Path, *args, **kwargs) -> None:
        original_rename(source, target, *args, **kwargs)
        if Path(source).name == "rollback":
            raise SystemExit("simulated publish exit after backup move")

    monkeypatch.setattr(
        "app.config.character_studio.rename_with_retry",
        interrupt_after_backup_move,
    )
    with pytest.raises(SystemExit, match="publish exit"):
        service.save_character(doc, opened["workspace_id"])
    assert len(list(service.backup_root.iterdir())) == 3

    def interrupt_before_recovery_install(source: Path, target: Path, *args, **kwargs) -> None:
        if Path(source).name == "recovery":
            raise SystemExit("simulated recovery exit")
        original_rename(source, target, *args, **kwargs)

    monkeypatch.setattr(
        "app.config.character_studio.rename_with_retry",
        interrupt_before_recovery_install,
    )
    with pytest.raises(SystemExit, match="recovery exit"):
        CharacterStudioService(tmp_path)

    monkeypatch.setattr("app.config.character_studio.rename_with_retry", original_rename)
    recovered = CharacterStudioService(tmp_path)

    assert (package / "card.md").read_text(encoding="utf-8") == "original card"
    assert not recovered._publish_journal_path.exists()
    assert not recovered._publish_transactions_root.exists()
    assert len(list(recovered.backup_root.iterdir())) == 3


def test_new_character_recovery_removal_survives_a_second_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = CharacterStudioService(tmp_path)
    created = service.create_character({"id": "new_role", "display_name": "New role"})
    package = Path(created["package_dir"])
    (package / "portraits" / "default.png").write_bytes(b"portrait")
    doc = created["doc"]
    doc["card_text"] = "new role"
    doc["default_portrait"] = "portraits/default.png"
    from app.config.character_studio import rename_with_retry as original_rename

    def interrupt_after_publish_install(source: Path, target: Path, *args, **kwargs) -> None:
        original_rename(source, target, *args, **kwargs)
        if Path(source).name == "staging":
            raise SystemExit("simulated new publish exit")

    monkeypatch.setattr(
        "app.config.character_studio.rename_with_retry",
        interrupt_after_publish_install,
    )
    with pytest.raises(SystemExit, match="new publish exit"):
        service.save_character(doc, created["workspace_id"])

    def interrupt_after_recovery_discard(source: Path, target: Path, *args, **kwargs) -> None:
        original_rename(source, target, *args, **kwargs)
        if Path(source).name == "new_role":
            raise SystemExit("simulated new recovery exit")

    monkeypatch.setattr(
        "app.config.character_studio.rename_with_retry",
        interrupt_after_recovery_discard,
    )
    with pytest.raises(SystemExit, match="new recovery exit"):
        CharacterStudioService(tmp_path)

    monkeypatch.setattr("app.config.character_studio.rename_with_retry", original_rename)
    recovered = CharacterStudioService(tmp_path)

    with pytest.raises(CharacterConfigError, match="未找到角色包"):
        CharacterRegistry(tmp_path).get("new_role")
    assert recovered._read_state("new_role")["dirty"] is True
    assert not recovered._publish_journal_path.exists()
    assert not recovered._publish_transactions_root.exists()


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


def test_publish_cancelled_after_staging_does_not_leave_a_scannable_role(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _write_character(tmp_path)
    service = CharacterStudioService(tmp_path)
    opened = service.open_character("sakura")
    doc = opened["doc"]
    doc["card_text"] = "replacement card"
    from app.config.character_studio import _copytree_cancellable as original_copytree

    def cancel_after_copy(source: Path, target: Path, *, cancel_check) -> None:
        original_copytree(source, target, cancel_check=cancel_check)
        if Path(target).name == "staging":
            raise CharacterStudioOperationCancelled()

    monkeypatch.setattr("app.config.character_studio._copytree_cancellable", cancel_after_copy)

    with pytest.raises(CharacterStudioOperationCancelled):
        service.save_character(doc, opened["workspace_id"])

    assert (package / "card.md").read_text(encoding="utf-8") == "original card"
    assert CharacterRegistry(tmp_path).get("sakura").package_dir == package
    transactions = tmp_path / "characters" / ".studio-transactions"
    assert not transactions.exists() or list(transactions.iterdir()) == []


def test_registry_ignores_studio_transaction_directories(tmp_path: Path) -> None:
    package = _write_character(tmp_path)
    characters = tmp_path / "characters"
    transaction_id = "a" * 32
    for name in (
        f".sakura.studio-staging-{transaction_id}",
        f".sakura.studio-rollback-{transaction_id}",
        f".sakura.studio-recovery-{transaction_id}",
    ):
        shutil.copytree(package, characters / name)
    transaction = characters / ".studio-transactions" / transaction_id / "staging"
    shutil.copytree(package, transaction)
    _write_character(tmp_path, "hero.studio-staging-real")

    registry = CharacterRegistry(tmp_path)

    assert set(registry.profiles) == {"sakura", "hero.studio-staging-real"}
    assert registry.load_errors == ()


def test_publish_failure_after_directory_switch_restores_role_and_dirty_draft(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _write_character(tmp_path)
    service = CharacterStudioService(tmp_path)
    opened = service.open_character("sakura")
    doc = opened["doc"]
    doc["card_text"] = "replacement card"
    original_write_state = service._write_state

    def fail_clean_state(character_id, saved_doc, *, origin, dirty, imported_assets) -> None:
        if not dirty:
            raise OSError("simulated clean-state failure")
        original_write_state(
            character_id,
            saved_doc,
            origin=origin,
            dirty=dirty,
            imported_assets=imported_assets,
        )

    monkeypatch.setattr(service, "_write_state", fail_clean_state)

    with pytest.raises(OSError, match="clean-state"):
        service.save_character(doc, opened["workspace_id"])

    assert (package / "card.md").read_text(encoding="utf-8") == "original card"
    assert service._read_state("sakura")["dirty"] is True
    assert not service._publish_journal_path.exists()


def test_open_rejects_symlink_before_copying_formal_role(
    tmp_path: Path,
) -> None:
    package = _write_character(tmp_path)
    outside = tmp_path / "secret.txt"
    outside.write_text("outside-secret", encoding="utf-8")
    link = package / "future-resource.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("当前文件系统不支持符号链接")
    service = CharacterStudioService(tmp_path)

    with pytest.raises(ValueError, match="符号链接"):
        service.open_character("sakura")

    assert not service._draft_root("sakura").exists()


def test_studio_reads_and_updates_runtime_v2_voice_extensions(tmp_path: Path) -> None:
    package = _write_character(tmp_path)
    (package / "voice" / "models").mkdir(parents=True)
    (package / "voice" / "refs" / "tone_refs").mkdir(parents=True)
    (package / "voice" / "models" / "old.ckpt").write_bytes(b"old")
    (package / "voice" / "models" / "old.pth").write_bytes(b"old")
    (package / "voice" / "refs" / "tone_refs" / "neutral.wav").write_bytes(b"wav")
    (package / "voice" / "refs" / "ref.txt").write_text(
        "voice/refs/tone_refs/neutral.wav|JA|hello|中性\n",
        encoding="utf-8",
    )
    manifest_path = package / "character.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["reply"] = {"futureMode": "keep"}
    manifest["extensions"] = {
        "sakura.tts": {"enabled": True, "provider": "sakura.tts.gpt-sovits"},
        "sakura.tts.gpt-sovits": {
            "toneRefs": "voice/refs/ref.txt",
            "gptModel": "voice/models/old.ckpt",
            "sovitsModel": "voice/models/old.pth",
            "refLang": "ja",
            "textLang": "zh",
            "futureProviderField": 7,
        },
        "com.example.keep": {"value": True},
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    service = CharacterStudioService(tmp_path)

    opened = service.open_character("sakura")
    assert opened["doc"]["voice"]["gpt_model"] == "voice/models/old.ckpt"
    assert opened["doc"]["voice"]["text_lang"] == "zh"
    doc = opened["doc"]
    doc["reply_tones"] = []
    doc["voice"]["gpt_model"] = ""
    service.save_draft(doc, opened["workspace_id"])
    saved = json.loads(
        (Path(opened["package_dir"]) / "character.json").read_text(encoding="utf-8")
    )

    assert saved["reply"] == {"futureMode": "keep", "tones": ["中性"]}
    assert saved["extensions"]["sakura.tts"]["enabled"] is True
    assert "gptModel" not in saved["extensions"]["sakura.tts.gpt-sovits"]
    assert saved["extensions"]["sakura.tts.gpt-sovits"]["futureProviderField"] == 7
    assert saved["extensions"]["com.example.keep"] == {"value": True}

    doc["voice"] = None
    service.save_draft(doc, opened["workspace_id"])
    disabled = json.loads(
        (Path(opened["package_dir"]) / "character.json").read_text(encoding="utf-8")
    )
    assert "voice" not in disabled
    assert disabled["extensions"]["sakura.tts"]["enabled"] is False
    assert disabled["extensions"]["sakura.tts.gpt-sovits"] == {
        "futureProviderField": 7,
    }
    assert disabled["extensions"]["com.example.keep"] == {"value": True}


def test_studio_preserves_future_reply_fields_when_tones_are_absent(tmp_path: Path) -> None:
    package = _write_character(tmp_path)
    manifest_path = package / "character.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["reply"] = {"futureMode": "keep"}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    service = CharacterStudioService(tmp_path)

    opened = service.open_character("sakura")
    service.save_draft(opened["doc"], opened["workspace_id"])
    saved = json.loads(
        (Path(opened["package_dir"]) / "character.json").read_text(encoding="utf-8")
    )

    assert saved["reply"] == {"futureMode": "keep"}


def test_studio_theme_save_preserves_an_unmanaged_voice_provider(tmp_path: Path) -> None:
    package = _write_character(tmp_path)
    manifest_path = package / "character.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["extensions"] = {
        "sakura.tts": {"enabled": True, "provider": "sakura.tts.genie"},
        "sakura.tts.genie": {
            "remoteCharacterName": "genie-role",
            "futureProviderField": 7,
        },
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    service = CharacterStudioService(tmp_path)

    opened = service.open_character("sakura")
    assert opened["doc"]["voice"] is None
    doc = opened["doc"]
    doc["theme"]["accent"] = "#112233"
    service.save_draft(doc, opened["workspace_id"])
    saved = json.loads(
        (Path(opened["package_dir"]) / "character.json").read_text(encoding="utf-8")
    )

    assert saved["extensions"] == manifest["extensions"]


def test_legacy_raw_and_new_drafts_migrate_once(tmp_path: Path) -> None:
    workspace_root = tmp_path / "data" / "character_studio"
    raw_root = workspace_root / "drafts" / "N.A.V.I."
    raw_package = raw_root / "package"
    (raw_package / "portraits").mkdir(parents=True)
    (raw_package / "portraits" / "default.png").write_bytes(b"portrait")
    (raw_package / "card.md").write_text("raw draft", encoding="utf-8")
    raw_doc = {
        "id": "N.A.V.I.",
        "display_name": "Raw draft",
        "card_text": "raw draft",
        "default_portrait": "portraits/default.png",
        "expressions": {"默认": "portraits/default.png"},
    }
    (raw_package / "character.json").write_text(
        json.dumps(
            {
                "id": "N.A.V.I.",
                "display_name": "Raw draft",
                "card": "card.md",
                "portrait": {
                    "default": "portraits/default.png",
                    "expressions": {"默认": "portraits/default.png"},
                },
            }
        ),
        encoding="utf-8",
    )
    (raw_root / "draft.json").write_text(
        json.dumps(
            {
                "version": 1,
                "id": "N.A.V.I.",
                "origin": "new",
                "dirty": True,
                "doc": raw_doc,
            }
        ),
        encoding="utf-8",
    )
    damaged_raw = workspace_root / "drafts" / "damaged"
    damaged_raw.mkdir()
    (damaged_raw / "draft.json").write_text(
        json.dumps({"version": 1, "id": "../bad", "dirty": True, "doc": {}}),
        encoding="utf-8",
    )
    legacy_package = _write_character(
        tmp_path / "runtime" / "character-studio" / "workspace",
        "legacy",
    )
    damaged_legacy = (
        tmp_path
        / "runtime"
        / "character-studio"
        / "workspace"
        / "characters"
        / "damaged"
    )
    damaged_legacy.mkdir()
    (damaged_legacy / "character.json").write_text("{", encoding="utf-8")
    assert legacy_package.is_dir()

    service = CharacterStudioService(tmp_path)
    second = CharacterStudioService(tmp_path)

    assert service.open_character("N.A.V.I.")["resumed"] is True
    assert service.open_character("N.A.V.I.")["doc"]["card_text"] == "raw draft"
    assert second.open_character("legacy")["resumed"] is True
    assert len([item for item in second.list_characters() if item["id"] == "legacy"]) == 1


def test_cancelled_folder_import_rolls_back_the_whole_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = CharacterStudioService(tmp_path)
    created = service.create_character({"id": "batch", "display_name": "Batch"})
    source = tmp_path / "portrait-source"
    source.mkdir()
    (source / "a.png").write_bytes(b"a")
    (source / "b.png").write_bytes(b"b")
    from app.config.character_studio import _copy_workspace_asset as original_copy_asset
    copied = 0

    def cancel_second(*args, **kwargs):
        nonlocal copied
        copied += 1
        if copied == 2:
            raise CharacterStudioOperationCancelled()
        return original_copy_asset(*args, **kwargs)

    monkeypatch.setattr("app.config.character_studio._copy_workspace_asset", cancel_second)

    with pytest.raises(CharacterStudioOperationCancelled):
        service.import_portrait_folder(created["workspace_id"], source)

    portrait_dir = Path(created["package_dir"]) / "portraits"
    assert list(portrait_dir.iterdir()) == []
    assert service._read_state("batch")["imported_assets"] == []


def test_current_role_quiesces_before_the_first_directory_rename(tmp_path: Path) -> None:
    package = _write_character(tmp_path)
    service = CharacterStudioService(tmp_path)
    opened = service.open_character("sakura")
    doc = opened["doc"]
    doc["card_text"] = "replacement card"
    observed: list[str] = []

    def quiesce() -> None:
        observed.append((package / "card.md").read_text(encoding="utf-8"))

    service.save_character(
        doc,
        opened["workspace_id"],
        current_character_id="sakura",
        quiesce_current=quiesce,
    )

    assert observed == ["original card"]
    assert (package / "card.md").read_text(encoding="utf-8") == "replacement card"
