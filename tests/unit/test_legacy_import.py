from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
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
from app.storage.timeline import TimelineKind, TimelineStore


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
    legacy_cli._emit({"type": "progress", "message": "正在迁移长期记忆"})

    encoded = capsys.readouterr().out.strip()
    assert encoded.isascii()
    assert json.loads(encoded) == {"type": "progress", "message": "正在迁移长期记忆"}


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


def test_configuration_import_fills_blank_llm_from_canonical_legacy_env(
    tmp_path: Path,
) -> None:
    migrated, service = _migrate_api_document(
        tmp_path,
        {"llm": {"base_url": "  ", "api_key": None}},
        env_text=(
            'export BASE_URL="https://canonical.example/v1"\n'
            "API_KEY='fixture-canonical-credential'\n"
            "MODEL=canonical-model\n"
            "UNRELATED_SETTING=must-not-migrate\n"
        ),
    )

    assert migrated["llm"] == {
        "base_url": "https://canonical.example/v1",
        "api_key": "fixture-canonical-credential",
        "model": "canonical-model",
    }
    assert migrated["api_profiles"] == [
        {
            "id": "legacy",
            "alias": "旧版本配置",
            "base_url": "https://canonical.example/v1",
            "api_key": "fixture-canonical-credential",
            "models": [{"name": "canonical-model"}],
        }
    ]
    assert migrated["model_slots"] == {
        "chat": {"profile_id": "legacy", "model": "canonical-model"}
    }
    assert "UNRELATED_SETTING" not in yaml.safe_dump(migrated)
    assert "must-not-migrate" not in yaml.safe_dump(migrated)

    providers = service.load_api_profiles()
    selection = service.load_model_selection()
    assert len(providers) == 1
    assert hashlib.sha256(providers[0].api_key.encode()).digest() == hashlib.sha256(
        b"fixture-canonical-credential"
    ).digest()
    assert selection.chat.profile_id == "legacy"
    assert selection.chat.model == "canonical-model"


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


def test_configuration_import_prefers_existing_model_slots_and_removes_retired_fields(
    tmp_path: Path,
) -> None:
    slots = {
        "chat": {
            "profile_id": "provider-b",
            "model": "current-chat",
            "context_window_tokens": 65_536,
            "slot_extension": "kept",
        },
        "vision_chat": {"profile_id": "provider-a", "model": "current-vision"},
        "memory_curation": {"profile_id": "provider-b", "model": "memory-model"},
    }
    migrated, service = _migrate_api_document(
        tmp_path,
        {
            "api_profiles": [
                {
                    "id": "provider-b",
                    "alias": "第二个 Provider",
                    "base_url": "https://b.example/v1",
                    "api_key": "provider-b-secret",
                    "models": [
                        {"name": "current-chat", "model_extension": "kept"},
                        {"name": "memory-model"},
                    ],
                    "provider_extension": {"kept": True},
                },
                {
                    "id": "provider-a",
                    "alias": "第一个 Provider",
                    "base_url": "https://a.example/v1",
                    "api_key": "provider-a-secret",
                    "models": ["current-vision"],
                },
            ],
            "model_slots": slots,
            "model_names": ["retired-text", "retired-vision"],
            "text_enabled": False,
            "text_profile_id": "retired-provider",
            "text_model": "retired-text",
            "vision_profile_id": "retired-provider",
            "vision_model": "retired-vision",
            "root_extension": {"kept": True},
        },
    )

    assert not (_RETIRED_MODEL_SELECTION_FIELDS & migrated.keys())
    assert migrated["model_slots"] == slots
    providers = migrated["api_profiles"]
    assert isinstance(providers, list)
    assert [provider["id"] for provider in providers] == ["provider-b", "provider-a"]
    assert providers[0]["provider_extension"] == {"kept": True}
    assert providers[0]["models"][0]["model_extension"] == "kept"
    assert providers[1]["models"] == [{"name": "current-vision"}]
    assert migrated["root_extension"] == {"kept": True}

    loaded_providers = service.load_api_profiles()
    selection = service.load_model_selection()
    assert [provider.id for provider in loaded_providers] == ["provider-b", "provider-a"]
    assert hashlib.sha256(loaded_providers[0].api_key.encode()).digest() == hashlib.sha256(
        b"provider-b-secret"
    ).digest()
    assert hashlib.sha256(loaded_providers[1].api_key.encode()).digest() == hashlib.sha256(
        b"provider-a-secret"
    ).digest()
    assert selection.chat.profile_id == "provider-b"
    assert selection.chat.model == "current-chat"
    assert selection.chat.context_window_tokens == 65_536
    assert selection.vision_chat is not None
    assert selection.vision_chat.profile_id == "provider-a"
    assert selection.vision_chat.model == "current-vision"


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


def test_configuration_import_uses_vision_selection_when_pr110_text_is_disabled(
    tmp_path: Path,
) -> None:
    migrated, service = _migrate_api_document(
        tmp_path,
        {
            "api_profiles": [
                {
                    "id": "provider",
                    "alias": "Provider",
                    "base_url": "https://api.example/v1",
                    "api_key": "provider-secret",
                    "models": ["unused-text-model", "vision-model"],
                }
            ],
            "text_enabled": False,
            "text_profile_id": "provider",
            "text_model": "unused-text-model",
            "vision_profile_id": "provider",
            "vision_model": "vision-model",
        },
    )

    assert not (_RETIRED_MODEL_SELECTION_FIELDS & migrated.keys())
    assert migrated["model_slots"] == {
        "chat": {"profile_id": "provider", "model": "vision-model"},
    }
    assert migrated["api_profiles"][0]["models"] == [
        {"name": "unused-text-model"},
        {"name": "vision-model"},
    ]

    loaded_providers = service.load_api_profiles()
    selection = service.load_model_selection()
    assert hashlib.sha256(loaded_providers[0].api_key.encode()).digest() == hashlib.sha256(
        b"provider-secret"
    ).digest()
    assert selection.chat.profile_id == "provider"
    assert selection.chat.model == "vision-model"
    assert selection.vision_chat is None


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


def test_inspection_accepts_same_platform_macos_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _macos_legacy_fixture(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    monkeypatch.setattr(legacy_inspector.platform, "system", lambda: "Darwin")

    inspection = inspect_legacy_installation(source, target)

    assert inspection.compatible
    assert inspection.source_platform == "macos"
    assert inspection.domains["tts"].present
    assert not inspection.domains["ttsBundles"].present


def test_inspection_accepts_same_platform_windows_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _legacy_fixture(tmp_path, source_platform="windows")
    target = tmp_path / "target"
    target.mkdir()
    monkeypatch.setattr(legacy_inspector.platform, "system", lambda: "Windows")

    inspection = inspect_legacy_installation(source, target)

    assert inspection.compatible
    assert inspection.source_platform == "windows"


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


def test_memory_validation_recovers_copied_wal_without_reusing_legacy_shm(tmp_path: Path) -> None:
    source = tmp_path / "source-memory"
    source.mkdir(parents=True)
    database = source / "mem0_history.db"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        "CREATE TABLE history (id TEXT PRIMARY KEY, memory_id TEXT, old_memory TEXT, "
        "new_memory TEXT, event TEXT)"
    )
    connection.execute(
        "CREATE TABLE messages (id TEXT PRIMARY KEY, session_scope TEXT, role TEXT, "
        "content TEXT, created_at DATETIME)"
    )
    connection.execute("INSERT INTO messages VALUES ('one', 'scope', 'human', 'payload', 1)")
    connection.commit()

    staged = tmp_path / "staged-memory"
    staged_database = staged / "mem0_history.db"
    staged_database.parent.mkdir(parents=True)
    for path in source.iterdir():
        (staged_database.parent / path.name).write_bytes(path.read_bytes())
    source_before = _tree_state(tmp_path / "source-memory")

    _validate_memory(staged)

    assert not Path(f"{staged_database}-shm").exists()
    with sqlite3.connect(staged_database) as staged_connection:
        assert staged_connection.execute("SELECT content FROM messages").fetchone() == ("payload",)
    assert _tree_state(tmp_path / "source-memory") == source_before
    connection.close()


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


def test_memory_validation_normalizes_legacy_schema_only_in_staging(tmp_path: Path) -> None:
    source = tmp_path / "source-memory"
    source.mkdir()
    source_database = source / "mem0_history.db"
    with sqlite3.connect(source_database) as connection:
        connection.execute(
            "CREATE TABLE history (id TEXT PRIMARY KEY, memory_id TEXT, old_memory TEXT, "
            "new_memory TEXT, event TEXT, created_at DATETIME, updated_at DATETIME, "
            "is_deleted INTEGER, actor_id TEXT, role TEXT, user_id TEXT)"
        )
        connection.execute(
            "INSERT INTO history (id, memory_id, event, user_id) "
            "VALUES ('history-one', 'memory-one', 'ADD', 'legacy-user')"
        )
    source_before = _tree_state(source)
    staged = tmp_path / "staged-memory"
    copy_tree_checked(source, staged, cancelled=lambda: False)

    _validate_memory(staged)

    with sqlite3.connect(staged / "mem0_history.db") as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        history_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(history)")
        }
        assert {"history", "messages"}.issubset(tables)
        assert "user_id" in history_columns
        assert connection.execute(
            "SELECT memory_id, event FROM history WHERE id = 'history-one'"
        ).fetchone() == ("memory-one", "ADD")
    assert _tree_state(source) == source_before
    with sqlite3.connect(source_database) as connection:
        source_tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        source_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(history)")
        }
    assert source_tables == {"history"}
    assert "user_id" in source_columns


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
def test_import_does_not_silently_skip_unreadable_memory_history(
    tmp_path: Path,
) -> None:
    source = _legacy_fixture(tmp_path)
    source_database = source / "data/memory/mem0_history.db"
    source_database.write_bytes(b"not a sqlite database")
    source_before = _tree_state(source)
    target = tmp_path / "target"
    target.mkdir()

    diagnostics: list[tuple[str, str, dict[str, object], str]] = []
    with pytest.raises(LegacyImportError) as captured:
        run_legacy_import(
            source,
            target,
            import_id="test-memory-required",
            diagnostic=lambda event, message, attributes, severity: diagnostics.append(
                (event, message, dict(attributes), severity)
            ),
            finalize=True,
        )

    assert captured.value.code == "LEGACY_MEMORY_DATABASE_INVALID"
    assert _tree_state(source) == source_before
    assert not (target / "data/memory/mem0_history.db").exists()
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
    messages = [message for _stage, _percent, message in progress_updates]
    assert messages.index("正在转换角色对话历史") < messages.index("正在迁移角色长期记忆")
    assert messages.index("正在迁移角色长期记忆") < messages.index("正在迁移配置")
    assert messages.index("正在迁移其他用户数据") < messages.index("正在校验核心迁移数据")
    assert messages.index("正在校验核心迁移数据") < messages.index("正在尝试迁移角色包")
    assert messages.index("正在尝试迁移角色包") < messages.index("正在尝试迁移 TTS 资源")
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
    state = json.loads((target / "data/memory/curation_state/Sakura.json").read_text(encoding="utf-8"))
    assert state["curation_cursor"]


@pytest.mark.skipif(__import__("platform").system() != "Windows", reason="v1 supports Windows imports")
def test_import_prepares_verified_memory_model_before_tts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _legacy_fixture(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    _runtime, calls = _install_fake_memory_model_runtime(monkeypatch)
    monkeypatch.setattr(
        legacy_importer,
        "_prepare_memory_model",
        _REAL_PREPARE_MEMORY_MODEL,
    )
    original_copy_tts = legacy_importer._copy_tts

    def copy_tts_after_model(*args: object, **kwargs: object) -> tuple[int, int]:
        calls.append("tts-copy")
        return original_copy_tts(*args, **kwargs)

    monkeypatch.setattr(legacy_importer, "_copy_tts", copy_tts_after_model)
    updates: list[tuple[str, int, str]] = []

    report, pending = run_legacy_import(
        source,
        target,
        import_id="test-memory-model-ready",
        finalize=True,
        progress=lambda stage, percent, message: updates.append(
            (stage, percent, message)
        ),
    )

    assert pending is None
    assert calls == ["memory-model-download", "tts-copy"]
    model_root = target / "data/cache/memory"
    assert any(model_root.rglob("model.onnx"))
    assert report.counts["memoryModelFiles"] > 0
    assert report.bytes["memoryModel"] > 0
    messages = [message for _stage, _percent, message in updates]
    assert messages.index("记忆模型已就绪") < messages.index("正在尝试迁移 TTS 资源")
    model_percents = [
        percent
        for _stage, percent, message in updates
        if "记忆模型" in message
    ]
    assert model_percents
    assert min(model_percents) >= 46
    assert max(model_percents) <= 54


@pytest.mark.skipif(__import__("platform").system() != "Windows", reason="v1 supports Windows imports")
def test_memory_model_preparation_failure_is_atomic(
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

    with pytest.raises(LegacyImportError) as raised:
        run_legacy_import(
            source,
            target,
            import_id="test-memory-model-failure",
            finalize=True,
        )

    assert raised.value.code == "LEGACY_MEMORY_MODEL_PREPARATION_FAILED"
    assert existing.read_text(encoding="utf-8") == "keep"
    assert not (target / "data/memory/mem0_history.db").exists()
    assert not list(target.glob(".legacy-import-staging-*"))
    assert not list(target.glob(".legacy-import-journal-*"))


@pytest.mark.skipif(__import__("platform").system() != "Windows", reason="v1 supports Windows imports")
def test_import_reuses_complete_target_memory_model_without_downloading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _legacy_fixture(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    runtime, calls = _install_fake_memory_model_runtime(monkeypatch)
    model_dir = runtime.write_model(target / "data/cache/memory")
    before = _tree_state(model_dir)
    monkeypatch.setattr(
        legacy_importer,
        "_prepare_memory_model",
        _REAL_PREPARE_MEMORY_MODEL,
    )

    report, pending = run_legacy_import(
        source,
        target,
        import_id="test-memory-model-reuse",
        finalize=True,
    )

    assert pending is None
    assert calls == []
    assert _tree_state(model_dir) == before
    assert report.counts["memoryModelFiles"] == len(before)
    assert report.bytes["memoryModel"] == sum(value[0] for value in before.values())


@pytest.mark.skipif(__import__("platform").system() != "Windows", reason="v1 supports Windows imports")
def test_import_copies_compatible_source_memory_model_into_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _legacy_fixture(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    runtime, calls = _install_fake_memory_model_runtime(monkeypatch)
    source_model = runtime.write_model(source / "data/cache/memory")
    source_before = _tree_state(source_model)
    monkeypatch.setattr(
        legacy_importer,
        "_prepare_memory_model",
        _REAL_PREPARE_MEMORY_MODEL,
    )

    report, pending = run_legacy_import(
        source,
        target,
        import_id="test-memory-model-copy",
        finalize=True,
    )

    assert pending is None
    assert calls == []
    target_model = (
        target
        / "data/cache/memory"
        / source_model.name
    )
    assert _tree_state(target_model) == source_before
    assert _tree_state(source_model) == source_before
    assert report.counts["memoryModelFiles"] == len(source_before)


@pytest.mark.skipif(__import__("platform").system() != "Windows", reason="v1 supports Windows imports")
def test_inspection_allows_nonempty_target_and_invalid_history_is_atomic(tmp_path: Path) -> None:
    source = _legacy_fixture(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    (target / "existing.txt").write_text("user", encoding="utf-8")
    assert inspect_legacy_installation(source, target).compatible
    history = source / "data/chat_history/Sakura.jsonl"
    history.write_text(history.read_text(encoding="utf-8") + "not json\n", encoding="utf-8")
    with pytest.raises(LegacyImportError, match="LEGACY_HISTORY_JSON_INVALID"):
        run_legacy_import(source, target, import_id="test-import-0002", finalize=True)
    assert not (target / "data/chat_history/timeline.sqlite3").exists()
    assert (target / "existing.txt").read_text(encoding="utf-8") == "user"


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
def test_installed_distribution_files_do_not_make_a_fresh_target_nonempty(tmp_path: Path) -> None:
    source = _legacy_fixture(tmp_path)
    target = tmp_path / "target"
    packaged_files = {
        "VERSION": "1.0.0\n",
        "runtime-manifest.json": "{}\n",
        "release-inventory.json": "{}\n",
        "sakura.exe": "binary",
        "uninstall.exe": "binary",
        "windows_host_backdrop_gate.exe": "binary",
        "core/app/legacy_import/__main__.py": "",
        "python/python.exe": "binary",
        "plugins/builtin/sakura_mem0/plugin.py": "",
        "plugins/builtin/sakura_mem0/__pycache__/plugin.cpython-312.pyc": "cache",
        "plugins/dependencies/sakura.memory.mem0/qdrant_client/__init__.py": "",
        "data/logs/sakura-runtime.log": "started\n",
    }
    for relative, contents in packaged_files.items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")

    assert inspect_legacy_installation(source, target).compatible

    user_plugin = target / "plugins/user/custom/plugin.py"
    user_plugin.parent.mkdir(parents=True)
    user_plugin.write_text("user data", encoding="utf-8")
    inspection = inspect_legacy_installation(source, target)
    assert inspection.compatible
    assert inspection.blockers == ()


@pytest.mark.skipif(__import__("platform").system() != "Windows", reason="v1 supports Windows imports")
def test_import_overwrites_existing_target_data_and_keeps_unrelated_files(tmp_path: Path) -> None:
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

    report, pending = run_legacy_import(
        source,
        target,
        import_id="test-overwrite-existing",
        finalize=True,
    )

    assert pending is None
    assert report.counts["timelineEntries"] > 0
    TimelineStore(target / "data/chat_history/timeline.sqlite3").assert_activated()
    assert not (target / "characters/Existing").exists()
    assert (target / "characters/Sakura/character.json").is_file()
    assert not (target / "tts/old-resource.bin").exists()
    assert unrelated.read_text(encoding="utf-8") == "keep me"


def test_commit_journal_restores_replaced_defaults(tmp_path: Path) -> None:
    target = tmp_path / "target"
    payload = target / ".legacy-import-staging-test-import-0003" / "payload"
    (target / "config").mkdir(parents=True)
    (payload / "config").mkdir(parents=True)
    (target / "config/ui.json").write_text("old", encoding="utf-8")
    (payload / "config/ui.json").write_text("new", encoding="utf-8")
    pending = commit_payload(target, "test-import-0003", payload)
    assert (target / "config/ui.json").read_text(encoding="utf-8") == "new"
    rollback_commit(pending)
    assert (target / "config/ui.json").read_text(encoding="utf-8") == "old"
    assert not pending.journal_path.exists()


def test_interrupted_rollback_cleanup_resumes_without_deleting_restored_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target"
    payload = target / ".legacy-import-staging-test-file-rollback-resume" / "payload"
    destination = target / "config/api.yaml"
    staged = payload / "config/api.yaml"
    destination.parent.mkdir(parents=True)
    staged.parent.mkdir(parents=True)
    (target / "characters").mkdir()
    (target / "tts").mkdir()
    original = b"original api config\n"
    migrated = b"migrated api config\n"
    destination.write_bytes(original)
    staged.write_bytes(migrated)
    pending = commit_payload(target, "test-file-rollback-resume", payload)
    assert destination.read_bytes() == migrated

    real_unlink = Path.unlink
    interrupted = False

    def interrupt_journal_unlink(path: Path, *args: object, **kwargs: object) -> None:
        nonlocal interrupted
        if path == pending.journal_path and not interrupted:
            interrupted = True
            raise PermissionError(5, "injected journal lock", str(path))
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", interrupt_journal_unlink)
    with pytest.raises(LegacyImportError, match="LEGACY_ROLLBACK_FAILED"):
        rollback_commit(pending)

    assert interrupted
    assert destination.read_bytes() == original
    journal = json.loads(pending.journal_path.read_text(encoding="utf-8"))
    assert journal["state"] == "rolling_back"
    assert journal["installed"] == []
    assert journal["backups"] == []
    assert not pending.backup_path.exists()
    assert not pending.staging_path.exists()

    monkeypatch.setattr(Path, "unlink", real_unlink)
    assert recover_pending_commits(target) == ["test-file-rollback-resume"]
    assert destination.read_bytes() == original
    assert destination.read_bytes() != migrated
    assert (target / "characters").is_dir()
    assert (target / "tts").is_dir()
    assert not list(target.glob(".legacy-import-*"))


def test_interrupted_rollback_cleanup_resumes_without_deleting_restored_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target"
    payload = target / ".legacy-import-staging-test-tree-rollback-resume" / "payload"
    original_tree = {
        "runtime/model.bin": b"original model",
        "config/runtime.yaml": b"original: true\n",
    }
    migrated_tree = {
        "runtime/model.bin": b"migrated model",
        "runtime/new.bin": b"new migration data",
    }
    for relative, content in original_tree.items():
        path = target / "tts" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    for relative, content in migrated_tree.items():
        path = payload / "tts" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (target / "characters").mkdir()
    pending = commit_payload(target, "test-tree-rollback-resume", payload)
    assert (target / "tts/runtime/model.bin").read_bytes() == b"migrated model"

    real_unlink = Path.unlink
    interrupted = False

    def interrupt_journal_unlink(path: Path, *args: object, **kwargs: object) -> None:
        nonlocal interrupted
        if path == pending.journal_path and not interrupted:
            interrupted = True
            raise PermissionError(5, "injected journal lock", str(path))
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", interrupt_journal_unlink)
    with pytest.raises(LegacyImportError, match="LEGACY_ROLLBACK_FAILED"):
        rollback_commit(pending)

    assert interrupted
    assert {
        path.relative_to(target / "tts").as_posix(): path.read_bytes()
        for path in (target / "tts").rglob("*")
        if path.is_file()
    } == original_tree
    journal = json.loads(pending.journal_path.read_text(encoding="utf-8"))
    assert journal["state"] == "rolling_back"
    assert journal["installedTrees"] == []
    assert journal["backupTrees"] == []
    assert not pending.backup_path.exists()
    assert not pending.staging_path.exists()

    monkeypatch.setattr(Path, "unlink", real_unlink)
    assert recover_pending_commits(target) == ["test-tree-rollback-resume"]
    assert {
        path.relative_to(target / "tts").as_posix(): path.read_bytes()
        for path in (target / "tts").rglob("*")
        if path.is_file()
    } == original_tree
    assert not (target / "tts/runtime/new.bin").exists()
    assert (target / "characters").is_dir()
    assert not list(target.glob(".legacy-import-*"))


def test_rollback_replays_restore_when_progress_journal_write_was_interrupted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target"
    payload = target / ".legacy-import-staging-test-restore-checkpoint" / "payload"
    destination = target / "config/api.yaml"
    staged = payload / "config/api.yaml"
    destination.parent.mkdir(parents=True)
    staged.parent.mkdir(parents=True)
    original = b"original before restore checkpoint\n"
    migrated = b"migrated before restore checkpoint\n"
    destination.write_bytes(original)
    staged.write_bytes(migrated)
    pending = commit_payload(target, "test-restore-checkpoint", payload)
    backup = pending.backup_path / "config/api.yaml"
    real_write_journal = legacy_transaction._write_journal
    interrupted = False

    def interrupt_restore_checkpoint(path: Path, value: object) -> None:
        nonlocal interrupted
        if (
            not interrupted
            and isinstance(value, dict)
            and value.get("state") == "rolling_back"
            and value.get("installed") == []
            and value.get("backups") == []
            and destination.is_file()
            and not backup.exists()
        ):
            interrupted = True
            raise LegacyImportError("LEGACY_JOURNAL_WRITE_FAILED", "committing")
        real_write_journal(path, value)

    monkeypatch.setattr(legacy_transaction, "_write_journal", interrupt_restore_checkpoint)
    with pytest.raises(LegacyImportError, match="LEGACY_JOURNAL_WRITE_FAILED"):
        rollback_commit(pending)

    assert interrupted
    assert destination.read_bytes() == original
    journal = json.loads(pending.journal_path.read_text(encoding="utf-8"))
    assert journal["state"] == "rolling_back"
    assert journal["installed"] == []
    assert journal["backups"] == ["config/api.yaml"]
    assert not backup.exists()

    monkeypatch.setattr(legacy_transaction, "_write_journal", real_write_journal)
    assert recover_pending_commits(target) == ["test-restore-checkpoint"]
    assert destination.read_bytes() == original
    assert destination.read_bytes() != migrated
    assert not list(target.glob(".legacy-import-*"))


@pytest.mark.parametrize("atomic_tree", [False, True], ids=["file", "atomic-tree"])
def test_recovery_reconciles_legacy_journal_left_after_restore(
    tmp_path: Path, atomic_tree: bool
) -> None:
    import_id = f"test-legacy-rollback-{'tree' if atomic_tree else 'file'}"
    target = tmp_path / "target"
    payload = target / f".legacy-import-staging-{import_id}" / "payload"
    if atomic_tree:
        destination = target / "tts"
        backup_relative = Path("tts")
        (destination / "old.bin").parent.mkdir(parents=True)
        (destination / "old.bin").write_bytes(b"original tree")
        (payload / "tts/new.bin").parent.mkdir(parents=True)
        (payload / "tts/new.bin").write_bytes(b"migrated tree")
    else:
        destination = target / "config/api.yaml"
        backup_relative = Path("config/api.yaml")
        destination.parent.mkdir(parents=True)
        (payload / backup_relative).parent.mkdir(parents=True)
        destination.write_bytes(b"original file")
        (payload / backup_relative).write_bytes(b"migrated file")

    pending = commit_payload(target, import_id, payload)
    backup = pending.backup_path / backup_relative
    # Reproduce the durable state left by the old rollback implementation when
    # every reverse operation completed but journal unlink did not.
    if atomic_tree:
        shutil.rmtree(destination)
    else:
        destination.unlink()
    os.replace(backup, destination)
    shutil.rmtree(pending.backup_path)
    shutil.rmtree(pending.staging_path)
    legacy_journal = json.loads(pending.journal_path.read_text(encoding="utf-8"))
    assert legacy_journal["state"] == "pending_core_validation"

    assert recover_pending_commits(target) == [import_id]
    if atomic_tree:
        assert (destination / "old.bin").read_bytes() == b"original tree"
        assert not (destination / "new.bin").exists()
    else:
        assert destination.read_bytes() == b"original file"
    assert not list(target.glob(".legacy-import-*"))


def test_commit_moves_tts_as_one_transactional_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target"
    payload = target / ".legacy-import-staging-test-import-tree" / "payload"
    (target / "tts").mkdir(parents=True)
    (target / "tts/old-resource.bin").write_bytes(b"old")
    staged_tts = payload / "tts" / "runtime"
    staged_tts.mkdir(parents=True)
    for index in range(100):
        (staged_tts / f"resource-{index}.bin").write_bytes(str(index).encode())
    writes = 0
    real_write_journal = legacy_transaction._write_journal

    def count_write(path: Path, value: object) -> None:
        nonlocal writes
        writes += 1
        real_write_journal(path, value)

    monkeypatch.setattr(legacy_transaction, "_write_journal", count_write)

    pending = commit_payload(target, "test-import-tree", payload)

    journal = json.loads(pending.journal_path.read_text(encoding="utf-8"))
    assert journal["installedTrees"] == ["tts"]
    assert journal["backupTrees"] == ["tts"]
    assert journal["installed"] == []
    assert writes == 4
    assert len(list((target / "tts/runtime").glob("*.bin"))) == 100
    assert not (target / "tts/old-resource.bin").exists()

    rollback_commit(pending)
    assert (target / "tts").is_dir()
    assert (target / "tts/old-resource.bin").read_bytes() == b"old"


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


@pytest.mark.skipif(__import__("platform").system() != "Windows", reason="v1 supports Windows imports")
@pytest.mark.parametrize("version", ["0.9.6", "0.9.8", "0.9.9"])
def test_supported_legacy_layout_revisions_are_detected_by_structure(
    tmp_path: Path, version: str
) -> None:
    source = _legacy_fixture(tmp_path, version)
    target = tmp_path / "target"
    target.mkdir()
    inspection = inspect_legacy_installation(source, target)
    assert inspection.compatible
    assert inspection.detected_version == version


@pytest.mark.skipif(__import__("platform").system() != "Windows", reason="v1 supports Windows imports")
def test_unrecognized_tts_layout_is_warning_and_not_required_disk_space(
    tmp_path: Path,
) -> None:
    source = _legacy_fixture(tmp_path)
    unexpected = source / "tts" / "unexpected-runtime"
    unexpected.mkdir()
    optional_bytes = 2 * 1024 * 1024
    (unexpected / "runtime.bin").write_bytes(b"x" * optional_bytes)
    target = tmp_path / "target"
    target.mkdir()

    inspection = inspect_legacy_installation(source, target)

    assert inspection.compatible
    assert not inspection.blockers
    assert any(
        warning["code"] == "LEGACY_TTS_LAYOUT_UNRECOGNIZED"
        for warning in inspection.warnings
    )
    assert inspection.required_bytes < optional_bytes

    report, pending = run_legacy_import(
        source,
        target,
        import_id="test-optional-tts-layout",
        inspection=inspection,
        finalize=True,
    )
    assert pending is None
    assert report.counts["ttsSkipped"] == 1
    TimelineStore(target / "data/chat_history/timeline.sqlite3").assert_activated()


def test_commit_fault_after_journal_intent_rolls_back_every_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target"
    payload = target / ".legacy-import-staging-test-import-fault" / "payload"
    (target / "config").mkdir(parents=True)
    (payload / "config").mkdir(parents=True)
    (target / "config/a.json").write_text("old-a", encoding="utf-8")
    (target / "config/b.json").write_text("old-b", encoding="utf-8")
    (payload / "config/a.json").write_text("new-a", encoding="utf-8")
    (payload / "config/b.json").write_text("new-b", encoding="utf-8")
    original_replace = legacy_transaction.os.replace
    injected = False

    def replace_with_one_failure(source: object, destination: object) -> None:
        nonlocal injected
        source_path = Path(source)  # type: ignore[arg-type]
        if not injected and source_path == payload / "config/b.json":
            injected = True
            raise OSError("injected commit failure")
        original_replace(source, destination)

    monkeypatch.setattr(legacy_transaction.os, "replace", replace_with_one_failure)
    with pytest.raises(LegacyImportError, match="LEGACY_COMMIT_FAILED"):
        commit_payload(target, "test-import-fault", payload)

    assert (target / "config/a.json").read_text(encoding="utf-8") == "old-a"
    assert (target / "config/b.json").read_text(encoding="utf-8") == "old-b"
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


def test_history_orders_archives_before_active_and_ids_are_deterministic(tmp_path: Path) -> None:
    source = tmp_path / "legacy"
    history = source / "data" / "chat_history"
    history.mkdir(parents=True)
    archived = [
        {"created_at": "2026-01-01T00:00:00+08:00", "role": "user", "content": "old"},
        {
            "created_at": "2026-01-01T00:00:01+08:00",
            "role": "assistant",
            "content": "old reply",
        },
    ]
    active = [
        {"created_at": "2026-01-02T00:00:00+08:00", "role": "user", "content": "new"},
        {
            "created_at": "2026-01-02T00:00:01+08:00",
            "role": "assistant",
            "content": "new reply",
        },
    ]
    (history / "Sakura.jsonl.archive").write_text(
        "\n" + "".join(json.dumps(item) + "\n" for item in archived), encoding="utf-8"
    )
    (history / "Sakura.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in active), encoding="utf-8"
    )

    first = tmp_path / "first"
    second = tmp_path / "second"
    first_stats = import_history(
        source, first, character_ids=("Sakura",), processed_counts={"Sakura": 2}
    )
    second_stats = import_history(
        source, second, character_ids=("Sakura",), processed_counts={"Sakura": 2}
    )
    first_entries = TimelineStore(first / "data/chat_history/timeline.sqlite3").read_all("Sakura")
    second_entries = TimelineStore(second / "data/chat_history/timeline.sqlite3").read_all("Sakura")

    assert [entry.payload for entry in first_entries if entry.kind == TimelineKind.HUMAN] == [
        {"text": "old"},
        {"text": "new"},
    ]
    assert [entry.entry_id for entry in first_entries] == [
        entry.entry_id for entry in second_entries
    ]
    assert first_stats.cutoff_entry_ids == second_stats.cutoff_entry_ids
    assert first_stats.cutoff_entry_ids["Sakura"] == first_entries[1].entry_id


def test_history_rejects_unknown_role_at_exact_source_line(tmp_path: Path) -> None:
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

    with pytest.raises(LegacyImportError) as raised:
        import_history(source, tmp_path / "staged", character_ids=())
    assert raised.value.to_public_dict() == {
        "code": "LEGACY_HISTORY_ROLE_UNSUPPORTED",
        "stage": "staging",
        "relativePath": "data/chat_history/orphan.jsonl",
        "line": 2,
    }
    assert "private content" not in str(raised.value)


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


def test_mcp_migration_matches_extended_source_path_against_drive_path(
    tmp_path: Path,
) -> None:
    source = Path(r"\\?\D:\legacy-root")
    config = tmp_path / "mcp.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "servers": {
                    "legacy": {
                        "transport": "stdio",
                        "command": r"D:\legacy-root\tools\server.exe",
                    },
                    "current": {
                        "transport": "stdio",
                        "command": r"D:\current-root\tools\server.exe",
                    },
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    migrated, dropped = _migrate_mcp(source, config)

    assert dropped == 1
    assert set(migrated["servers"]) == {"current"}


def test_mcp_migration_reports_quarantined_server_count(tmp_path: Path) -> None:
    source = _legacy_fixture(tmp_path)
    config = source / "data/config/mcp.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "servers": {
                    "legacy": {
                        "transport": "stdio",
                        "command": str(source / "tools/server.exe"),
                    },
                    "current": {
                        "transport": "stdio",
                        "command": "runner",
                    },
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    staged = tmp_path / "staged"

    counts = migrate_configuration(
        source,
        staged,
        new_tts_root=tmp_path / "target/tts",
    )

    assert counts["mcpServersQuarantined"] == 1
    migrated = yaml.safe_load((staged / "config/mcp.yaml").read_text(encoding="utf-8"))
    assert set(migrated["servers"]) == {"current"}


@pytest.mark.parametrize("separator", ["/", "\\"])
def test_mcp_migration_rebinds_legacy_web_server_tokens(
    tmp_path: Path, separator: str
) -> None:
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
    ("relative", "content", "code"),
    [
        ("data/reminders.json", "[]", "LEGACY_REMINDERS_VALIDATION_FAILED"),
        ("data/tasks.json", "[]", "LEGACY_TASKS_VALIDATION_FAILED"),
        (
            "data/config/mcp.yaml",
            "enabled: true\ndefault_call_timeout: invalid\nservers: {}\n",
            "LEGACY_MCP_VALIDATION_FAILED",
        ),
    ],
)
def test_current_validators_block_invalid_user_data_atomically(
    tmp_path: Path, relative: str, content: str, code: str
) -> None:
    source = _legacy_fixture(tmp_path)
    path = source / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    target = tmp_path / "target"
    target.mkdir()

    with pytest.raises(LegacyImportError, match=code):
        run_legacy_import(source, target, import_id="test-import-validator", finalize=True)
    assert list(target.iterdir()) == []


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
def test_per_character_curation_state_keeps_orphan_history_tail_position(tmp_path: Path) -> None:
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

    state = json.loads(
        (target / "data/memory/curation_state/Orphan.json").read_text(encoding="utf-8")
    )
    assert state["processed_history_count"] == 1
    assert state["backfill_completed"] is True
    assert state["curation_cursor"]
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


def test_large_artifact_manifest_keeps_deterministic_order_and_hashes(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "payload"
    for index in reversed(range(40)):
        path = payload / "tts" / f"resource-{index:02d}.bin"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"resource-{index}".encode())

    byte_progress: list[tuple[int, int]] = []
    artifacts = _build_artifact_manifest(
        payload,
        lambda: False,
        byte_progress=lambda completed, expected: byte_progress.append(
            (completed, expected)
        ),
    )

    assert [artifact["id"] for artifact in artifacts] == sorted(
        artifact["id"] for artifact in artifacts
    )
    assert all(
        artifact["domain"] == "tts"
        and len(str(artifact["sha256"])) == 64
        and int(artifact["bytes"]) > 0
        for artifact in artifacts
    )
    expected_bytes = sum(len(f"resource-{index}") for index in range(40))
    assert byte_progress[0] == (0, expected_bytes)
    assert byte_progress[-1] == (expected_bytes, expected_bytes)


@pytest.mark.skipif(__import__("os").name != "nt", reason="robocopy is Windows-only")
def test_windows_fast_copy_preserves_filtered_tree_without_python_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    (source / "nested").mkdir(parents=True)
    (source / "nested" / "model.bin").write_bytes(b"model")
    (source / "nested" / "readme.txt").write_text("readme", encoding="utf-8")
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "noise.pyc").write_bytes(b"noise")
    extended_source = Path("\\\\?\\" + str(source))
    target = tmp_path / "target"

    monkeypatch.setattr(
        legacy_files,
        "copy_tree_checked",
        lambda *_args, **_kwargs: pytest.fail("unexpected Python copy fallback"),
    )
    byte_progress: list[tuple[int, int]] = []
    files, size = copy_tree_fast_checked(
        extended_source,
        target,
        cancelled=lambda: False,
        skip_noise=True,
        byte_progress=lambda copied, expected: byte_progress.append((copied, expected)),
    )

    assert (files, size) == (2, len(b"model") + len("readme"))
    assert (target / "nested" / "model.bin").read_bytes() == b"model"
    assert (target / "nested" / "readme.txt").read_text(encoding="utf-8") == "readme"
    assert not (target / "__pycache__").exists()
    assert byte_progress[0] == (0, size)
    assert byte_progress[-1] == (size, size)


def test_tts_copy_only_excludes_noise_names_at_the_resource_root(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "diagnostics").mkdir(parents=True)
    (source / "diagnostics" / "runtime.log").write_text("noise", encoding="utf-8")
    package = source / "runtime" / "site-packages" / "torch" / "diagnostics"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")

    target = tmp_path / "target"
    files, _size = copy_tree_fast_checked(
        source,
        target,
        cancelled=lambda: False,
        skip_noise=True,
        noise_names_at_root_only=True,
    )

    assert files == 1
    assert not (target / "diagnostics").exists()
    assert (target / "runtime" / "site-packages" / "torch" / "diagnostics" / "__init__.py").is_file()


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


def test_tts_onnx_failure_is_logged_with_specific_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    payload = tmp_path / "payload"
    source.mkdir()
    payload.mkdir()
    logged: list[tuple[str, dict[str, object], str]] = []

    def fail_onnx(*_args: object, **_kwargs: object) -> tuple[int, int]:
        raise OSError(5, "access denied")

    def capture_log(
        _import_id: str,
        event: str,
        _message: str,
        attributes: object = None,
        *,
        severity: str = "info",
    ) -> None:
        logged.append((event, dict(attributes or {}), severity))

    monkeypatch.setattr(legacy_importer, "_copy_legacy_onnx", fail_onnx)
    monkeypatch.setattr(legacy_importer, "_log_legacy_import", capture_log)

    with pytest.raises(OSError, match="access denied"):
        _copy_tts(source, payload, lambda: False, import_id="test-tts-onnx")

    failure = next(item for item in logged if item[0] == "legacy_import.tts_copy_failed")
    assert failure[1]["detail_stage"] == "legacy_onnx"
    assert failure[1]["error_type"] == "OSError"
    assert failure[1]["reason_code"] == "ERRNO_5"
    assert failure[1]["stage"] == "legacy_onnx"
    assert failure[1]["errno"] == 5
    assert failure[2] == "error"


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


def test_tts_runtime_path_adaptation_removes_old_absolute_pth_entries(tmp_path: Path) -> None:
    tts_root = tmp_path / "payload" / "tts"
    site_packages = tts_root / "g50" / "runtime" / "Lib" / "site-packages"
    site_packages.mkdir(parents=True)
    users = site_packages / "users.pth"
    users.write_text(
        "D:\\Old Sakura\\tts\\g50\n"
        "D:/Old Sakura/tts/g50/tools\n",
        encoding="utf-8",
    )
    mixed = site_packages / "mixed.pth"
    mixed.write_text(
        "./relative-package\n"
        "import runtime_bootstrap\n"
        "C:\\Old Sakura\\runtime\n",
        encoding="utf-8",
    )
    model_weights = site_packages / "torchmetrics" / "lpips_models" / "alex.pth"
    model_weights.parent.mkdir(parents=True)
    model_payload = b"\x80\x04binary-model-weights"
    model_weights.write_bytes(model_payload)

    changed, _byte_delta = _sanitize_tts_runtime_pth_files(tts_root)

    assert changed == 2
    assert "Old Sakura" not in users.read_text(encoding="utf-8")
    assert mixed.read_text(encoding="utf-8") == (
        "./relative-package\nimport runtime_bootstrap\n"
    )
    assert model_weights.read_bytes() == model_payload


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
