from __future__ import annotations

import json
from pathlib import Path

import yaml

from app.config import tts_plugin_cutover as cutover


def _write_character(
    root: Path,
    *,
    character_id: str = "alpha",
    extensions: dict | None = None,
    tone_refs: str = "voice/refs/ref.txt",
) -> Path:
    package = root / "characters" / character_id
    refs = package / "voice" / "refs"
    models = package / "voice" / "models"
    refs.mkdir(parents=True)
    models.mkdir(parents=True)
    (package / "card.md").write_text("card", encoding="utf-8")
    (package / "portrait.png").write_bytes(b"portrait")
    (refs / "tone.wav").write_bytes(b"wav")
    (refs / "ref.txt").write_text(
        "voice/refs/tone.wav|ja|hello|中性\n",
        encoding="utf-8",
    )
    (models / "gpt.ckpt").write_bytes(b"gpt")
    (models / "sovits.pth").write_bytes(b"sovits")
    manifest = {
        "id": character_id,
        "display_name": "Alpha Voice",
        "card": "card.md",
        "portrait": {"default": "portrait.png"},
        "voice": {
            "tone_refs": tone_refs,
            "ref_lang": "ja",
            "text_lang": "zh",
            "gpt_model": "voice/models/gpt.ckpt",
            "sovits_model": "voice/models/sovits.pth",
        },
    }
    if extensions is not None:
        manifest["extensions"] = extensions
    path = package / "character.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _write_api(root: Path, tts: dict) -> Path:
    path = root / "data" / "config" / "api.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(
        yaml.safe_dump({"llm": {"model": "keep"}, "tts": tts}, sort_keys=False),
        encoding="utf-8",
    )
    return path


def test_cutover_is_copy_only_idempotent_and_preserves_new_values(tmp_path: Path) -> None:
    root = tmp_path / "assistant"
    api_path = _write_api(
        root,
        {
            "enabled": True,
            "provider": "custom-gpt-sovits",
            "gpt_sovits": {
                "api_url": "https://voice.example.test/api/tts",
                "timeout_seconds": 120,
                "remote_reference_root": "/srv/refs",
                "managed_runtime": {"work_dir": "legacy/gpt"},
            },
        },
    )
    manifest_path = _write_character(
        root,
        extensions={"sakura.tts.gpt-sovits": {"refLang": "ko"}},
    )
    config_path = root / "data" / "plugins" / cutover.GPT_PROVIDER_ID / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({"timeoutSeconds": 17, "customBaseUrl": "https://new.example.test"}),
        encoding="utf-8",
    )
    old_api = api_path.read_bytes()
    old_voice = json.loads(manifest_path.read_text(encoding="utf-8"))["voice"]

    first = cutover.migrate_legacy_tts_to_plugins(root)
    assert first.changed_files >= 1
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["timeoutSeconds"] == 17
    assert config["endpointMode"] == "custom"
    assert config["customBaseUrl"] == "https://new.example.test"
    assert config["ttsPath"] == "/api/tts"
    assert config["remoteReferenceRoot"] == "/srv/refs"
    assert "workDir" not in config

    migrated = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert migrated["voice"] == old_voice
    assert migrated["extensions"]["sakura.tts"] == {
        "enabled": True,
        "provider": cutover.GPT_PROVIDER_ID,
    }
    provider = migrated["extensions"][cutover.GPT_PROVIDER_ID]
    assert provider["refLang"] == "ko"
    assert provider["toneRefs"] == "voice/refs/ref.txt"
    assert provider["textLang"] == "zh"
    assert api_path.read_bytes() == old_api

    snapshots = {
        path: path.read_bytes()
        for path in (
            config_path,
            root / "data" / "plugins" / cutover.GENIE_PROVIDER_ID / "config.json",
            manifest_path,
        )
    }
    second = cutover.migrate_legacy_tts_to_plugins(root)
    assert second.changed_files == 0
    assert {path: path.read_bytes() for path in snapshots} == snapshots


def test_cutover_derives_missing_gpt_mode_from_provider_owned_endpoint(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"customBaseUrl": "https://voice.example.test"}),
        encoding="utf-8",
    )

    assert cutover._merge_json_file(path, {"endpointMode": "managed"}) == "changed"
    assert json.loads(path.read_text(encoding="utf-8"))["endpointMode"] == "custom"


def test_cutover_resolves_runtime_paths_and_migrates_custom_genie_character(tmp_path: Path) -> None:
    root = tmp_path / "assistant"
    _write_api(
        root,
        {
            "enabled": False,
            "provider": "genie-tts",
            "gpt_sovits": {
                "managed_runtime": {
                    "work_dir": "runtime/gpt",
                    "python_path": "runtime/python.exe",
                    "tts_config_path": "runtime/tts.yaml",
                }
            },
            "genie_tts": {
                "api_url": "https://genie.example.test/",
                "timeout_seconds": 90,
            },
        },
    )
    manifest_path = _write_character(root)

    cutover.migrate_legacy_tts_to_plugins(root)

    gpt = json.loads(
        (root / "data" / "plugins" / cutover.GPT_PROVIDER_ID / "config.json").read_text(
            encoding="utf-8"
        )
    )
    assert Path(gpt["workDir"]).is_absolute()
    assert gpt["endpointMode"] == "managed"
    assert Path(gpt["workDir"]) == (root / "runtime" / "gpt").resolve()
    assert Path(gpt["pythonPath"]) == (root / "runtime" / "python.exe").resolve()
    genie = json.loads(
        (root / "data" / "plugins" / cutover.GENIE_PROVIDER_ID / "config.json").read_text(
            encoding="utf-8"
        )
    )
    assert genie == {
        "endpointMode": "custom",
        "apiUrl": "https://genie.example.test/",
        "timeoutSeconds": 90,
    }
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["extensions"]["sakura.tts"] == {
        "enabled": False,
        "provider": cutover.GENIE_PROVIDER_ID,
    }
    assert manifest["extensions"][cutover.GENIE_PROVIDER_ID] == {
        "remoteCharacterName": "Alpha Voice"
    }


def test_cutover_retries_independent_atomic_write_failures(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "assistant"
    api_path = _write_api(
        root,
        {"enabled": True, "provider": "gpt-sovits", "gpt_sovits": {}},
    )
    manifest_path = _write_character(root)
    old_api = api_path.read_bytes()
    old_voice = json.loads(manifest_path.read_text(encoding="utf-8"))["voice"]
    real_write = cutover.atomic_write_text
    calls = 0

    def fail_first(path, text, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected atomic write failure")
        return real_write(path, text, **kwargs)

    monkeypatch.setattr(cutover, "atomic_write_text", fail_first)
    first = cutover.migrate_legacy_tts_to_plugins(root)
    assert first.failed_files == 1
    monkeypatch.setattr(cutover, "atomic_write_text", real_write)
    second = cutover.migrate_legacy_tts_to_plugins(root)
    assert second.failed_files == 0
    assert second.changed_files == 1
    assert api_path.read_bytes() == old_api
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["voice"] == old_voice
    assert (
        root / "data" / "plugins" / cutover.GPT_PROVIDER_ID / "config.json"
    ).is_file()


def test_cutover_skips_malformed_or_escaping_legacy_character(tmp_path: Path) -> None:
    root = tmp_path / "assistant"
    _write_api(root, {"enabled": True, "provider": "gpt-sovits"})
    manifest_path = _write_character(root, tone_refs="../outside.txt")
    report = cutover.migrate_legacy_tts_to_plugins(root)

    assert report.failed_files == 0
    migrated = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert migrated["extensions"]["sakura.tts"] == {
        "provider": cutover.GPT_PROVIDER_ID,
        "enabled": True,
    }
    assert cutover.GPT_PROVIDER_ID not in migrated["extensions"]
    assert migrated["voice"]["tone_refs"] == "../outside.txt"
