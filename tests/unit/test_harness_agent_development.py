from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from harness.git_state import collect_git_state, evaluate_scope
from harness.runner import _build_parser, main
from harness.task_contract import ContractError, TaskContract, load_task_contract
from harness.task_runner import (
    EXIT_MANUAL_PENDING,
    EXIT_VALIDATION_FAILED,
    check_task,
    current_task,
    verify_task,
)
from harness.work_packages import WorkPackageError, load_work_packages


REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _work_packages(current: str = "WP-T-01", status: str = "active") -> str:
    return f"""---
kind: plan
status: active
audience: maintainer
source_of_truth: self
active_work_package: {current}
updated: 2026-08-08
---

# Work Packages

| Work Package | 主要结果 | 依赖 | 当前状态 |
|---|---|---|---|
| WP-D-01 | dependency | 无 | accepted |
| WP-T-01 | task | WP-D-01 | {status} |
| WP-N-01 | next | WP-T-01 | planned |
"""


def _manifest(failing_case: str | None = None) -> dict[str, object]:
    profiles = {
        "first": {"description": "first", "cases": ["a", "b"]},
        "second": {"description": "second", "cases": ["b", "c"]},
        "smoke": {"description": "smoke", "cases": ["a"]},
        "core-host": {"description": "core", "cases": ["a"]},
        "python-full": {"description": "full", "cases": ["a"]},
    }
    return {
        "schema_version": 1,
        "profiles": profiles,
        "cases": [
            {
                "id": case_id,
                "description": case_id,
                "argv": [
                    "{python}",
                    "-c",
                    f"print('{case_id}'); raise SystemExit({1 if failing_case == case_id else 0})",
                ],
                "timeout_seconds": 10,
            }
            for case_id in ("a", "b", "c")
        ],
    }


def _contract(
    base_ref: str,
    *,
    required_profiles: list[str] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 2,
        "id": "WP-T-01",
        "base_ref": base_ref,
        "allowed_paths": [
            "data/**",
            "docs/plans/runtime-v2/work-packages.md",
            "harness/**",
            "requirements-dev.txt",
            "src/**",
            "tests/**",
        ],
        "required_profiles": required_profiles or ["first", "second"],
    }


def _repo(
    tmp_path: Path,
    *,
    failing_case: str | None = None,
    required_profiles: list[str] | None = None,
    foreign_base: bool = False,
) -> Path:
    repo = tmp_path / "repo with 空格"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "tests@example.invalid")
    _git(repo, "config", "user.name", "Harness Tests")
    _write(repo / "docs/plans/runtime-v2/work-packages.md", _work_packages())
    _write(repo / "src/allowed.txt", "base\n")
    _write(repo / "tests/test_keep.py", "def test_keep(): pass\n")
    _write(repo / "harness/suites.json", json.dumps(_manifest(failing_case)))
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    base_ref = _git(repo, "rev-parse", "HEAD")
    if foreign_base:
        tree = _git(repo, "rev-parse", "HEAD^{tree}")
        base_ref = _git(repo, "commit-tree", tree, "-m", "foreign")
    _write(
        repo / "harness/tasks/WP-T-01.json",
        json.dumps(_contract(base_ref, required_profiles=required_profiles)),
    )
    _git(repo, "add", "harness/tasks/WP-T-01.json")
    _git(repo, "commit", "-m", "add task")
    return repo


def _load(repo: Path) -> TaskContract:
    return load_task_contract(
        repo / "harness/tasks/WP-T-01.json",
        repo_root=repo,
        manifest=_manifest(),
    )


def test_v2_contract_is_small_and_resolves_first_committed_base(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    contract = _load(repo)
    schema = json.loads(
        (REPO_ROOT / "harness/tasks/schema.json").read_text(encoding="utf-8")
    )

    assert contract.task_id == "WP-T-01"
    assert len(contract.base_sha) == 40
    assert len(contract.initial_task_sha) == 40
    assert contract.revision_fields == ()
    assert schema["properties"]["schema_version"] == {"const": 2}
    assert set(schema["required"]) == {
        "schema_version",
        "id",
        "base_ref",
        "allowed_paths",
        "required_profiles",
    }
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.pop("allowed_paths"), "CONTRACT_REQUIRED"),
        (lambda value: value.__setitem__("schema_version", 1), "historical only"),
        (lambda value: value.__setitem__("unknown", True), "CONTRACT_UNKNOWN"),
        (lambda value: value.__setitem__("base_ref", "HEAD"), "BASE_REF_FORMAT"),
        (lambda value: value.__setitem__("id", "WP-X-01"), "does not match"),
        (lambda value: value.__setitem__("allowed_paths", ["src/*.py"]), "CONTRACT_PATH"),
        (lambda value: value.__setitem__("allowed_paths", ["../src/**"]), "CONTRACT_PATH"),
        (lambda value: value.__setitem__("required_profiles", ["missing"]), "PROFILE_UNKNOWN"),
        (lambda value: value.__setitem__("required_profiles", ["first", "first"]), "DUPLICATE"),
        (
            lambda value: value.__setitem__("required_profiles", ["core-host", "python-full"]),
            "PROFILE_OVERLAP",
        ),
        (
            lambda value: value.__setitem__("required_profiles", ["smoke", "python-full"]),
            "PROFILE_OVERLAP",
        ),
    ],
)
def test_invalid_contracts_fail_closed(tmp_path: Path, mutation, message: str) -> None:
    repo = _repo(tmp_path)
    path = repo / "harness/tasks/WP-T-01.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    mutation(value)
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ContractError, match=message):
        _load(repo)


def test_base_ref_cannot_move_after_the_task_first_commit(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path = repo / "harness/tasks/WP-T-01.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["base_ref"] = _git(repo, "rev-parse", "HEAD")
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ContractError, match="CONTRACT_BASE_REF_MOVED"):
        _load(repo)


def test_non_ancestor_base_is_a_hard_check_failure(tmp_path: Path) -> None:
    repo = _repo(tmp_path, foreign_base=True)

    exit_code, report = check_task("WP-T-01", repo_root=repo)

    assert exit_code == EXIT_VALIDATION_FAILED
    assert report["status"] == "failed"
    base_check = next(item for item in report["checks"] if item["code"] == "CHECK_BASE_ANCESTOR")
    assert base_check["status"] == "failed"


def test_work_package_parser_returns_current_and_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "work-packages.md"
    _write(path, _work_packages())
    registry = load_work_packages(path)
    assert registry.current.task_id == "WP-T-01"
    assert registry.packages["WP-T-01"].dependencies == ("WP-D-01",)

    path.write_text(_work_packages(status="planned"), encoding="utf-8")
    with pytest.raises(WorkPackageError, match="WORK_PACKAGE_CURRENT_COUNT"):
        load_work_packages(path)

    path.write_text(_work_packages(current="WP-N-01"), encoding="utf-8")
    with pytest.raises(WorkPackageError, match="WORK_PACKAGE_METADATA_MISMATCH"):
        load_work_packages(path)


def test_scope_collects_all_four_git_origins(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    contract = _load(repo)
    _write(repo / "src/committed.txt", "committed\n")
    _git(repo, "add", "src/committed.txt")
    _git(repo, "commit", "-m", "committed change")
    _write(repo / "src/staged.txt", "staged\n")
    _git(repo, "add", "src/staged.txt")
    _write(repo / "src/allowed.txt", "unstaged\n")
    _write(repo / "src/未跟踪.txt", "untracked\n")

    result = evaluate_scope(collect_git_state(repo, contract.base_sha), contract)

    assert result.status == "passed"
    assert {change.origin for change in result.changes} == {
        "committed",
        "staged",
        "unstaged",
        "untracked",
    }
    assert "src/未跟踪.txt" in result.untracked_files


@pytest.mark.parametrize(
    ("path", "bucket"),
    [
        ("outside.txt", "out_of_scope_files"),
        ("data/secret.txt", "protected_files"),
    ],
)
def test_scope_rejects_outside_and_global_protected_paths(
    tmp_path: Path, path: str, bucket: str
) -> None:
    repo = _repo(tmp_path)
    contract = _load(repo)
    _write(repo / path, "change\n")

    result = evaluate_scope(collect_git_state(repo, contract.base_sha), contract)

    assert result.status == "failed"
    assert path in getattr(result, bucket)


def test_allowed_dependency_is_highlighted_and_tests_continue(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    contract = _load(repo)
    _write(repo / "requirements-dev.txt", "pytest\n")

    result = evaluate_scope(collect_git_state(repo, contract.base_sha), contract)
    exit_code, report = check_task("WP-T-01", repo_root=repo)

    assert result.status == "passed"
    assert result.dependency_files == ("requirements-dev.txt",)
    assert result.dependency_changes == (
        {"path": "requirements-dev.txt", "status": "allowed"},
    )
    assert exit_code == 0
    assert report["dependencies"]["status"] == "changed"


def test_deleted_or_renamed_test_is_rejected(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    contract = _load(repo)
    _git(repo, "mv", "tests/test_keep.py", "src/renamed.py")

    result = evaluate_scope(collect_git_state(repo, contract.base_sha), contract)

    assert result.deleted_tests == ("tests/test_keep.py",)
    assert {"tests/test_keep.py", "src/renamed.py"} <= set(result.changed_files)
    assert result.status == "failed"


def test_wp_h_02_anchor_is_the_only_activation_change_still_allowed(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    contract = _load(repo)
    _write(repo / "harness/activations/WP-N-01/0001.json", "{}\n")

    result = evaluate_scope(collect_git_state(repo, contract.base_sha), contract)

    assert result.retired_activation_files == (
        "harness/activations/WP-N-01/0001.json",
    )
    assert result.status == "failed"


def test_committed_task_revision_is_reported_without_a_ledger(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path = repo / "harness/tasks/WP-T-01.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["allowed_paths"].append("outside/**")
    value["required_profiles"] = ["second"]
    path.write_text(json.dumps(value), encoding="utf-8")
    _git(repo, "add", str(path.relative_to(repo)))
    _git(repo, "commit", "-m", "revise task")

    exit_code, report = check_task("WP-T-01", repo_root=repo)

    assert exit_code == 0
    assert report["status"] == "passed"
    assert report["contract"]["revision_fields"] == [
        "allowed_paths",
        "required_profiles",
    ]


@pytest.mark.parametrize("staged", [False, True])
def test_uncommitted_task_revision_requires_owner_review_and_skips_cases(
    tmp_path: Path, staged: bool
) -> None:
    repo = _repo(tmp_path)
    path = repo / "harness/tasks/WP-T-01.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["allowed_paths"].append("outside/**")
    path.write_text(json.dumps(value), encoding="utf-8")
    if staged:
        _git(repo, "add", str(path.relative_to(repo)))

    check_exit, check = check_task("WP-T-01", repo_root=repo)
    verify_exit, verify = verify_task(
        "WP-T-01", repo_root=repo, report_path=repo / "temp/review.json"
    )

    assert check_exit == EXIT_MANUAL_PENDING
    assert check["status"] == "owner_review_required"
    assert verify_exit == EXIT_MANUAL_PENDING
    assert verify["status"] == "owner_review_required"
    assert verify["cases"] == []
    assert {item["status"] for item in verify["acceptance"]["automated"]} == {"pending"}


def test_current_and_dependency_checks_use_the_work_package_table(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path = repo / "docs/plans/runtime-v2/work-packages.md"
    next_current = (
        _work_packages()
        .replace("active_work_package: WP-T-01", "active_work_package: WP-N-01")
        .replace("| WP-T-01 | task | WP-D-01 | active |", "| WP-T-01 | task | WP-D-01 | planned |")
        .replace("| WP-N-01 | next | WP-T-01 | planned |", "| WP-N-01 | next | WP-T-01 | active |")
    )
    path.write_text(next_current, encoding="utf-8")

    exit_code, report = check_task("WP-T-01", repo_root=repo)

    assert exit_code == EXIT_VALIDATION_FAILED
    assert report["status"] == "failed"
    assert any(item["code"] == "CHECK_CURRENT" and item["status"] == "failed" for item in report["checks"])

    path.write_text(_work_packages().replace("WP-D-01 | dependency | 无 | accepted", "WP-D-01 | dependency | 无 | planned"), encoding="utf-8")
    exit_code, report = check_task("WP-T-01", repo_root=repo)
    assert exit_code == EXIT_VALIDATION_FAILED
    assert report["dependencies"]["status"] == "failed"


def test_verify_runs_each_case_once_and_returns_manual_pending(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    report_path = repo / "temp/report.json"

    exit_code, report = verify_task(
        "WP-T-01", repo_root=repo, report_path=report_path
    )

    assert exit_code == EXIT_MANUAL_PENDING
    assert report["schema_version"] == 2
    assert report["status"] == "manual_pending"
    assert [item["id"] for item in report["cases"]] == ["a", "b", "c"]
    assert [item["case_id"] for item in report["acceptance"]["automated"]] == [
        "a",
        "b",
        "c",
    ]
    assert all(item["status"] == "passed" for item in report["profiles"])
    assert report["acceptance"]["manual"] == {
        "status": "pending",
        "source": "corresponding normative Spec",
    }
    assert Path(report["runtime_tmp_root"]).is_dir()
    assert json.loads(report_path.read_text(encoding="utf-8"))["status"] == "manual_pending"


def test_verify_stops_at_first_failure_and_propagates_shared_case(tmp_path: Path) -> None:
    repo = _repo(tmp_path, failing_case="b")

    exit_code, report = verify_task(
        "WP-T-01", repo_root=repo, report_path=repo / "temp/failed.json"
    )

    assert exit_code == EXIT_VALIDATION_FAILED
    assert report["status"] == "failed"
    assert [item["id"] for item in report["cases"]] == ["a", "b"]
    assert [item["status"] for item in report["profiles"]] == ["failed", "failed"]
    automated = {item["case_id"]: item["status"] for item in report["acceptance"]["automated"]}
    assert automated == {"a": "passed", "b": "failed", "c": "blocked"}
    assert report["acceptance"]["manual"]["status"] == "blocked"


def test_verify_skips_all_cases_after_a_hard_scope_failure(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write(repo / "outside.txt", "not allowed\n")

    exit_code, report = verify_task(
        "WP-T-01", repo_root=repo, report_path=repo / "temp/scope-failed.json"
    )

    assert exit_code == EXIT_VALIDATION_FAILED
    assert report["status"] == "failed"
    assert report["cases"] == []
    assert {item["status"] for item in report["acceptance"]["automated"]} == {
        "blocked"
    }


def test_current_check_verify_active_and_removed_preflight_cli(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert current_task(repo_root=repo)["schema_version"] == 2
    for command in ("check", "verify"):
        args = _build_parser().parse_args([command, "--active"])
        assert args.active is True
        assert args.task is None

    with pytest.raises(SystemExit) as error:
        _build_parser().parse_args(["preflight", "--active"])
    assert error.value.code == 2
    assert main(["check"]) == 2
