from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PYTHON_DRIVER = ROOT / "tests/fixtures/runtime_v2/wp_3v_01/acceptance_driver.py"


def _load_driver():
    spec = importlib.util.spec_from_file_location("wp_3v_01_acceptance_driver", PYTHON_DRIVER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_acceptance_manifest_and_secret_scan_are_narrow(tmp_path: Path) -> None:
    driver = _load_driver()
    root = tmp_path / "app-root"
    history = root / "data/chat_history/fixture.jsonl"
    config = root / "data/config/api.yaml"
    history.parent.mkdir(parents=True)
    config.parent.mkdir(parents=True)
    history.write_text("before\n", encoding="utf-8")
    config.write_text("api_key: REDACTED\n", encoding="utf-8")
    before = driver.manifest(root)

    history.write_text("before\nafter\n", encoding="utf-8")
    after = driver.manifest(root)

    assert driver.changed_paths(before, after) == {"data/chat_history/fixture.jsonl"}
    assert driver.find_sensitive_evidence("safe output") == []
    assert driver.find_sensitive_evidence("Authorization: Bearer PRIVATE_TOKEN")


def test_frozen_legacy_oracle_seed_is_part_of_the_before_manifest(tmp_path: Path) -> None:
    driver = _load_driver()
    app_root = tmp_path / "app-root"
    history = app_root / "data/chat_history/fixture.jsonl"
    history.parent.mkdir(parents=True)
    history.write_text("", encoding="utf-8")

    driver.seed_frozen_legacy_oracle_markers(app_root)
    before = driver.manifest(app_root)
    entries = [json.loads(line) for line in history.read_text(encoding="utf-8").splitlines()]

    assert {entry["content"] for entry in entries} == {
        "[WP-3-06-LEGACY-USER]",
        "[WP-3-06-LEGACY-REPLY]",
        "[WP-3-06-TAURI-USER]",
        "[WP-3-06-TAURI-REPLY]",
    }
    assert "data/chat_history/fixture.jsonl" in before


def test_provider_message_classification_uses_the_latest_user_turn() -> None:
    driver = _load_driver()
    request = {
        "messages": [
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "[WP-3V-01-CANCEL]"},
        ]
    }

    assert driver._last_user_message(request) == "[WP-3V-01-CANCEL]"


def test_tauri_and_legacy_oracle_environments_are_isolated(
    tmp_path: Path, monkeypatch
) -> None:
    driver = _load_driver()
    monkeypatch.delenv(driver.WP3V_DIRECTORY_ENV, raising=False)
    monkeypatch.delenv(driver.WP3V_MODE_ENV, raising=False)
    monkeypatch.delenv(driver.LEGACY_DIRECTORY_ENV, raising=False)
    monkeypatch.delenv(driver.LEGACY_MODE_ENV, raising=False)

    tauri = driver.environment(tmp_path)
    legacy = driver.environment(tmp_path, legacy=True)

    assert tauri[driver.WP3V_MODE_ENV] == "vertical"
    assert driver.LEGACY_MODE_ENV not in tauri
    assert legacy[driver.LEGACY_MODE_ENV] == "legacy-read"
    assert driver.WP3V_MODE_ENV not in legacy
