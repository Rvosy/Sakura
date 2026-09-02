from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import app.legacy_import.transaction as legacy_transaction
import app.legacy_import.files as legacy_files
import app.legacy_import.importer as legacy_importer
import app.legacy_import.inspector as legacy_inspector
import app.legacy_import.__main__ as legacy_cli
from app.config.settings_service import AppSettingsService
from app.legacy_import.configuration import (
    _migrate_mcp,
    _write_tts_plugin_config,
    migrate_configuration,
)
from app.legacy_import import LegacyImportError, inspect_legacy_installation, run_legacy_import
from app.legacy_import.files import (
    copy_file_checked,
    copy_tree_checked,
    copy_tree_fast_checked,
)
from app.legacy_import.history import import_history
from app.legacy_import.importer import (
    _build_artifact_manifest,
    _copy_memory,
    _copy_tts,
    _sanitize_tts_runtime_pth_files,
    _sanitize_tts_runtime_profiles,
    _validate_current_settings,
    _validate_memory,
)
from app.legacy_import.transaction import (
    PendingCommit,
    commit_payload,
    finalize_commit,
    recover_pending_commits,
    rollback_commit,
)
from app.storage.timeline import MAX_SEGMENTS, NewTimelineEntry, TimelineKind, TimelineStore


_REAL_PREPARE_MEMORY_MODEL = legacy_importer._prepare_memory_model


@pytest.fixture(autouse=True)
def _disable_real_memory_model_download(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ordinary importer tests must never turn into network tests."""

    monkeypatch.setattr(
        legacy_importer,
        "_prepare_memory_model",
        lambda *_args, **_kwargs: (0, 0),
    )


def test_cli_machine_protocol_is_ascii_safe_on_windows_code_pages(
    capsys: pytest.CaptureFixture[str],
) -> None:
    message = "包含中文的进度"
    legacy_cli._emit({"type": "progress", "message": message})

    encoded = capsys.readouterr().out.strip()
    assert encoded.isascii()
    assert json.loads(encoded) == {"type": "progress", "message": message}


def test_cli_keeps_runtime_console_output_out_of_machine_protocol(
    capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    inspection = SimpleNamespace(to_public_dict=lambda: {"compatible": True})

    def inspect_with_console_output(_source: Path, _target: Path) -> object:
        print("普通 Runtime 日志")
        os.write(1, "原生库输出".encode("utf-8"))
        return inspection

    monkeypatch.setattr(legacy_cli, "inspect_legacy_installation", inspect_with_console_output)

    assert legacy_cli.main(["inspect", "--source", ".", "--target", "."]) == 0

    captured = capfd.readouterr()
    assert json.loads(captured.out) == {
        "type": "inspection",
        "inspection": {"compatible": True},
    }
    assert "普通 Runtime 日志" in captured.err
    assert "原生库输出" in captured.err


def test_cli_streams_structured_diagnostics_to_rust_parent(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    inspection = SimpleNamespace(
        compatible=True,
        blockers=[],
        to_public_dict=lambda: {"compatible": True},
    )
    report = SimpleNamespace(to_public_dict=lambda: {"importId": "test-import-protocol"})

    def fake_run(
        *_args: object,
        diagnostic: object,
        **_kwargs: object,
    ) -> tuple[object, None]:
        assert callable(diagnostic)
        diagnostic(
            "legacy_import.memory_snapshot_failed",
            "不可信的子进程文案",
            {
                "diagnostic": "database disk image is malformed",
                "error_type": "DatabaseError",
                "reason_code": "SQLITE_CORRUPT",
                "stage": "quick_check",
            },
            "error",
        )
        return report, None

    monkeypatch.setattr(legacy_cli, "inspect_legacy_installation", lambda *_args: inspection)
    monkeypatch.setattr(legacy_cli, "run_legacy_import", fake_run)

    result = legacy_cli._run(
        SimpleNamespace(
            command="run",
            source=".",
            target=".",
            import_id="test-import-protocol",
        )
    )

    assert result == 0
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    diagnostic = next(record for record in records if record["type"] == "diagnostic")
    assert diagnostic == {
        "type": "diagnostic",
        "event": "legacy_import.memory_snapshot_failed",
        "message": "不可信的子进程文案",
        "attributes": {
            "diagnostic": "database disk image is malformed",
            "error_type": "DatabaseError",
            "reason_code": "SQLITE_CORRUPT",
            "stage": "quick_check",
        },
        "severity": "error",
    }


def test_cli_rejects_stale_overwrite_confirmation(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    inspection = SimpleNamespace(
        compatible=True,
        blockers=[],
        overwrite_domains=("配置", "聊天历史"),
        to_public_dict=lambda: {"compatible": True},
    )
    monkeypatch.setattr(legacy_cli, "inspect_legacy_installation", lambda *_args: inspection)

    result = legacy_cli._run(
        SimpleNamespace(
            command="run",
            source=".",
            target=".",
            import_id="test-import-confirmation",
            confirmed_overwrite_domain=["配置"],
        )
    )

    assert result == 2
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert records[-1]["error"]["code"] == "LEGACY_IMPORT_CONFIRMATION_STALE"


def _write_character(root: Path, character_id: str = "Sakura") -> None:
    package = root / "characters" / character_id
    (package / "voice" / "refs").mkdir(parents=True)
    (package / "card.md").write_text("persona", encoding="utf-8")
    (package / "portrait.png").write_bytes(b"png")
    (package / "voice" / "refs" / "neutral.wav").write_bytes(b"wav")
    (package / "voice" / "refs" / "ref.txt").write_text(
        "voice/refs/neutral.wav|JA|hello|中性\n", encoding="utf-8"
    )
    (package / "character.json").write_text(
        json.dumps(
            {
                "id": character_id,
                "display_name": character_id,
                "card": "card.md",
                "portrait": {"default": "portrait.png"},
                "voice": {
                    "tone_refs": "voice/refs/ref.txt",
                    "ref_lang": "ja",
                    "text_lang": "ja",
                },
            }
        ),
        encoding="utf-8",
    )


def _legacy_fixture(
    tmp_path: Path,
    version: str = "0.9.9",
    *,
    source_platform: str = "windows",
) -> Path:
    root = tmp_path / f"sakura-v{version}-{source_platform}"
    config = root / "data" / "config"
    history = root / "data" / "chat_history"
    memory = root / "data" / "memory"
    config.mkdir(parents=True)
    history.mkdir(parents=True)
    memory.mkdir(parents=True)
    (root / "tts").mkdir()
    (root / "plugins").mkdir()
    if source_platform == "windows":
        (root / "start.bat").write_text("@echo off\n", encoding="utf-8")
    elif source_platform == "macos":
        # 0.9.x source checkouts can contain the Windows launcher too. The
        # packaged runtime is the authoritative platform marker.
        (root / "start.bat").write_text("@echo off\n", encoding="utf-8")
        launcher = root / "scripts" / "start.command"
        launcher.parent.mkdir()
        launcher.write_text("#!/bin/bash\n", encoding="utf-8")
        runtime_python = root / "runtime" / "bin" / "python"
        runtime_python.parent.mkdir(parents=True)
        runtime_python.write_text("#!/bin/sh\n", encoding="utf-8")
        runtime_python.chmod(0o755)
    else:
        raise ValueError(f"unsupported fixture platform: {source_platform}")
    (root / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    _write_character(root)
    (config / "api.yaml").write_text(
        yaml.safe_dump(
            {
                "llm": {"base_url": "https://example.test/v1", "api_key": "secret-key", "model": "model-a"},
                "tts": {"provider": "gpt-sovits", "enabled": True, "gpt_sovits": {"timeout_seconds": 7}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    system_config = {
        "ui": {"subtitle_language": "ja", "always_on_top_enabled": True},
        "tool_loop": {"max_agent_steps_per_turn": 3},
    }
    if version == "0.9.8":
        system_config["config_version"] = 3
    elif version != "0.9.6":
        system_config["config_version"] = 4
    (config / "system_config.yaml").write_text(
        yaml.safe_dump(system_config, sort_keys=False), encoding="utf-8"
    )
    if version == "0.9.8":
        api_document = yaml.safe_load((config / "api.yaml").read_text(encoding="utf-8"))
        api_document["api_profiles"] = []
        api_document["model_slots"] = {"chat": {"profile_id": "", "model": ""}}
        (config / "api.yaml").write_text(
            yaml.safe_dump(api_document, sort_keys=False), encoding="utf-8"
        )
    (config / "characters.yaml").write_text("current_character_id: Sakura\n", encoding="utf-8")
    (config / "mcp.yaml").write_text("enabled: false\nservers: {}\n", encoding="utf-8")
    (config / "plugins.yaml").write_text("- id: sakura_mobile\n  enabled: true\n", encoding="utf-8")
    records = [
        {"created_at": "2026-06-01T10:00:00+08:00", "role": "user", "content": "hello [Sakura 已附加手动框选截图]"},
        {"created_at": "2026-06-01T10:00:01+08:00", "role": "assistant", "content": "a", "translation": "甲", "tone": "中性", "portrait": "neutral"},
        {"created_at": "2026-06-01T10:00:02+08:00", "role": "assistant", "content": "b", "translation": "乙", "tone": "开心", "portrait": "smile"},
        {"created_at": "2026-06-01T10:00:03+08:00", "role": "error", "content": "provider failed"},
        {"created_at": "2026-06-01T11:00:00+08:00", "role": "system", "content": "[已抓取屏幕上下文]"},
        {"created_at": "2026-06-01T11:00:01+08:00", "role": "assistant", "content": "proactive", "translation": "", "tone": "中性", "portrait": "neutral"},
    ]
    (history / "Sakura.jsonl").write_text(
        "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in records), encoding="utf-8"
    )
    (root / "data" / "memory_curation_state.json").write_text(
        json.dumps({"processed_history_count": 3, "pending_turns": 0}), encoding="utf-8"
    )
    with sqlite3.connect(memory / "mem0_history.db") as connection:
        connection.execute(
            "CREATE TABLE history (id TEXT PRIMARY KEY, memory_id TEXT, old_memory TEXT, "
            "new_memory TEXT, event TEXT, created_at DATETIME, updated_at DATETIME, "
            "is_deleted INTEGER, actor_id TEXT, role TEXT)"
        )
        connection.execute(
            "CREATE TABLE messages (id TEXT PRIMARY KEY, session_scope TEXT, role TEXT, "
            "content TEXT, name TEXT, created_at DATETIME)"
        )
    return root


_RETIRED_MODEL_SELECTION_FIELDS = {
    "model_names",
    "text_enabled",
    "text_profile_id",
    "text_model",
    "vision_profile_id",
    "vision_model",
}


def _migrate_api_document(
    tmp_path: Path,
    document: dict[str, object],
    *,
    env_text: str | None = None,
) -> tuple[dict[str, object], AppSettingsService]:
    source = tmp_path / "source"
    config = source / "data/config"
    config.mkdir(parents=True)
    (config / "api.yaml").write_text(
        yaml.safe_dump(document, sort_keys=False),
        encoding="utf-8",
    )
    if env_text is not None:
        (source / ".env").write_text(env_text, encoding="utf-8")
    staged = tmp_path / "staged"

    migrate_configuration(source, staged, new_tts_root=staged / "tts")
    _validate_current_settings(staged)

    migrated = yaml.safe_load((staged / "config/api.yaml").read_text(encoding="utf-8"))
    assert isinstance(migrated, dict)
    return migrated, AppSettingsService(staged)




def test_configuration_import_keeps_nonempty_yaml_ahead_of_legacy_env(
    tmp_path: Path,
) -> None:
    migrated, service = _migrate_api_document(
        tmp_path,
        {
            "llm": {
                "base_url": "https://yaml.example/v1",
                "api_key": "fixture-yaml-credential",
                "model": "yaml-model",
            }
        },
        env_text=(
            "BASE_URL=https://env.example/v1\n"
            "API_KEY=fixture-env-credential\n"
            "MODEL=env-model\n"
        ),
    )

    assert migrated["llm"] == {
        "base_url": "https://yaml.example/v1",
        "api_key": "fixture-yaml-credential",
        "model": "yaml-model",
    }
    providers = service.load_api_profiles()
    selection = service.load_model_selection()
    assert hashlib.sha256(providers[0].api_key.encode()).digest() == hashlib.sha256(
        b"fixture-yaml-credential"
    ).digest()
    assert providers[0].base_url == "https://yaml.example/v1"
    assert selection.chat.model == "yaml-model"




@pytest.mark.parametrize(
    ("legacy_models", "expected_text_model", "expected_vision_model"),
    [
        (
            {
                "model_names": ["text-model", "vision-model"],
                "text_model": "text-model",
                "vision_model": "vision-model",
            },
            "text-model",
            "vision-model",
        ),
        ({}, "gpt-4.1-mini", "gpt-4o"),
    ],
    ids=("explicit-models", "historical-defaults"),
)
def test_configuration_import_converts_pr110_selection_without_model_slots(
    tmp_path: Path,
    legacy_models: dict[str, object],
    expected_text_model: str,
    expected_vision_model: str,
) -> None:
    migrated, service = _migrate_api_document(
        tmp_path,
        {
            "api_profiles": [
                {
                    "id": "text-provider",
                    "alias": "文本 Provider",
                    "base_url": "https://text.example/v1",
                    "api_key": "text-provider-secret",
                    "provider_extension": "kept",
                },
                {
                    "id": "vision-provider",
                    "alias": "视觉 Provider",
                    "base_url": "https://vision.example/v1",
                    "api_key": "vision-provider-secret",
                },
            ],
            "text_enabled": True,
            "text_profile_id": "text-provider",
            "vision_profile_id": "vision-provider",
            **legacy_models,
        },
    )

    assert not (_RETIRED_MODEL_SELECTION_FIELDS & migrated.keys())
    assert migrated["model_slots"] == {
        "chat": {"profile_id": "text-provider", "model": expected_text_model},
        "vision_chat": {
            "profile_id": "vision-provider",
            "model": expected_vision_model,
        },
    }
    providers = migrated["api_profiles"]
    assert isinstance(providers, list)
    assert [provider["id"] for provider in providers] == ["text-provider", "vision-provider"]
    assert providers[0]["provider_extension"] == "kept"
    assert providers[0]["models"] == [
        {"name": expected_text_model},
        {"name": expected_vision_model},
    ]

    loaded_providers = service.load_api_profiles()
    selection = service.load_model_selection()
    assert loaded_providers[0].models == (expected_text_model, expected_vision_model)
    assert hashlib.sha256(loaded_providers[0].api_key.encode()).digest() == hashlib.sha256(
        b"text-provider-secret"
    ).digest()
    assert selection.chat.profile_id == "text-provider"
    assert selection.chat.model == expected_text_model
    assert selection.vision_chat is not None
    assert selection.vision_chat.profile_id == "vision-provider"
    assert selection.vision_chat.model == expected_vision_model






def test_exception_diagnostics_do_not_include_free_form_private_messages() -> None:
    attributes = legacy_importer._exception_log_attributes(
        RuntimeError("private config value and absolute path")
    )

    assert attributes["diagnostic"] == "RuntimeError"
    assert "private config value" not in json.dumps(attributes)


def _macos_legacy_fixture(tmp_path: Path) -> Path:
    root = _legacy_fixture(tmp_path, source_platform="macos")
    (root / "tts").rmdir()
    bundle = root / "data/tts_bundles/installed/gpt_sovits_macos"
    work_dir = bundle / "GPT-SoVITS"
    (work_dir / "GPT_SoVITS/configs").mkdir(parents=True)
    (work_dir / "api_v2.py").write_text("# legacy runtime\n", encoding="utf-8")
    (work_dir / "GPT_SoVITS/configs/tts_infer_sakura_macos.yaml").write_text(
        yaml.safe_dump(
            {
                "custom": {
                    "version": "v2ProPlus",
                    "t2s_weights_path": "/old/sakura/characters/sakura/model.ckpt",
                    "vits_weights_path": "/old/sakura/characters/sakura/model.pth",
                },
                "v2ProPlus": {
                    "t2s_weights_path": "GPT_SoVITS/pretrained_models/s1v3.ckpt",
                    "vits_weights_path": (
                        "GPT_SoVITS/pretrained_models/v2Pro/s2Gv2ProPlus.pth"
                    ),
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    runtime_bin = bundle / "miniforge3/envs/gpt-sovits310/bin"
    runtime_bin.mkdir(parents=True)
    runtime_python = runtime_bin / "python3.10"
    runtime_python.write_text("#!/bin/sh\n", encoding="utf-8")
    runtime_python.chmod(0o755)
    if os.name != "nt":
        (runtime_bin / "python").symlink_to("python3.10")
        absolute_alias = work_dir / "GPT_weights_v2ProPlus/legacy.ckpt"
        absolute_alias.parent.mkdir(parents=True)
        absolute_alias.symlink_to(
            "/old/sakura/characters/sakura/voice/models/legacy.ckpt"
        )

    api_path = root / "data/config/api.yaml"
    api = yaml.safe_load(api_path.read_text(encoding="utf-8"))
    api["tts"] = {
        "provider": "custom-gpt-sovits",
        "enabled": True,
        "gpt_sovits": {
            "api_url": "http://127.0.0.1:9880/tts",
            "work_dir": "data/tts_bundles/installed/gpt_sovits_macos/GPT-SoVITS",
            "python_path": (
                "data/tts_bundles/installed/gpt_sovits_macos/"
                "miniforge3/envs/gpt-sovits310/bin/python3.10"
            ),
            "tts_config_path": "",
            "timeout_seconds": 120,
        },
    }
    api_path.write_text(yaml.safe_dump(api, sort_keys=False), encoding="utf-8")
    return root


def _tree_state(root: Path) -> dict[str, tuple[int, int, str]]:
    state: dict[str, tuple[int, int, str]] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        stat = path.stat()
        state[path.relative_to(root).as_posix()] = (
            stat.st_size,
            stat.st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    return state


def _install_fake_memory_model_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[object, list[str]]:
    import plugins.builtin.sakura_mem0.memory as memory_runtime

    calls: list[str] = []

    def snapshot_for(cache_dir: Path) -> Path:
        return (
            Path(cache_dir)
            / memory_runtime.DEFAULT_EMBEDDING_MODEL_CACHE_NAME
            / "snapshots"
            / memory_runtime.DEFAULT_EMBEDDING_ARTIFACT_REVISION
        )

    def fake_snapshot(
        _model_name: str,
        _base_dir: Path | None = None,
        *,
        cache_dir: Path | None = None,
    ) -> Path | None:
        assert cache_dir is not None
        snapshot = snapshot_for(cache_dir)
        return snapshot if (snapshot / "model.onnx").is_file() else None

    def fake_cached(
        model_name: str,
        base_dir: Path | None = None,
        *,
        cache_dir: Path | None = None,
    ) -> bool:
        return fake_snapshot(model_name, base_dir, cache_dir=cache_dir) is not None

    def fake_validate(snapshot: Path) -> None:
        assert all(
            (snapshot / name).is_file()
            for name in memory_runtime.DEFAULT_EMBEDDING_MODEL_REQUIRED_FILES
        )

    def write_model(cache_dir: Path) -> Path:
        snapshot = snapshot_for(cache_dir)
        snapshot.mkdir(parents=True, exist_ok=True)
        for index, name in enumerate(
            memory_runtime.DEFAULT_EMBEDDING_MODEL_REQUIRED_FILES,
            start=1,
        ):
            (snapshot / name).write_bytes((name + str(index)).encode("utf-8"))
        return snapshot.parents[1]

    def fake_download(
        _base_dir: Path | None = None,
        *,
        cache_dir: Path | None = None,
        progress: object = None,
        cancel: object = None,
    ) -> object:
        assert cache_dir is not None
        assert cancel is not None and not cancel.is_set()
        calls.append("memory-model-download")
        if callable(progress):
            progress("connecting", 5)
            progress("downloading", 50)
            progress("completed", 100)
        model_dir = write_model(cache_dir)
        return SimpleNamespace(model_dir=model_dir)

    monkeypatch.setattr(memory_runtime, "_embedding_model_snapshot", fake_snapshot)
    monkeypatch.setattr(memory_runtime, "_embedding_model_cached", fake_cached)
    monkeypatch.setattr(
        memory_runtime,
        "_validate_fastembed_snapshot_artifacts",
        fake_validate,
    )
    monkeypatch.setattr(memory_runtime, "download_embedding_model", fake_download)
    return SimpleNamespace(write_model=write_model), calls








def test_partial_legacy_source_without_config_imports_surviving_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _legacy_fixture(tmp_path, source_platform="windows")
    shutil.rmtree(source / "data/config")
    target = tmp_path / "target"
    target.mkdir()
    monkeypatch.setattr(legacy_inspector.platform, "system", lambda: "Windows")

    report, pending = run_legacy_import(
        source,
        target,
        import_id="partial-without-config",
        finalize=True,
    )

    assert pending is None
    entries = TimelineStore(target / "data/chat_history/timeline.sqlite3").read_all(
        "Sakura"
    )
    assert entries
    assert (target / "config/api.yaml").is_file()
    assert report.counts["configCompatibilityFallbacks"] >= 1
    assert any(
        warning["code"] == "LEGACY_CONFIGURATION_COMPATIBILITY_APPLIED"
        for warning in report.warnings
    )


def test_inspection_rejects_a_1_0x_target_inside_the_0_9x_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _legacy_fixture(tmp_path, source_platform="windows")
    target = source / "runtime-v2"
    target.mkdir()
    monkeypatch.setattr(legacy_inspector.platform, "system", lambda: "Windows")

    inspection = inspect_legacy_installation(source, target)

    assert not inspection.compatible
    assert "LEGACY_SOURCE_TARGET_OVERLAP" in {
        str(blocker["code"]) for blocker in inspection.blockers
    }


def test_inspection_reports_transformed_configuration_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _legacy_fixture(tmp_path, source_platform="windows")
    target = tmp_path / "target"
    (target / "config").mkdir(parents=True)
    (target / "config/ui.json").write_text(
        '{"always_on_top": false}\n', encoding="utf-8"
    )
    monkeypatch.setattr(legacy_inspector.platform, "system", lambda: "Windows")

    inspection = inspect_legacy_installation(source, target)

    assert inspection.compatible
    assert "配置" in inspection.overwrite_domains


def test_inspection_rejects_cross_platform_legacy_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _macos_legacy_fixture(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    monkeypatch.setattr(legacy_inspector.platform, "system", lambda: "Windows")

    inspection = inspect_legacy_installation(source, target)

    assert not inspection.compatible
    assert inspection.source_platform == "macos"
    assert "LEGACY_CROSS_PLATFORM_UNSUPPORTED" in {
        str(blocker["code"]) for blocker in inspection.blockers
    }


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_macos_import_copies_managed_tts_and_preserves_safe_links(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _macos_legacy_fixture(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    monkeypatch.setattr(legacy_inspector.platform, "system", lambda: "Darwin")
    before = _tree_state(source)

    report, pending = run_legacy_import(
        source,
        target,
        import_id="test-macos-import",
        finalize=True,
    )

    assert pending is None
    assert _tree_state(source) == before
    bundle = target / "tts/gpt_sovits_macos"
    assert (bundle / "GPT-SoVITS/api_v2.py").is_file()
    copied_python = bundle / "miniforge3/envs/gpt-sovits310/bin/python3.10"
    assert copied_python.stat().st_mode & stat.S_IXUSR
    assert (copied_python.parent / "python").is_symlink()
    assert not os.path.lexists(
        bundle / "GPT-SoVITS/GPT_weights_v2ProPlus/legacy.ckpt"
    )
    assert any(
        warning["code"] == "LEGACY_TTS_ABSOLUTE_LINKS_SKIPPED"
        and warning["items"] == 1
        for warning in report.warnings
    )
    config = json.loads(
        (target / "data/plugins/sakura.tts.gpt-sovits/config.json").read_text(
            encoding="utf-8"
        )
    )
    assert Path(config["workDir"]) == bundle / "GPT-SoVITS"
    assert Path(config["pythonPath"]) == copied_python
    assert Path(config["ttsConfigPath"]) == (
        bundle / "GPT-SoVITS/GPT_SoVITS/configs/tts_infer_sakura_macos.yaml"
    )
    migrated_profile = yaml.safe_load(
        Path(config["ttsConfigPath"]).read_text(encoding="utf-8")
    )
    assert migrated_profile["custom"]["t2s_weights_path"] == (
        migrated_profile["v2ProPlus"]["t2s_weights_path"]
    )
    assert migrated_profile["custom"]["vits_weights_path"] == (
        migrated_profile["v2ProPlus"]["vits_weights_path"]
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_internal_symlink_copy_rejects_lexical_escape(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "escape").symlink_to("../outside")

    with pytest.raises(LegacyImportError) as raised:
        copy_tree_checked(
            source,
            tmp_path / "target",
            cancelled=lambda: False,
            preserve_internal_symlinks=True,
        )

    assert raised.value.code == "LEGACY_NESTED_LINK_UNSUPPORTED"




def test_memory_copy_uses_consistent_sqlite_snapshot_with_open_wal(tmp_path: Path) -> None:
    source = tmp_path / "legacy"
    memory = source / "data/memory"
    memory.mkdir(parents=True)
    database = memory / "mem0_history.db"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        "CREATE TABLE history (id TEXT PRIMARY KEY, memory_id TEXT, event TEXT)"
    )
    connection.execute(
        "INSERT INTO history VALUES ('history-one', 'memory-one', 'ADD')"
    )
    connection.commit()
    source_before = _tree_state(memory)
    source_before.pop("mem0_history.db-shm", None)
    payload = tmp_path / "payload"

    source_path = (
        Path(f"\\\\?\\{source}")
        if __import__("platform").system() == "Windows"
        else source
    )
    files, size = _copy_memory(source_path, payload, lambda: False)

    copied = payload / "data/memory/mem0_history.db"
    assert files == 1
    assert size == copied.stat().st_size
    assert not Path(f"{copied}-wal").exists()
    assert not Path(f"{copied}-shm").exists()
    with sqlite3.connect(copied) as copied_connection:
        assert copied_connection.execute(
            "SELECT memory_id, event FROM history WHERE id = 'history-one'"
        ).fetchone() == ("memory-one", "ADD")
    source_after = _tree_state(memory)
    source_after.pop("mem0_history.db-shm", None)
    assert source_after == source_before
    connection.close()




def test_memory_validation_uses_runtime_schema_migration_for_legacy_variants(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source-memory"
    source.mkdir()
    source_database = source / "mem0_history.db"
    with sqlite3.connect(source_database) as connection:
        connection.execute(
            "CREATE TABLE history (id TEXT PRIMARY KEY, memory_id TEXT, "
            "event TEXT, user_id TEXT)"
        )
        connection.execute(
            "INSERT INTO history VALUES ('history-one', 'memory-one', 'ADD', 'legacy-user')"
        )
        connection.execute(
            "CREATE TABLE messages (id TEXT PRIMARY KEY, role TEXT, content TEXT)"
        )
        connection.execute(
            "INSERT INTO messages VALUES ('message-one', 'human', 'cached prompt')"
        )
    source_before = _tree_state(source)
    staged = tmp_path / "staged-memory"
    copy_tree_checked(source, staged, cancelled=lambda: False)

    _validate_memory(staged)

    with sqlite3.connect(staged / "mem0_history.db") as connection:
        history_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(history)")
        }
        message_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(messages)")
        }
        assert {
            "id",
            "memory_id",
            "old_memory",
            "new_memory",
            "event",
            "created_at",
            "updated_at",
            "is_deleted",
            "actor_id",
            "role",
            "user_id",
        }.issubset(history_columns)
        assert {
            "id",
            "session_scope",
            "role",
            "content",
            "name",
            "created_at",
        }.issubset(message_columns)
        assert connection.execute(
            "SELECT memory_id, event, user_id FROM history WHERE id = 'history-one'"
        ).fetchone() == ("memory-one", "ADD", "legacy-user")
        assert connection.execute(
            "SELECT role, content FROM messages WHERE id = 'message-one'"
        ).fetchone() == ("human", "cached prompt")
    assert _tree_state(source) == source_before


def test_memory_validation_rebuilds_only_unidentifiable_message_cache(
    tmp_path: Path,
) -> None:
    database = tmp_path / "staged-memory" / "mem0_history.db"
    database.parent.mkdir()
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE history (id TEXT PRIMARY KEY, memory_id TEXT, old_memory TEXT, "
            "new_memory TEXT, event TEXT)"
        )
        connection.execute("CREATE TABLE messages (role TEXT, content TEXT)")
        connection.execute("INSERT INTO messages VALUES ('human', 'disposable cache')")

    _validate_memory(database.parent)

    with sqlite3.connect(database) as connection:
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(messages)")
        }
        assert columns == {
            "id",
            "session_scope",
            "role",
            "content",
            "name",
            "created_at",
        }
        assert connection.execute("SELECT COUNT(*) FROM messages").fetchone() == (0,)


@pytest.mark.skipif(__import__("platform").system() != "Windows", reason="v1 supports Windows imports")
def test_import_quarantines_unreadable_memory_history_without_losing_timeline(
    tmp_path: Path,
) -> None:
    source = _legacy_fixture(tmp_path)
    source_database = source / "data/memory/mem0_history.db"
    source_database.write_bytes(b"not a sqlite database")
    source_before = _tree_state(source)
    target = tmp_path / "target"
    target.mkdir()

    diagnostics: list[tuple[str, str, dict[str, object], str]] = []
    report, pending = run_legacy_import(
        source,
        target,
        import_id="test-memory-required",
        diagnostic=lambda event, message, attributes, severity: diagnostics.append(
            (event, message, dict(attributes), severity)
        ),
        finalize=True,
    )

    assert pending is None
    assert any(
        warning["code"] == "LEGACY_MEMORY_RECORDS_QUARANTINED"
        for warning in report.warnings
    )
    assert _tree_state(source) == source_before
    assert not (target / "data/memory/mem0_history.db").exists()
    assert (
        target
        / "data/legacy-imports/test-memory-required/quarantine/memory/mem0_history.db"
    ).read_bytes() == b"not a sqlite database"
    TimelineStore(target / "data/chat_history/timeline.sqlite3").assert_activated()
    assert not (tmp_path / "logs/sakura-runtime.log").exists()
    _event, _message, attributes, severity = next(
        record for record in diagnostics if record[0] == "legacy_import.memory_snapshot_failed"
    )
    assert severity == "error"
    assert attributes["detail_stage"] == "open_source"
    assert attributes["error_type"] == "DatabaseError"
    assert attributes["reason_code"] == "SQLITE_NOTADB"
    assert attributes["stage"] == "open_source"
    assert attributes["sqlite_errorname"] == "SQLITE_NOTADB"


@pytest.mark.skipif(__import__("platform").system() != "Windows", reason="v1 supports Windows imports")
def test_import_converts_timeline_configuration_characters_and_memory(tmp_path: Path) -> None:
    source = _legacy_fixture(tmp_path)
    system_config_path = source / "data/config/system_config.yaml"
    system_config = yaml.safe_load(system_config_path.read_text(encoding="utf-8"))
    system_config["screen_awareness"] = {
        "enabled": True,
        "screen_context_enabled": False,
        "check_interval_minutes": 2,
    }
    system_config_path.write_text(
        yaml.safe_dump(system_config, sort_keys=False), encoding="utf-8"
    )
    (source / "data/config/mcp.yaml").write_text(
        """\
enabled: true
servers:
  web:
    transport: stdio
    command: "{python}"
    args: ["{base_dir}/app/agent/mcp/web_search_server.py"]
""",
        encoding="utf-8",
    )
    source_manifest_path = source / "characters/Sakura/character.json"
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    source_manifest["theme"] = {
        "source": "compat_default",
        "primary_color": "#d55b91",
    }
    source_manifest_path.write_text(json.dumps(source_manifest), encoding="utf-8")
    target = tmp_path / "target"
    (target / "config").mkdir(parents=True)
    (target / "config" / "ui.json").write_text(
        json.dumps({"schema_version": 1, "domain": "ui", "settings": {}}), encoding="utf-8"
    )
    before = _tree_state(source)
    progress_updates: list[tuple[str, int, str]] = []
    diagnostics: list[tuple[str, dict[str, object], str]] = []

    inspection = inspect_legacy_installation(source, target)
    assert inspection.compatible
    report, pending = run_legacy_import(
        source,
        target,
        import_id="test-import-0001",
        finalize=True,
        progress=lambda stage, percent, message: progress_updates.append(
            (stage, percent, message)
        ),
        diagnostic=lambda event, _message, attributes, severity: diagnostics.append(
            (event, dict(attributes), severity)
        ),
    )

    assert pending is None
    assert _tree_state(source) == before
    assert report.counts["historyErrorsQuarantined"] == 1
    entries = TimelineStore(target / "data/chat_history/timeline.sqlite3").read_all("Sakura")
    assert [entry.kind for entry in entries] == [
        TimelineKind.HUMAN,
        TimelineKind.OBSERVATION,
        TimelineKind.ASSISTANT,
        TimelineKind.OBSERVATION,
        TimelineKind.ASSISTANT,
    ]
    assert entries[0].payload == {"text": "hello"}
    assert [segment["text"] for segment in entries[2].payload["segments"]] == ["a", "b"]
    assert entries[-1].origin == "proactive"
    manifest = json.loads((target / "characters/Sakura/character.json").read_text(encoding="utf-8"))
    assert manifest["extensions"]["sakura.tts"]["provider"] == "sakura.tts.gpt-sovits"
    assert manifest["extensions"]["sakura.tts.genie"]["toneRefs"] == "voice/refs/ref.txt"
    assert manifest["theme"]["source"] == "package"
    assert manifest["theme"]["primary_color"] == "#d55b91"
    assert progress_updates
    assert all(0 <= percent <= 100 for _stage, percent, _message in progress_updates)
    completed_stages = {
        attributes["stage"]
        for event, attributes, _severity in diagnostics
        if event == "legacy_import.stage_completed"
    }
    assert completed_stages == {
        "history",
        "memory",
        "configuration",
        "auxiliary",
        "core_payload_validation",
        "characters",
        "tts",
        "manifest",
        "commit",
    }
    history_log = next(
        attributes
        for event, attributes, _severity in diagnostics
        if event == "legacy_import.stage_completed"
        and attributes.get("stage") == "history"
    )
    assert history_log["source_records"] == 6
    assert history_log["timeline_entries"] == 5
    api = yaml.safe_load((target / "config/api.yaml").read_text(encoding="utf-8"))
    assert api["api_profiles"][0]["api_key"] == "secret-key"
    migrated_system = yaml.safe_load(
        (target / "config/system_config.yaml").read_text(encoding="utf-8")
    )
    assert migrated_system["screen_awareness"] == {
        "enabled": False,
        "check_interval_minutes": 2,
    }
    migrated_mcp = yaml.safe_load((target / "config/mcp.yaml").read_text(encoding="utf-8"))
    assert migrated_mcp["servers"]["web"]["args"] == [
        "{core_root}/app/agent/mcp/web_search_server.py"
    ]
    report_text = (target / "data/legacy-imports/test-import-0001/report.json").read_text(encoding="utf-8")
    assert "secret-key" not in report_text
    assert str(source) not in report_text
    report_payload = json.loads(report_text)
    assert report_payload["artifacts"]
    assert all(
        len(artifact["sha256"]) == 64 and not Path(artifact["id"]).is_absolute()
        for artifact in report_payload["artifacts"]
    )
    assert not (target / "data/memory/curation_state").exists()




@pytest.mark.skipif(__import__("platform").system() != "Windows", reason="v1 supports Windows imports")
def test_memory_model_preparation_failure_preserves_imported_memory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import plugins.builtin.sakura_mem0.memory as memory_runtime

    source = _legacy_fixture(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    existing = target / "existing.txt"
    existing.write_text("keep", encoding="utf-8")
    _install_fake_memory_model_runtime(monkeypatch)

    def fail_download(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("private downloader detail")

    monkeypatch.setattr(memory_runtime, "download_embedding_model", fail_download)
    monkeypatch.setattr(
        legacy_importer,
        "_prepare_memory_model",
        _REAL_PREPARE_MEMORY_MODEL,
    )

    report, pending = run_legacy_import(
        source,
        target,
        import_id="test-memory-model-failure",
        finalize=True,
    )

    assert pending is None
    assert any(
        warning["code"] == "LEGACY_MEMORY_MODEL_PREPARATION_SKIPPED"
        for warning in report.warnings
    )
    assert existing.read_text(encoding="utf-8") == "keep"
    assert (target / "data/memory/mem0_history.db").is_file()
    assert not list(target.glob(".legacy-import-staging-*"))
    assert not list(target.glob(".legacy-import-journal-*"))








def test_first_import_merges_and_preserves_all_atomic_target_trees(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _legacy_fixture(tmp_path)
    (source / "tts/onnx").mkdir()
    (source / "tts/onnx/new-resource.bin").write_bytes(b"legacy tts")
    source_memory = source / "data/memory"
    (source_memory / "legacy-extra.bin").write_bytes(b"legacy extra")
    with sqlite3.connect(source_memory / "mem0_history.db") as connection:
        connection.execute("ALTER TABLE history ADD COLUMN user_id TEXT")
        connection.execute(
            "INSERT INTO history (id, memory_id, event, user_id) VALUES (?, ?, ?, ?)",
            ("shared-memory-event", "legacy-point", "legacy-event", "Sakura"),
        )
    (source_memory / "core_profiles.json").write_text(
        json.dumps({"Sakura": {"content": "legacy profile"}}),
        encoding="utf-8",
    )

    converted = tmp_path / "converted-for-identities"
    import_history(source, converted, character_ids=("Sakura",))
    source_entries = TimelineStore(
        converted / "data/chat_history/timeline.sqlite3"
    ).read_all("Sakura")
    shared_entry = source_entries[0]

    target = tmp_path / "target"
    _write_character(target, "Existing")
    (target / "tts").mkdir()
    (target / "tts/old-resource.bin").write_bytes(b"target tts")
    target_timeline = TimelineStore(target / "data/chat_history/timeline.sqlite3")
    target_timeline.initialize()
    target_timeline.append(
        NewTimelineEntry(
            entry_id="target-only-entry",
            turn_id="target-only-turn",
            character_id="Existing",
            kind=TimelineKind.HUMAN,
            origin="chat",
            created_at="2026-05-01T00:00:00+08:00",
            payload={"text": "target only"},
        )
    )
    target_timeline.append(
        NewTimelineEntry(
            entry_id=shared_entry.entry_id,
            turn_id=shared_entry.turn_id,
            character_id="Sakura",
            kind=TimelineKind.HUMAN,
            origin="chat",
            created_at=shared_entry.created_at,
            payload={"text": "target conflict"},
        )
    )
    target_memory = target / "data/memory"
    target_memory.mkdir(parents=True)
    with sqlite3.connect(target_memory / "mem0_history.db") as connection:
        connection.execute(
            "CREATE TABLE history (id TEXT PRIMARY KEY, memory_id TEXT, event TEXT, user_id TEXT, target_extension TEXT)"
        )
        connection.execute(
            "INSERT INTO history VALUES (?, ?, ?, ?, ?)",
            (
                "target-only-memory-event",
                "target-point",
                "target-event",
                "Existing",
                "kept",
            ),
        )
        connection.execute(
            "INSERT INTO history VALUES (?, ?, ?, ?, ?)",
            (
                "shared-memory-event",
                "legacy-point",
                "target-event",
                "Sakura",
                "target-only-column",
            ),
        )
    # sqlite3's context manager commits but does not close. The production
    # importer stops Core before the atomic directory rename, so release this
    # in-process fixture handle to model the same lifecycle on Windows.
    connection.close()
    (target_memory / "core_profiles.json").write_text(
        json.dumps({"Existing": {"content": "target profile"}}),
        encoding="utf-8",
    )
    (target_memory / "target-extra.bin").write_bytes(b"target extra")
    curation = target_memory / "curation_state/Existing.json"
    curation.parent.mkdir()
    curation.write_text('{"curation_cursor":"stale"}\n', encoding="utf-8")
    before = _tree_state(source)
    monkeypatch.setattr(legacy_inspector.platform, "system", lambda: "Windows")
    inspection = inspect_legacy_installation(source, target)

    assert inspection.compatible
    assert set(inspection.overwrite_domains) == {"聊天历史", "长期记忆"}

    report, pending = run_legacy_import(
        source,
        target,
        import_id="test-preserving-first-import",
        inspection=inspection,
        finalize=True,
    )

    assert pending is None
    assert report.counts["timelineEntries"] == len(source_entries)
    assert _tree_state(source) == before
    assert (target / "characters/Existing/character.json").is_file()
    assert (target / "characters/Sakura/character.json").is_file()
    assert (target / "tts/old-resource.bin").read_bytes() == b"target tts"
    assert (target / "tts/onnx/new-resource.bin").read_bytes() == b"legacy tts"
    merged_timeline = TimelineStore(target / "data/chat_history/timeline.sqlite3")
    assert [entry.payload for entry in merged_timeline.read_all("Existing")] == [
        {"text": "target only"}
    ]
    assert merged_timeline.read_all("Sakura")[0].payload == {"text": "hello"}
    with sqlite3.connect(target_memory / "mem0_history.db") as connection:
        target_only = connection.execute(
            "SELECT event, user_id, target_extension FROM history WHERE id = ?",
            ("target-only-memory-event",),
        ).fetchone()
        overwritten = connection.execute(
            "SELECT event, user_id, target_extension FROM history WHERE id = ?",
            ("shared-memory-event",),
        ).fetchone()
    assert target_only == ("target-event", "Existing", "kept")
    assert overwritten == ("legacy-event", "Sakura", "target-only-column")
    profiles = json.loads((target_memory / "core_profiles.json").read_text(encoding="utf-8"))
    assert set(profiles) == {"Existing", "Sakura"}
    assert (target_memory / "target-extra.bin").read_bytes() == b"target extra"
    assert (target_memory / "legacy-extra.bin").read_bytes() == b"legacy extra"
    assert not (target_memory / "curation_state").exists()






def test_first_import_never_overwrites_cross_role_timeline_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _legacy_fixture(tmp_path)
    converted = tmp_path / "converted-for-cross-role"
    import_history(source, converted, character_ids=("Sakura",))
    source_entry = TimelineStore(
        converted / "data/chat_history/timeline.sqlite3"
    ).read_all("Sakura")[0]
    target = tmp_path / "target"
    target.mkdir()
    timeline = TimelineStore(target / "data/chat_history/timeline.sqlite3")
    timeline.initialize()
    timeline.append(
        NewTimelineEntry(
            entry_id=source_entry.entry_id,
            turn_id=source_entry.turn_id,
            character_id="Beta",
            kind=TimelineKind.HUMAN,
            origin="chat",
            created_at=source_entry.created_at,
            payload={"text": "beta owns this identity"},
        )
    )
    before = _tree_state(target)
    monkeypatch.setattr(legacy_inspector.platform, "system", lambda: "Windows")

    with pytest.raises(LegacyImportError, match="LEGACY_DATA_SCOPE_CONFLICT"):
        run_legacy_import(
            source,
            target,
            import_id="test-first-import-cross-role",
            finalize=True,
        )

    assert _tree_state(target) == before
    assert not list(target.glob(".legacy-import-*"))


def test_first_import_rejects_unreadable_target_memory_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _legacy_fixture(tmp_path, source_platform="windows")
    target = tmp_path / "target"
    target_memory = target / "data/memory"
    target_memory.mkdir(parents=True)
    (target_memory / "mem0_history.db").write_bytes(b"not a sqlite database")
    before = _tree_state(target)
    monkeypatch.setattr(legacy_inspector.platform, "system", lambda: "Windows")

    inspection = inspect_legacy_installation(source, target)

    assert not inspection.compatible
    assert "LEGACY_DATA_TARGET_MEMORY_INVALID" in {
        str(blocker["code"]) for blocker in inspection.blockers
    }
    with pytest.raises(LegacyImportError, match="LEGACY_DATA_TARGET_MEMORY_INVALID"):
        run_legacy_import(
            source,
            target,
            import_id="target-memory-invalid",
            inspection=inspection,
            finalize=True,
        )
    assert _tree_state(target) == before
    assert not list(target.glob(".legacy-import-*"))


@pytest.mark.skipif(__import__("platform").system() != "Windows", reason="v1 supports Windows imports")
def test_invalid_character_package_is_skipped_after_timeline_and_memory(
    tmp_path: Path,
) -> None:
    source = _legacy_fixture(tmp_path)
    (source / "characters/Sakura/character.json").write_text("not json", encoding="utf-8")
    target = tmp_path / "target"
    target.mkdir()

    report, pending = run_legacy_import(
        source,
        target,
        import_id="test-optional-character",
        finalize=True,
    )

    assert pending is None
    assert report.counts["charactersSkipped"] == 1
    assert any(
        warning["code"] == "LEGACY_CHARACTER_IMPORT_SKIPPED"
        for warning in report.warnings
    )
    assert not (target / "characters").exists()
    TimelineStore(target / "data/chat_history/timeline.sqlite3").assert_activated()
    entries = TimelineStore(target / "data/chat_history/timeline.sqlite3").read_all("Sakura")
    assert entries
    assert (target / "data/memory/mem0_history.db").is_file()


@pytest.mark.skipif(__import__("platform").system() != "Windows", reason="v1 supports Windows imports")
def test_tts_copy_failure_is_warning_and_keeps_core_character_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _legacy_fixture(tmp_path)
    target = tmp_path / "target"
    target.mkdir()

    def fail_tts(*_args: object, **_kwargs: object) -> tuple[int, int]:
        raise LegacyImportError("LEGACY_COPY_FAILED", "staging")

    monkeypatch.setattr(legacy_importer, "_copy_tts", fail_tts)
    report, pending = run_legacy_import(
        source,
        target,
        import_id="test-optional-tts",
        finalize=True,
    )

    assert pending is None
    assert report.counts["ttsSkipped"] == 1
    assert any(
        warning["code"] == "LEGACY_TTS_IMPORT_SKIPPED" and warning["reasonCode"] == "LEGACY_COPY_FAILED"
        for warning in report.warnings
    )
    assert not (target / "tts").exists()
    assert (target / "characters/Sakura/character.json").is_file()
    TimelineStore(target / "data/chat_history/timeline.sqlite3").assert_activated()
    assert (target / "data/memory/mem0_history.db").is_file()


@pytest.mark.skipif(__import__("platform").system() != "Windows", reason="v1 supports Windows imports")
@pytest.mark.parametrize(
    ("failed_domain", "import_id"),
    [
        ("characters", "test-character-cleanup-failure"),
        ("tts", "test-tts-cleanup-failure"),
    ],
)
def test_optional_domain_cleanup_failure_aborts_before_commit_and_preserves_target_trees(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_domain: str,
    import_id: str,
) -> None:
    source = _legacy_fixture(tmp_path)
    target = tmp_path / "target"
    (target / "characters/Existing").mkdir(parents=True)
    (target / "characters/Existing/complete.bin").write_bytes(b"complete character")
    (target / "tts").mkdir()
    (target / "tts/existing-model.bin").write_bytes(b"complete tts")
    characters_before = _tree_state(target / "characters")
    tts_before = _tree_state(target / "tts")

    if failed_domain == "characters":
        (source / "characters/Sakura/character.json").write_text(
            "not json", encoding="utf-8"
        )
    else:
        def fail_tts_after_partial_copy(
            _source: Path,
            payload: Path,
            *_args: object,
            **_kwargs: object,
        ) -> tuple[int, int]:
            staged_tts = payload / "tts"
            staged_tts.mkdir(parents=True)
            (staged_tts / "partial.bin").write_bytes(b"partial")
            raise LegacyImportError("LEGACY_COPY_FAILED", "staging")

        monkeypatch.setattr(legacy_importer, "_copy_tts", fail_tts_after_partial_copy)

    staged_domain = (
        target / f".legacy-import-staging-{import_id}" / "payload" / failed_domain
    )
    real_rmtree = legacy_importer.shutil.rmtree
    cleanup_was_blocked = False

    def leave_optional_domain_once(path: object, *args: object, **kwargs: object) -> None:
        nonlocal cleanup_was_blocked
        if Path(path) == staged_domain and not cleanup_was_blocked:  # type: ignore[arg-type]
            cleanup_was_blocked = True
            return
        real_rmtree(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(legacy_importer.shutil, "rmtree", leave_optional_domain_once)

    with pytest.raises(LegacyImportError) as raised:
        run_legacy_import(source, target, import_id=import_id, finalize=True)

    assert raised.value.code == "LEGACY_OPTIONAL_DOMAIN_CLEANUP_FAILED"
    assert cleanup_was_blocked
    assert _tree_state(target / "characters") == characters_before
    assert _tree_state(target / "tts") == tts_before
    assert not (target / f"data/legacy-imports/{import_id}/report.json").exists()
    assert not list(target.glob(".legacy-import-journal-*"))


@pytest.mark.skipif(__import__("platform").system() != "Windows", reason="v1 supports Windows imports")
def test_optional_domain_does_not_swallow_user_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _legacy_fixture(tmp_path)
    target = tmp_path / "target"
    target.mkdir()

    def cancel_tts(*_args: object, **_kwargs: object) -> tuple[int, int]:
        raise LegacyImportError("LEGACY_IMPORT_CANCELLED", "staging")

    monkeypatch.setattr(legacy_importer, "_copy_tts", cancel_tts)
    with pytest.raises(LegacyImportError, match="LEGACY_IMPORT_CANCELLED"):
        run_legacy_import(
            source,
            target,
            import_id="test-optional-cancel",
            finalize=True,
        )

    assert list(target.iterdir()) == []




@pytest.mark.skipif(__import__("platform").system() != "Windows", reason="v1 supports Windows imports")
def test_import_rejects_invalid_target_timeline_and_keeps_existing_files(tmp_path: Path) -> None:
    source = _legacy_fixture(tmp_path)
    target = tmp_path / "target"
    (target / "data/chat_history").mkdir(parents=True)
    (target / "data/chat_history/timeline.sqlite3").write_bytes(b"stale timeline")
    (target / "characters/Existing").mkdir(parents=True)
    (target / "characters/Existing/marker.txt").write_text("old", encoding="utf-8")
    (target / "tts").mkdir()
    (target / "tts/old-resource.bin").write_bytes(b"old")
    unrelated = target / "plugins/user/custom/plugin.py"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("keep me", encoding="utf-8")

    inspection = inspect_legacy_installation(source, target)
    assert not inspection.compatible
    assert inspection.blockers == (
        {"code": "LEGACY_DATA_TARGET_TIMELINE_INVALID", "stage": "inspect"},
    )
    with pytest.raises(LegacyImportError, match="LEGACY_DATA_TARGET_TIMELINE_INVALID"):
        run_legacy_import(
            source,
            target,
            import_id="test-overwrite-existing",
            inspection=inspection,
            finalize=True,
        )

    assert (target / "data/chat_history/timeline.sqlite3").read_bytes() == b"stale timeline"
    assert (target / "characters/Existing/marker.txt").read_text(encoding="utf-8") == "old"
    assert (target / "tts/old-resource.bin").read_bytes() == b"old"
    assert unrelated.read_text(encoding="utf-8") == "keep me"














@pytest.mark.skipif(__import__("platform").system() != "Windows", reason="Windows retry")
def test_journal_replace_retries_brief_windows_file_locks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = tmp_path / ".legacy-import-journal-retry.json"
    real_replace = legacy_transaction.os.replace
    attempts = 0

    def briefly_locked(source: Path, destination: Path) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError(5, "brief scanner lock", str(destination))
        real_replace(source, destination)

    monkeypatch.setattr(legacy_transaction.os, "replace", briefly_locked)
    monkeypatch.setattr(legacy_transaction.time, "sleep", lambda _delay: None)

    legacy_transaction._write_journal(journal, {"state": "committing"})

    assert attempts == 3
    assert json.loads(journal.read_text(encoding="utf-8")) == {"state": "committing"}








@pytest.mark.parametrize("cut", ["after_backup", "after_install"])
def test_hard_exit_at_each_atomic_tree_rename_is_fully_recovered(
    tmp_path: Path,
    cut: str,
) -> None:
    tree_name = "data/chat_history"
    tree_id = tree_name.replace("/", "-").replace("_", "-")
    import_id = f"hard-exit-{tree_id}-{cut.replace('_', '-')}"
    target = tmp_path / "target"
    payload = target / f".legacy-import-staging-{import_id}" / "payload"
    destination = target / tree_name
    staged_tree = payload / tree_name
    destination.mkdir(parents=True)
    staged_tree.mkdir(parents=True)
    (destination / "original.bin").write_bytes(b"original")
    (staged_tree / "imported.bin").write_bytes(b"imported")
    script = r"""
import os
import sys
from pathlib import Path
import app.legacy_import.transaction as transaction

target = Path(sys.argv[1])
payload = Path(sys.argv[2])
import_id = sys.argv[3]
tree_name = sys.argv[4]
cut = sys.argv[5]
destination = target / tree_name
backup = target / f".legacy-import-backup-{import_id}" / tree_name
staged = payload / tree_name
real_replace = transaction.os.replace

def replace_then_exit(source, target_path):
    source_path = Path(source)
    destination_path = Path(target_path)
    real_replace(source, target_path)
    if cut == "after_backup" and source_path == destination and destination_path == backup:
        os._exit(91)
    if cut == "after_install" and source_path == staged and destination_path == destination:
        os._exit(92)

transaction.os.replace = replace_then_exit
transaction.commit_payload(target, import_id, payload)
"""

    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            script,
            str(target),
            str(payload),
            import_id,
            tree_name,
            cut,
        ],
        cwd=Path(__file__).resolve().parents[2],
        check=False,
        capture_output=True,
        timeout=20,
    )

    assert completed.returncode == (91 if cut == "after_backup" else 92)
    assert recover_pending_commits(target) == [import_id]
    assert (destination / "original.bin").read_bytes() == b"original"
    assert not (destination / "imported.bin").exists()
    assert not list(target.glob(".legacy-import-*"))


def test_interrupted_finalize_is_resumed_without_rolling_back_valid_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target"
    payload = target / ".legacy-import-staging-test-finalize-resume" / "payload"
    (target / "config").mkdir(parents=True)
    (payload / "config").mkdir(parents=True)
    (target / "config/ui.json").write_text("old", encoding="utf-8")
    (payload / "config/ui.json").write_text("new", encoding="utf-8")
    pending = commit_payload(target, "test-finalize-resume", payload)
    original_rmtree = legacy_transaction.shutil.rmtree
    monkeypatch.setattr(legacy_transaction.shutil, "rmtree", lambda *_args, **_kwargs: None)

    finalize_commit(pending)

    assert (target / "config/ui.json").read_text(encoding="utf-8") == "new"
    journal = json.loads(pending.journal_path.read_text(encoding="utf-8"))
    assert journal["state"] == "finalizing"
    monkeypatch.setattr(legacy_transaction.shutil, "rmtree", original_rmtree)
    assert recover_pending_commits(target) == ["test-finalize-resume"]
    assert (target / "config/ui.json").read_text(encoding="utf-8") == "new"
    assert not list(target.glob(".legacy-import-*"))




def test_history_quarantines_unknown_role_at_exact_source_line(tmp_path: Path) -> None:
    source = tmp_path / "legacy"
    history = source / "data" / "chat_history"
    history.mkdir(parents=True)
    (history / "orphan.jsonl").write_text(
        "\n"
        + json.dumps(
            {
                "created_at": "2026-01-01T00:00:00+08:00",
                "role": "tool",
                "content": "private content",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    staged = tmp_path / "staged"
    stats = import_history(source, staged, character_ids=())
    assert stats.errors_quarantined == 1
    assert TimelineStore(staged / "data/chat_history/timeline.sqlite3").read_all("orphan") == []
    quarantine = staged / "data/legacy-imports/history-import/quarantine/history-records.jsonl"
    record = json.loads(quarantine.read_text(encoding="utf-8"))
    assert record["code"] == "LEGACY_HISTORY_ROLE_UNSUPPORTED"
    assert record["relativePath"] == "data/chat_history/orphan.jsonl"
    assert record["line"] == 2
    assert "private content" not in quarantine.read_text(encoding="utf-8")


def test_large_history_streams_binary_lines_with_stable_chunks_and_raw_quarantine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "legacy"
    history_root = source / "data/chat_history"
    history_root.mkdir(parents=True)
    history = history_root / "Sakura.jsonl"
    records = [
        {
            "created_at": "2026-01-01T00:00:00+08:00",
            "role": "user",
            "content": "hello",
        },
        *[
            {
                "created_at": f"2026-01-01T00:{index // 60:02d}:{index % 60:02d}+08:00",
                "role": "assistant",
                "content": f"segment-{index:03d}",
            }
            for index in range(1, MAX_SEGMENTS + 2)
        ],
    ]
    invalid_raw = b"not-json-\xff\n"
    history.write_bytes(
        b"".join(
            (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
            for record in records
        )
        + invalid_raw
    )
    real_read_bytes = Path.read_bytes

    def reject_whole_history_read(path: Path) -> bytes:
        if path == history:
            raise AssertionError("history JSONL must be streamed")
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", reject_whole_history_read)
    first = tmp_path / "first"
    second = tmp_path / "second"

    first_stats = import_history(source, first, character_ids=("Sakura",))
    second_stats = import_history(source, second, character_ids=("Sakura",))

    first_entries = TimelineStore(first / "data/chat_history/timeline.sqlite3").read_all(
        "Sakura"
    )
    second_entries = TimelineStore(second / "data/chat_history/timeline.sqlite3").read_all(
        "Sakura"
    )
    assistant_entries = [
        entry for entry in first_entries if entry.kind == TimelineKind.ASSISTANT
    ]
    assert [len(entry.payload["segments"]) for entry in assistant_entries] == [
        MAX_SEGMENTS,
        1,
    ]
    assert [
        segment["text"]
        for entry in assistant_entries
        for segment in entry.payload["segments"]
    ] == [f"segment-{index:03d}" for index in range(1, MAX_SEGMENTS + 2)]
    assert [entry.entry_id for entry in first_entries] == [
        entry.entry_id for entry in second_entries
    ]
    assert first_stats == second_stats
    assert first_stats.errors_quarantined == 1
    quarantine = (
        first
        / "data/legacy-imports/history-import/quarantine/history-records.jsonl"
    )
    issue = json.loads(quarantine.read_text(encoding="utf-8"))
    assert base64.b64decode(issue["rawBase64"]) == invalid_raw


def test_mcp_migration_drops_deprecated_confirmation_fields_recursively(
    tmp_path: Path,
) -> None:
    source = tmp_path / "legacy"
    config = source / "data/config/mcp.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        """enabled: true
servers:
  web:
    transport: stdio
    command: python
    args: []
    risk: low
    requires_confirmation: false
    tool_policies:
      Snapshot:
        risk: medium
        requires_confirmation: true
""",
        encoding="utf-8",
    )

    migrated, dropped = _migrate_mcp(source, config)

    assert dropped == 0
    assert migrated["servers"]["web"]["risk"] == "low"
    assert "requires_confirmation" not in json.dumps(migrated)


def test_mcp_migration_quarantines_source_paths_in_executable_fields(
    tmp_path: Path,
) -> None:
    source = Path(r"C:\foo")
    config = tmp_path / "mcp.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "enabled": True,
                "servers": {
                    "command-source": {
                        "transport": "stdio",
                        "command": r"C:\foo\tools\server.exe",
                    },
                    "args-source": {
                        "transport": "stdio",
                        "command": "runner",
                        "args": ["--directory", "c:/FOO/tools/server"],
                    },
                    "env-source": {
                        "transport": "stdio",
                        "command": "runner",
                        "env": {"PYTHONPATH": r"C:\foo\packages"},
                    },
                    "source-sibling": {
                        "transport": "stdio",
                        "command": r"C:\foobar\tools\server.exe",
                    },
                    "source-space-sibling": {
                        "transport": "stdio",
                        "command": r"C:\foo archive\tools\server.exe",
                    },
                    "unrelated": {
                        "transport": "stdio",
                        "command": "runner",
                        "args": ["https://example.test/C:/foo/docs", "foo is a label"],
                        "env": {"DOCS_URL": "https://example.test/legacy-root"},
                    },
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    migrated, dropped = _migrate_mcp(source, config)

    assert dropped == 3
    assert set(migrated["servers"]) == {
        "source-sibling",
        "source-space-sibling",
        "unrelated",
    }




def test_mcp_migration_quarantines_source_path_in_shell_command(
    tmp_path: Path,
) -> None:
    source = Path(r"C:\legacy-root")
    config = tmp_path / "mcp.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "servers": {
                    "legacy": {
                        "transport": "stdio",
                        "command": "powershell",
                        "args": ["-Command", r"& C:\legacy-root\server.ps1"],
                    },
                    "sibling": {
                        "transport": "stdio",
                        "command": "powershell",
                        "args": ["-Command", r"& C:\legacy-root archive\server.ps1"],
                    },
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    migrated, dropped = _migrate_mcp(source, config)

    assert dropped == 1
    assert set(migrated["servers"]) == {"sibling"}


def test_mcp_migration_quarantines_drive_file_uri_but_not_http_or_sibling(
    tmp_path: Path,
) -> None:
    source = Path(r"C:\foo")
    config = tmp_path / "mcp.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "servers": {
                    "legacy": {
                        "transport": "stdio",
                        "command": "runner",
                        "args": ["--script=file:///C:/foo/server.py"],
                    },
                    "http": {
                        "transport": "stdio",
                        "command": "runner",
                        "args": ["https://example.test/C:/foo/docs"],
                    },
                    "sibling": {
                        "transport": "stdio",
                        "command": "runner",
                        "args": ["file:///C:/foo%20archive/server.py"],
                    },
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    migrated, dropped = _migrate_mcp(source, config)

    assert dropped == 1
    assert set(migrated["servers"]) == {"http", "sibling"}






def test_mcp_migration_quarantines_posix_colon_path_list_entries(
    tmp_path: Path,
) -> None:
    source = Path("/legacy-root")
    config = tmp_path / "mcp.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "servers": {
                    "source-last": {
                        "transport": "stdio",
                        "command": "runner",
                        "env": {"PATH": "/other:/legacy-root/bin"},
                    },
                    "source-first": {
                        "transport": "stdio",
                        "command": "runner",
                        "env": {"PATH": "/legacy-root:/other"},
                    },
                    "http": {
                        "transport": "stdio",
                        "command": "runner",
                        "args": ["https://example.test/legacy-root/docs"],
                    },
                    "sibling": {
                        "transport": "stdio",
                        "command": "runner",
                        "env": {"PATH": "/legacy-root-backup:/other"},
                    },
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    migrated, dropped = _migrate_mcp(source, config)

    assert dropped == 2
    assert set(migrated["servers"]) == {"http", "sibling"}




def test_mcp_migration_rebinds_legacy_web_server_tokens(
    tmp_path: Path,
) -> None:
    separator = "\\"
    source = Path(r"C:\legacy-root")
    config = tmp_path / "mcp.yaml"
    web_path = separator.join(
        ["{base_dir}", "app", "agent", "mcp", "web_search_server.py"]
    )
    python_path = separator.join(["{base_dir}", "runtime", "python.exe"])
    config.write_text(
        yaml.safe_dump(
            {
                "servers": {
                    "web": {
                        "transport": "stdio",
                        "command": python_path,
                        "args": [web_path],
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    migrated, dropped = _migrate_mcp(source, config)

    assert dropped == 0
    assert migrated["servers"]["web"]["command"] == "{python}"
    assert migrated["servers"]["web"]["args"] == [
        "{core_root}/app/agent/mcp/web_search_server.py"
    ]


@pytest.mark.skipif(__import__("platform").system() != "Windows", reason="v1 supports Windows imports")
@pytest.mark.parametrize(
    ("relative", "content", "_legacy_code"),
    [
        ("data/reminders.json", "[]", "LEGACY_REMINDERS_VALIDATION_FAILED"),
        (
            "data/config/mcp.yaml",
            "enabled: true\ndefault_call_timeout: invalid\nservers: {}\n",
            "LEGACY_MCP_VALIDATION_FAILED",
        ),
    ],
)
def test_current_validators_quarantine_invalid_auxiliary_or_configuration_data(
    tmp_path: Path, relative: str, content: str, _legacy_code: str
) -> None:
    source = _legacy_fixture(tmp_path)
    path = source / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    target = tmp_path / "target"
    target.mkdir()

    report, pending = run_legacy_import(
        source,
        target,
        import_id="test-import-validator",
        finalize=True,
    )
    assert pending is None
    TimelineStore(target / "data/chat_history/timeline.sqlite3").assert_activated()
    quarantine = target / "data/legacy-imports/test-import-validator/quarantine"
    if relative == "data/config/mcp.yaml":
        assert not any(
            warning["code"] == "LEGACY_CONFIGURATION_IMPORT_SKIPPED"
            for warning in report.warnings
        )
        migrated = yaml.safe_load(
            (target / "config/mcp.yaml").read_text(encoding="utf-8")
        )
        assert migrated["default_call_timeout"] == 20
    else:
        assert any(
            warning["code"] == "LEGACY_AUXILIARY_DATA_QUARANTINED"
            for warning in report.warnings
        )
        assert (quarantine / "invalid-data" / Path(relative).name).is_file()


@pytest.mark.skipif(__import__("platform").system() != "Windows", reason="v1 supports Windows imports")
def test_identical_tts_duplicates_are_deduplicated_and_conflicts_skip_tts(tmp_path: Path) -> None:
    source = _legacy_fixture(tmp_path)
    top = source / "tts" / "onnx" / "model.bin"
    bundled = source / "data" / "tts_bundles" / "onnx" / "model.bin"
    top.parent.mkdir(parents=True)
    bundled.parent.mkdir(parents=True)
    top.write_bytes(b"same model")
    bundled.write_bytes(b"same model")
    target = tmp_path / "target"
    target.mkdir()

    run_legacy_import(source, target, import_id="test-import-tts-same", finalize=True)
    assert (target / "tts/onnx/model.bin").read_bytes() == b"same model"

    conflict_source = _legacy_fixture(tmp_path / "conflict")
    conflict_top = conflict_source / "tts" / "onnx" / "model.bin"
    conflict_bundled = conflict_source / "data" / "tts_bundles" / "onnx" / "model.bin"
    conflict_top.parent.mkdir(parents=True)
    conflict_bundled.parent.mkdir(parents=True)
    conflict_top.write_bytes(b"first")
    conflict_bundled.write_bytes(b"second")
    conflict_target = tmp_path / "conflict-target"
    conflict_target.mkdir()
    report, pending = run_legacy_import(
        conflict_source,
        conflict_target,
        import_id="test-import-tts-conflict",
        finalize=True,
    )
    assert pending is None
    assert report.counts["ttsSkipped"] == 1
    assert not (conflict_target / "tts").exists()
    TimelineStore(conflict_target / "data/chat_history/timeline.sqlite3").assert_activated()


@pytest.mark.skipif(__import__("platform").system() != "Windows", reason="v1 supports Windows imports")
def test_genie_configuration_and_onnx_models_map_to_current_character_schema(
    tmp_path: Path,
) -> None:
    source = _legacy_fixture(tmp_path, "0.9.8")
    api_path = source / "data/config/api.yaml"
    api = yaml.safe_load(api_path.read_text(encoding="utf-8"))
    api["tts"] = {
        "provider": "genie_tts",
        "enabled": True,
        "genie_tts": {
            "api_url": "http://127.0.0.1:9881/",
            "work_dir": str(source / "tts/cpu"),
            "onnx_model_dir": str(source / "data/tts_bundles/onnx/Sakura"),
            "timeout_seconds": 9,
        },
    }
    api_path.write_text(yaml.safe_dump(api, sort_keys=False), encoding="utf-8")
    onnx = source / "data/tts_bundles/onnx/Sakura/model.onnx"
    onnx.parent.mkdir(parents=True)
    onnx.write_bytes(b"onnx")
    target = tmp_path / "target"
    target.mkdir()

    run_legacy_import(source, target, import_id="test-import-genie", finalize=True)

    plugins = yaml.safe_load((target / "config/plugins.yaml").read_text(encoding="utf-8"))
    enabled = {item["id"] for item in plugins if item["enabled"]}
    assert "sakura.tts.genie" in enabled
    assert "sakura.tts.gpt-sovits" not in enabled
    genie = json.loads(
        (target / "data/plugins/sakura.tts.genie/config.json").read_text(encoding="utf-8")
    )
    assert genie == {
        "endpointMode": "managed",
        "apiUrl": "http://127.0.0.1:9881/",
        "timeoutSeconds": 9,
        "workDir": str(target / "tts/cpu"),
    }
    manifest = json.loads((target / "characters/Sakura/character.json").read_text(encoding="utf-8"))
    assert manifest["extensions"]["sakura.tts"] == {
        "enabled": True,
        "provider": "sakura.tts.genie",
    }
    assert manifest["extensions"]["sakura.tts.genie"]["onnxModelDir"] == "voice/onnx"
    assert manifest["extensions"]["sakura.tts.gpt-sovits"]["toneRefs"] == "voice/refs/ref.txt"
    assert (target / "characters/Sakura/voice/onnx/model.onnx").read_bytes() == b"onnx"


@pytest.mark.skipif(__import__("os").name != "nt", reason="verbatim paths are Windows-only")
def test_migrated_tts_plugin_configs_do_not_persist_verbatim_paths(tmp_path: Path) -> None:
    staged = tmp_path / "staged"
    ordinary_root = (tmp_path / "target" / "tts").resolve()
    verbatim_root = Path("\\\\?\\" + str(ordinary_root))

    _write_tts_plugin_config(
        staged,
        {
            "gpt_sovits": {
                "api_url": "http://127.0.0.1:9880/tts",
                "work_dir": r"C:\\Old Sakura\\tts\\g50",
            }
        },
        verbatim_root,
        tts_provider="sakura.tts.gpt-sovits",
    )
    _write_tts_plugin_config(
        staged,
        {
            "genie_tts": {
                "api_url": "http://127.0.0.1:9881/",
                "work_dir": r"C:\\Old Sakura\\tts\\cpu",
            }
        },
        verbatim_root,
        tts_provider="sakura.tts.genie",
    )

    gpt = json.loads(
        (staged / "data/plugins/sakura.tts.gpt-sovits/config.json").read_text(
            encoding="utf-8"
        )
    )
    genie = json.loads(
        (staged / "data/plugins/sakura.tts.genie/config.json").read_text(encoding="utf-8")
    )
    assert gpt["workDir"] == str(ordinary_root / "g50")
    assert genie["workDir"] == str(ordinary_root / "cpu")
    assert "\\\\?\\" not in gpt["workDir"]
    assert "\\\\?\\" not in genie["workDir"]


@pytest.mark.skipif(__import__("platform").system() != "Windows", reason="v1 supports Windows imports")
def test_first_import_clears_curation_state_for_core_rebuild(tmp_path: Path) -> None:
    source = _legacy_fixture(tmp_path)
    (source / "data/chat_history/Orphan.jsonl").write_text(
        json.dumps(
            {
                "created_at": "2026-01-03T00:00:00+08:00",
                "role": "user",
                "content": "orphan history",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (source / "data/memory_curation_state.Orphan.json").write_text(
        json.dumps({"processed_history_count": 1, "pending_turns": 0}), encoding="utf-8"
    )
    target = tmp_path / "target"
    target.mkdir()

    run_legacy_import(source, target, import_id="test-import-orphan", finalize=True)

    assert not (target / "data/memory/curation_state").exists()
    entries = TimelineStore(target / "data/chat_history/timeline.sqlite3").read_all("Orphan")
    assert entries[0].payload == {"text": "orphan history"}


def test_chunked_copy_cancellation_removes_partial_file(tmp_path: Path) -> None:
    source = tmp_path / "large.bin"
    source.write_bytes(b"x" * (3 * 1024 * 1024))
    target = tmp_path / "copied.bin"
    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 3

    with pytest.raises(LegacyImportError, match="LEGACY_IMPORT_CANCELLED"):
        copy_file_checked(source, target, cancelled=cancelled)
    assert not target.exists()








@pytest.mark.skipif(__import__("os").name != "nt", reason="robocopy is Windows-only")
def test_windows_fast_copy_normalizes_extended_paths_and_reports_robocopy_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "model.bin").write_bytes(b"model")
    extended_source = Path("\\\\?\\" + str(source))
    target = tmp_path / "target"
    diagnostics: list[tuple[str, dict[str, object]]] = []
    captured_command: list[str] = []

    class FailedProcess:
        returncode = 16

        def poll(self) -> int:
            return self.returncode

    def popen(command: list[str], **kwargs: object) -> FailedProcess:
        captured_command.extend(command)
        output = kwargs["stdout"]
        output.write(f"ERROR 5 Accessing Source Directory {command[1]}".encode())
        return FailedProcess()

    monkeypatch.setattr(legacy_files.shutil, "which", lambda _name: "robocopy.exe")
    monkeypatch.setattr(legacy_files.subprocess, "Popen", popen)

    with pytest.raises(LegacyImportError, match="LEGACY_COPY_FAILED"):
        copy_tree_fast_checked(
            extended_source,
            target,
            cancelled=lambda: False,
            diagnostic=lambda event, attributes: diagnostics.append((event, dict(attributes))),
        )

    assert captured_command[1] == str(source)
    completed = next(attributes for event, attributes in diagnostics if event == "robocopy_completed")
    assert completed["return_code"] == 16
    assert str(source).casefold() not in str(completed["output_tail"]).casefold()
    assert "ERROR 5" in str(completed["output_tail"])


@pytest.mark.skipif(__import__("os").name != "nt", reason="robocopy is Windows-only")
def test_windows_fast_copy_reports_post_scan_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "model.bin").write_bytes(b"expected")
    target = tmp_path / "target"
    diagnostics: list[tuple[str, dict[str, object]]] = []

    class IncompleteProcess:
        returncode = 1

        def poll(self) -> int:
            return self.returncode

    def popen(_command: list[str], **_kwargs: object) -> IncompleteProcess:
        target.mkdir()
        (target / "model.bin").write_bytes(b"x")
        return IncompleteProcess()

    monkeypatch.setattr(legacy_files.shutil, "which", lambda _name: "robocopy.exe")
    monkeypatch.setattr(legacy_files.subprocess, "Popen", popen)

    with pytest.raises(LegacyImportError, match="LEGACY_COPY_FAILED"):
        copy_tree_fast_checked(
            source,
            target,
            cancelled=lambda: False,
            diagnostic=lambda event, attributes: diagnostics.append((event, dict(attributes))),
        )

    failure = next(
        attributes
        for event, attributes in diagnostics
        if event == "failed" and attributes.get("detail_stage") == "post_scan"
    )
    assert failure["expected_files"] == 1
    assert failure["expected_bytes"] == len(b"expected")
    assert failure["actual_files"] == 1
    assert failure["actual_bytes"] == 1




def test_tts_profile_adaptation_removes_old_install_paths(tmp_path: Path) -> None:
    tts_root = tmp_path / "payload" / "tts"
    config_root = tts_root / "g50" / "GPT_SoVITS" / "configs"
    config_root.mkdir(parents=True)
    profile = {
        "custom": {
            "device": "cuda:0",
            "is_half": True,
            "version": "v2ProPlus",
            "t2s_weights_path": r"\\?\C:\Old Sakura\characters\A.ckpt",
            "vits_weights_path": r"C:\Old Sakura\characters\A.pth",
        },
        "v2ProPlus": {
            "device": "cuda:0",
            "is_half": True,
            "version": "v2ProPlus",
            "t2s_weights_path": "GPT_SoVITS/pretrained_models/s1v3.ckpt",
            "vits_weights_path": "GPT_SoVITS/pretrained_models/v2Pro/s2Gv2ProPlus.pth",
        },
    }
    for name in ("tts_infer.yaml", "tts_infer_sakura_managed.yaml"):
        (config_root / name).write_text(
            yaml.safe_dump(profile, sort_keys=False),
            encoding="utf-8",
        )

    changed, _byte_delta = _sanitize_tts_runtime_profiles(tts_root)

    assert changed == 2
    for name in ("tts_infer.yaml", "tts_infer_sakura_managed.yaml"):
        migrated = yaml.safe_load((config_root / name).read_text(encoding="utf-8"))
        assert migrated["custom"]["t2s_weights_path"] == migrated["v2ProPlus"]["t2s_weights_path"]
        assert migrated["custom"]["vits_weights_path"] == migrated["v2ProPlus"]["vits_weights_path"]




@pytest.mark.skipif(__import__("os").name != "nt", reason="robocopy is Windows-only")
def test_windows_fast_copy_cancellation_terminates_process_and_cleans_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "model.bin").write_bytes(b"model")
    target = tmp_path / "target"

    class PendingProcess:
        returncode: int | None = None
        terminated = False

        def poll(self) -> int | None:
            return self.returncode

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = 1

        def wait(self, timeout: int) -> int:
            del timeout
            assert self.returncode is not None
            return self.returncode

        def kill(self) -> None:
            self.returncode = 1

    process = PendingProcess()
    monkeypatch.setattr(legacy_files.shutil, "which", lambda _name: "robocopy.exe")
    monkeypatch.setattr(legacy_files.subprocess, "Popen", lambda *_args, **_kwargs: process)
    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 2

    with pytest.raises(LegacyImportError, match="LEGACY_IMPORT_CANCELLED"):
        copy_tree_fast_checked(source, target, cancelled=cancelled)
    assert process.terminated
    assert not target.exists()
