from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from harness.report import write_json_atomic
from harness.runner import (
    HarnessError,
    _build_parser,
    _write_captured_output,
    load_manifest,
    main,
    run_profile,
)


def _manifest(
    path: Path,
    *,
    exit_code: int = 0,
    timeout: float = 10,
    env: dict[str, str] | None = None,
    code: str | None = None,
) -> Path:
    value = {
        "schema_version": 1,
        "profiles": {
            "first": {"description": "first", "cases": ["a", "b"]},
            "second": {"description": "second", "cases": ["b", "c"]},
        },
        "cases": [
            {
                "id": case_id,
                "description": case_id,
                "argv": [
                    "{python}",
                    "-c",
                    code
                    or f"print('{case_id}-output'); raise SystemExit({exit_code})",
                ],
                "timeout_seconds": timeout,
                "env": env or {},
            }
            for case_id in ("a", "b", "c")
        ],
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_default_manifest_and_profile_topology_are_valid() -> None:
    manifest = load_manifest()

    assert manifest["schema_version"] == 1
    assert {
        "smoke",
        "harness",
        "unit",
        "docs",
        "core-host",
        "python-full",
        "release-distribution",
        "runtime-v2-shell",
        "legacy-import",
    } <= set(manifest["profiles"])
    shell = manifest["profiles"]["runtime-v2-shell"]["cases"]
    core = manifest["profiles"]["core-host"]["cases"]
    release = manifest["profiles"]["release-distribution"]["cases"]
    legacy_import = manifest["profiles"]["legacy-import"]["cases"]
    assert "runtime-v2-provider-model-tests" not in shell
    assert "runtime-v2-memory-tests" not in shell
    assert {"runtime-v2-provider-model-tests", "runtime-v2-memory-tests"} <= set(core)
    assert "runtime-v2-release-distribution-tests" in release
    assert {
        "legacy-import-python-tests",
        "legacy-import-rust-tests",
        "legacy-import-rust-transaction-tests",
    } <= set(legacy_import)
    assert "harness-agent-development-tests" not in {
        item["id"] for item in manifest["cases"]
    }
    used = {
        case_id
        for profile in manifest["profiles"].values()
        for case_id in profile["cases"]
    }
    assert used == {item["id"] for item in manifest["cases"]}


def test_manifest_rejects_unknown_duplicate_and_unreferenced_cases(
    tmp_path: Path,
) -> None:
    path = _manifest(tmp_path / "suites.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    value["profiles"]["first"]["cases"] = ["a", "missing"]
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(HarnessError, match="unknown cases: missing"):
        load_manifest(path)

    value["profiles"]["first"]["cases"] = ["a", "a"]
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(HarnessError, match="contains duplicates"):
        load_manifest(path)

    value["profiles"]["first"]["cases"] = ["a", "b"]
    value["profiles"]["second"]["cases"] = ["b"]
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(HarnessError, match="unreferenced cases: c"):
        load_manifest(path)


def test_profile_runs_cases_in_manifest_order(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    manifest = load_manifest(_manifest(tmp_path / "suites.json"))

    _, report, _ = run_profile(manifest, "second", repo_root=repo)

    assert [case["id"] for case in report["cases"]] == ["b", "c"]


def test_list_preserves_profile_and_case_order(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path = _manifest(tmp_path / "suites.json")

    assert main(["--manifest", str(manifest_path), "list"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        "first: first",
        "  - a: a",
        "  - b: b",
        "second: second",
        "  - b: b",
        "  - c: c",
    ]


@pytest.mark.parametrize("removed_command", ["current", "check", "verify"])
def test_cli_only_exposes_list_and_run(removed_command: str) -> None:
    assert _build_parser().parse_args(["list"]).command == "list"
    assert _build_parser().parse_args(["run", "smoke"]).command == "run"
    with pytest.raises(SystemExit) as raised:
        main([removed_command])
    assert raised.value.code == 2


@pytest.mark.parametrize(
    ("process_exit", "expected_exit", "expected_status"),
    [(0, 0, "passed"), (7, 1, "failed")],
)
def test_run_profile_records_process_result(
    tmp_path: Path,
    process_exit: int,
    expected_exit: int,
    expected_status: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    manifest = load_manifest(
        _manifest(tmp_path / "suites.json", exit_code=process_exit)
    )
    report_path = tmp_path / "report.json"

    exit_code, report, destination = run_profile(
        manifest, "first", report_path=report_path, repo_root=repo
    )

    persisted = json.loads(report_path.read_text(encoding="utf-8"))
    assert exit_code == expected_exit
    assert report["status"] == expected_status
    assert report["summary"]["total"] == 2
    assert [case["id"] for case in report["cases"]] == ["a", "b"]
    assert destination == report_path.resolve()
    assert persisted["cases"][0]["argv"][0] == sys.executable
    assert persisted["cases"][0]["exit_code"] == process_exit
    assert "a-output" in persisted["cases"][0]["stdout"]


def test_run_profile_records_real_utf8_output(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    manifest = load_manifest(
        _manifest(tmp_path / "suites.json", code="print('中文', flush=True)")
    )

    exit_code, report, _ = run_profile(manifest, "first", repo_root=repo)

    assert exit_code == 0
    assert [case["stdout"].strip() for case in report["cases"]] == ["中文", "中文"]


def test_each_run_uses_unique_repo_temp_env_and_case_env_can_override(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo with 空格"
    repo.mkdir()
    code = (
        "import json, os; "
        "print(json.dumps({k: os.environ[k] for k in ('TMPDIR','TMP','TEMP')}))"
    )
    manifest = load_manifest(_manifest(tmp_path / "suites.json", code=code))

    _, first, _ = run_profile(manifest, "first", repo_root=repo)
    _, second, _ = run_profile(manifest, "first", repo_root=repo)

    first_root = Path(first["runtime_tmp_root"])
    assert first_root != Path(second["runtime_tmp_root"])
    assert first_root.parent.parent == (repo / "temp/harness/runtime-tmp").resolve().parent
    env = json.loads(first["cases"][0]["stdout"])
    assert env == {"TMPDIR": str(first_root), "TMP": str(first_root), "TEMP": str(first_root)}

    override = load_manifest(
        _manifest(tmp_path / "override.json", code=code, env={"TMPDIR": "case-value"})
    )
    _, report, _ = run_profile(override, "first", repo_root=repo)
    overridden = json.loads(report["cases"][0]["stdout"])
    assert overridden["TMPDIR"] == "case-value"
    assert overridden["TMP"] == report["runtime_tmp_root"]
    assert overridden["TEMP"] == report["runtime_tmp_root"]


def test_twenty_millisecond_timeout_does_not_assume_python_started(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    code = "import time; time.sleep(1)"
    manifest = load_manifest(
        _manifest(tmp_path / "suites.json", code=code, timeout=0.02)
    )

    exit_code, report, _ = run_profile(manifest, "first", repo_root=repo)

    assert exit_code == 1
    assert [case["timed_out"] for case in report["cases"]] == [True, True]
    assert [case["exit_code"] for case in report["cases"]] == [None, None]


@pytest.mark.parametrize(
    ("captured_stdout", "captured_stderr"),
    [
        ("中文", "错误"),
        ("中文".encode(), "错误".encode()),
    ],
)
def test_timeout_preserves_utf8_output_returned_by_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    captured_stdout: str | bytes,
    captured_stderr: str | bytes,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    manifest = load_manifest(
        _manifest(tmp_path / "suites.json", code="raise AssertionError", timeout=0.02)
    )
    observed_timeouts: list[float] = []

    def raise_timeout(*args: object, **kwargs: object) -> None:
        observed_timeouts.append(float(kwargs["timeout"]))
        raise subprocess.TimeoutExpired(
            cmd=args[0],
            timeout=kwargs["timeout"],
            output=captured_stdout,
            stderr=captured_stderr,
        )

    monkeypatch.setattr("harness.runner.subprocess.run", raise_timeout)

    exit_code, report, _ = run_profile(manifest, "first", repo_root=repo)

    assert exit_code == 1
    assert observed_timeouts == [0.02, 0.02]
    assert [case["timed_out"] for case in report["cases"]] == [True, True]
    assert [case["stdout"] for case in report["cases"]] == ["中文", "中文"]
    assert [case["stderr"] for case in report["cases"]] == ["错误", "错误"]


def test_unknown_profile_and_narrow_console_encoding(tmp_path: Path) -> None:
    manifest = load_manifest(_manifest(tmp_path / "suites.json"))
    with pytest.raises(HarnessError, match="unknown profile: missing"):
        run_profile(manifest, "missing", repo_root=tmp_path)

    class AsciiStream(io.StringIO):
        encoding = "ascii"

    stream = AsciiStream()
    _write_captured_output("valid � 中文", stream)
    assert stream.getvalue() == "valid \\ufffd \\u4e2d\\u6587\n"


def test_atomic_report_handles_unicode_and_cleans_failed_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "有 空格" / "报告.json"
    write_json_atomic(path, {"message": "中文"})
    assert json.loads(path.read_text(encoding="utf-8"))["message"] == "中文"
    assert not list(path.parent.glob(".*.tmp"))

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("harness.report.os.replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        write_json_atomic(tmp_path / "failed.json", {"status": "failed"})
    assert not list(tmp_path.glob(".*.tmp"))
