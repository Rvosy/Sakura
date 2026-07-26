from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from harness.runner import HarnessError, load_manifest, run_profile


def _write_manifest(path: Path, *, exit_code: int = 0) -> Path:
    manifest = {
        "schema_version": 1,
        "profiles": {
            "smoke": {
                "description": "fixture profile",
                "cases": ["fixture"],
            }
        },
        "cases": [
            {
                "id": "fixture",
                "description": "fixture case",
                "argv": [
                    "{python}",
                    "-c",
                    f"print('fixture-output'); raise SystemExit({exit_code})",
                ],
                "timeout_seconds": 10,
                "env": {"HARNESS_FIXTURE": "1"},
            }
        ],
    }
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_default_manifest_is_valid_and_has_smoke_profile() -> None:
    manifest = load_manifest()

    assert manifest["schema_version"] == 1
    assert manifest["profiles"]["smoke"]["cases"]


def test_manifest_rejects_unknown_profile_case(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path / "suites.json")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["profiles"]["smoke"]["cases"] = ["missing"]
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(HarnessError, match="unknown cases: missing"):
        load_manifest(path)


@pytest.mark.parametrize(
    ("process_exit_code", "expected_exit_code", "expected_status"),
    [(0, 0, "passed"), (7, 1, "failed")],
)
def test_run_profile_records_process_result(
    tmp_path: Path,
    process_exit_code: int,
    expected_exit_code: int,
    expected_status: str,
) -> None:
    manifest = load_manifest(
        _write_manifest(tmp_path / "suites.json", exit_code=process_exit_code)
    )
    report_path = tmp_path / "report.json"

    exit_code, report, destination = run_profile(
        manifest,
        "smoke",
        report_path=report_path,
    )

    persisted = json.loads(report_path.read_text(encoding="utf-8"))
    assert exit_code == expected_exit_code
    assert report["status"] == expected_status
    assert destination == report_path.resolve()
    assert persisted["cases"][0]["argv"][0] == sys.executable
    assert persisted["cases"][0]["exit_code"] == process_exit_code
    assert "fixture-output" in persisted["cases"][0]["stdout"]


def test_run_profile_rejects_unknown_profile(tmp_path: Path) -> None:
    manifest = load_manifest(_write_manifest(tmp_path / "suites.json"))

    with pytest.raises(HarnessError, match="unknown profile: missing"):
        run_profile(manifest, "missing", report_path=tmp_path / "report.json")
