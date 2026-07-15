from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = REPO_ROOT / "docs/runtime-v2/baselines/wp_0_02_contract.py"
FIXTURE_ROOT = REPO_ROOT / "tests/fixtures/runtime_v2/wp_0_02"


def _load_contract():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("wp_0_02_contract", CONTRACT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_fixture_is_synthetic_complete_and_secret_free() -> None:
    contract = _load_contract()

    manifest = contract.validate_fixture_root(FIXTURE_ROOT)

    assert manifest["synthetic"] is True
    assert {category["id"] for category in manifest["categories"]} == contract.EXPECTED_CATEGORIES


def test_supported_config_version_matches_legacy_migration_epoch() -> None:
    contract = _load_contract()
    source = (REPO_ROOT / "app/config/migration_runner.py").read_text(encoding="utf-8")
    match = re.search(r"^CURRENT_CONFIG_VERSION\s*=\s*(\d+)\s*$", source, re.MULTILINE)

    assert match is not None
    assert contract.SUPPORTED_CONFIG_VERSION == int(match.group(1))


def test_contract_matrix_is_repeatable_and_does_not_modify_fixture(tmp_path: Path) -> None:
    contract = _load_contract()
    before = contract.tree_manifest(FIXTURE_ROOT)

    first = contract.run_contract(FIXTURE_ROOT, tmp_path / "run-1")
    second = contract.run_contract(FIXTURE_ROOT, tmp_path / "run-2")

    expected = {
        "normal_qt_tauri_qt",
        "backup_failure",
        "temporary_write_failure",
        "atomic_replace_failure",
        "abnormal_interruption",
        "corrupt_file",
        "future_schema",
    }
    assert set(first["scenarios"]) == expected
    assert set(second["scenarios"]) == expected
    assert all(result["status"] == "passed" for result in first["scenarios"].values())
    assert all(result["status"] == "passed" for result in second["scenarios"].values())
    assert first["fixture_tree_sha256"] == second["fixture_tree_sha256"]
    assert first["fixture_unchanged"] is True
    assert second["fixture_unchanged"] is True
    assert contract.tree_manifest(FIXTURE_ROOT) == before


def test_reference_atomic_commit_keeps_verified_previous_version(tmp_path: Path) -> None:
    contract = _load_contract()
    target = tmp_path / "tasks.json"
    target.write_text('{"tasks": []}\n', encoding="utf-8")
    original_hash = contract.sha256_file(target)

    result = contract.strong_atomic_write_json(
        target,
        {
            "tasks": [
                {
                    "id": "fixture",
                    "text": "[REDACTED_FIXTURE_TASK]",
                    "created_at": "2000-01-01T00:00:00+00:00",
                    "completed_at": None,
                }
            ]
        },
    )

    backup = target.with_name("tasks.json.compat.bak")
    assert result["status"] == "committed"
    assert backup.is_file()
    assert contract.sha256_file(backup) == original_hash
    assert contract.sha256_file(target) != original_hash
