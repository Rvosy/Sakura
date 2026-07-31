from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from harness.git_state import collect_git_state, evaluate_scope
from harness.report import write_json_atomic
from harness.runner import _build_parser
from harness.task_contract import ContractError, TaskContract, load_task_contract
from harness.task_runner import (
    EXIT_MANUAL_PENDING,
    EXIT_VALIDATION_FAILED,
    check_task,
    preflight_task,
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
updated: 2026-07-31
---

# Work Packages

| Work Package | 主要结果 | 依赖 | 当前状态 |
|---|---|---|---|
| WP-D-01 | dependency | 无 | accepted |
| WP-T-01 | task | WP-D-01 | {status} |
| WP-N-01 | next | WP-T-01 | planned |
"""


def _manifest(exit_code: int = 0) -> dict[str, object]:
    return {
        "schema_version": 1,
        "profiles": {
            "task": {"description": "task profile", "cases": ["task-case"]}
        },
        "cases": [
            {
                "id": "task-case",
                "description": "fixture",
                "argv": [
                    "{python}",
                    "-c",
                    f"print('task-profile'); raise SystemExit({exit_code})",
                ],
                "timeout_seconds": 10,
            }
        ],
    }


def _contract(base_ref: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "id": "WP-T-01",
        "title": "测试任务",
        "status_source": "docs/plans/runtime-v2/work-packages.md",
        "documents": {
            "specs": ["docs/specs/task.md"],
            "adrs": ["docs/adr/0001-task.md"],
            "plans": ["docs/plans/task.md"],
        },
        "dependencies": ["WP-D-01"],
        "base_ref": base_ref,
        "allowed_paths": [
            "docs/plans/runtime-v2/work-packages.md",
            "harness/activations/WP-T-01/0001.json",
            "harness/tasks/WP-T-01.json",
            "src/**",
            "tests/**",
        ],
        "forbidden_paths": ["forbidden/**"],
        "protected_paths": ["data/**", "characters/**", "third_party/**"],
        "dependency_policy": {"mode": "forbidden", "allowed_files": []},
        "required_profiles": ["task"],
        "acceptance": {"automated": ["profile passes"], "manual": []},
        "rollback": ["revert task"],
    }


def _repo(
    tmp_path: Path, *, profile_exit_code: int = 0, manual_acceptance: bool = False
) -> Path:
    repo = tmp_path / "repo with 空格"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "tests@example.invalid")
    _git(repo, "config", "user.name", "Harness Tests")
    _write(repo / "docs/plans/runtime-v2/work-packages.md", _work_packages())
    _write(repo / "docs/specs/task.md", "spec\n")
    _write(repo / "docs/adr/0001-task.md", "adr\n")
    _write(repo / "docs/plans/task.md", "plan\n")
    _write(repo / "src/allowed.txt", "base\n")
    _write(repo / "tests/test_keep.py", "def test_keep(): pass\n")
    _write(repo / "harness/suites.json", json.dumps(_manifest(profile_exit_code)))
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    base_ref = _git(repo, "rev-parse", "HEAD")
    contract = _contract(base_ref)
    if manual_acceptance:
        contract["acceptance"]["manual"] = ["负责人验收"]
    _write(repo / "harness/tasks/WP-T-01.json", json.dumps(contract))
    _write(
        repo / "harness/activations/WP-T-01/0001.json",
        json.dumps(
            {
                "schema_version": 1,
                "sequence": 1,
                "task_id": "WP-T-01",
                "kind": "activation",
                "base_ref": base_ref,
                "supersedes": None,
            }
        ),
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "activate task")
    return repo


def _load(repo: Path) -> TaskContract:
    return load_task_contract(
        repo / "harness/tasks/WP-T-01.json",
        repo_root=repo,
        manifest=_manifest(),
    )


def test_valid_contract_loads_and_resolves_base_ref(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    contract = _load(repo)

    assert contract.task_id == "WP-T-01"
    assert len(contract.base_sha) == 40
    assert len(contract.activation_sha) == 40
    assert contract.activation_path == "harness/activations/WP-T-01/0001.json"
    assert contract.required_profiles == ("task",)


@pytest.mark.parametrize("command", ["preflight", "check", "verify"])
def test_task_commands_accept_active_selector(command: str) -> None:
    args = _build_parser().parse_args([command, "--active"])

    assert args.active is True
    assert args.task is None


def test_repository_task_schema_is_strict_v1() -> None:
    schema = json.loads(
        (REPO_ROOT / "harness/tasks/schema.json").read_text(encoding="utf-8")
    )

    assert schema["properties"]["schema_version"] == {"const": 1}
    assert schema["properties"]["base_ref"] == {
        "type": "string",
        "pattern": "^[0-9a-fA-F]{40}$",
    }
    assert schema["additionalProperties"] is False
    assert schema["properties"]["documents"]["additionalProperties"] is False
    document_schema = schema["properties"]["documents"]
    assert document_schema["properties"]["specs"] == {"$ref": "#/$defs/uniqueStrings"}
    assert len(document_schema["anyOf"]) == 3


def test_contract_allows_empty_document_categories_but_not_all_empty(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    path = repo / "harness/tasks/WP-T-01.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["documents"]["adrs"] = []
    value["documents"]["plans"] = []
    path.write_text(json.dumps(value), encoding="utf-8")

    contract = _load(repo)

    assert contract.documents["specs"] == ("docs/specs/task.md",)
    assert contract.documents["adrs"] == ()
    assert contract.documents["plans"] == ()

    value["documents"]["specs"] = []
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ContractError, match="CONTRACT_DOCUMENTS_EMPTY"):
        _load(repo)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.pop("title"), "CONTRACT_REQUIRED"),
        (lambda value: value.__setitem__("schema_version", 2), "CONTRACT_SCHEMA"),
        (lambda value: value.__setitem__("unknown", True), "CONTRACT_UNKNOWN"),
        (
            lambda value: value.__setitem__("allowed_paths", ["src/**", "src/**"]),
            "CONTRACT_DUPLICATE_PATH",
        ),
        (
            lambda value: value.__setitem__("forbidden_paths", ["src/**"]),
            "CONTRACT_PATH_CONFLICT",
        ),
        (
            lambda value: value["acceptance"].__setitem__("automated", []),
            "CONTRACT_ACCEPTANCE_EMPTY",
        ),
        (
            lambda value: value.__setitem__("base_ref", "missing-ref"),
            "CONTRACT_BASE_REF_FORMAT",
        ),
        (
            lambda value: value.__setitem__("allowed_paths", ["src/*.py"]),
            "CONTRACT_PATH_PATTERN",
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


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("specs", ["docs/specs/missing.md"], "CONTRACT_DOCUMENT_MISSING"),
        ("profiles", ["missing"], "CONTRACT_PROFILE_UNKNOWN"),
    ],
)
def test_contract_references_must_exist(
    tmp_path: Path, field: str, replacement: list[str], message: str
) -> None:
    repo = _repo(tmp_path)
    path = repo / "harness/tasks/WP-T-01.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if field == "profiles":
        value["required_profiles"] = replacement
    else:
        value["documents"][field] = replacement
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ContractError, match=message):
        _load(repo)


def test_work_package_parser_returns_current_and_dependencies(tmp_path: Path) -> None:
    path = tmp_path / "work-packages.md"
    _write(path, _work_packages())

    registry = load_work_packages(path)

    assert registry.current.task_id == "WP-T-01"
    assert registry.current.status == "active"
    assert registry.packages["WP-T-01"].dependencies == ("WP-D-01",)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (_work_packages(status="planned"), "WORK_PACKAGE_CURRENT_COUNT"),
        (
            _work_packages()
            + "\n| WP-X-01 | duplicate current | 无 | stabilizing |\n",
            "WORK_PACKAGE_CURRENT_COUNT",
        ),
        (
            _work_packages(current="WP-N-01"),
            "WORK_PACKAGE_METADATA_MISMATCH",
        ),
    ],
)
def test_work_package_parser_fails_closed(
    tmp_path: Path, text: str, message: str
) -> None:
    path = tmp_path / "work-packages.md"
    _write(path, text)

    with pytest.raises(WorkPackageError, match=message):
        load_work_packages(path)


def test_scope_accepts_allowed_staged_unstaged_untracked_and_committed(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    contract = _load(repo)
    _write(repo / "src/committed.txt", "committed\n")
    _git(repo, "add", "src/committed.txt")
    _git(repo, "commit", "-m", "allowed commit")
    _write(repo / "src/staged.txt", "staged\n")
    _git(repo, "add", "src/staged.txt")
    _write(repo / "src/allowed.txt", "unstaged\n")
    _write(repo / "src/未跟踪.txt", "untracked\n")

    result = evaluate_scope(
        collect_git_state(repo, contract.base_sha), contract, repo_root=repo
    )

    assert result.status == "passed"
    assert "src/committed.txt" in result.changed_files
    assert "src/staged.txt" in result.changed_files
    assert "src/allowed.txt" in result.changed_files
    assert "src/未跟踪.txt" in result.untracked_files


@pytest.mark.parametrize(
    ("path", "bucket"),
    [
        ("outside.txt", "out_of_scope_files"),
        ("forbidden/value.txt", "forbidden_files"),
        ("data/value.txt", "protected_files"),
        ("requirements-dev.txt", "dependency_files"),
    ],
)
def test_scope_rejects_outside_forbidden_protected_and_dependencies(
    tmp_path: Path, path: str, bucket: str
) -> None:
    repo = _repo(tmp_path)
    contract = _load(repo)
    _write(repo / path, "change\n")

    result = evaluate_scope(collect_git_state(repo, contract.base_sha), contract, repo_root=repo)

    assert result.status == "failed"
    assert path in getattr(result, bucket)


def test_scope_detects_deleted_tests_and_renames(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    contract = _load(repo)
    _git(repo, "mv", "tests/test_keep.py", "src/renamed.py")

    result = evaluate_scope(collect_git_state(repo, contract.base_sha), contract, repo_root=repo)

    assert result.deleted_tests == ("tests/test_keep.py",)
    assert {"tests/test_keep.py", "src/renamed.py"} <= set(result.changed_files)
    assert result.status == "failed"


def test_scope_detects_contract_boundary_changes_as_owner_review_required(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    path = repo / "harness/tasks/WP-T-01.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["allowed_paths"].append("outside/**")
    path.write_text(json.dumps(value), encoding="utf-8")
    contract = _load(repo)
    result = evaluate_scope(collect_git_state(repo, contract.base_sha), contract, repo_root=repo)
    assert result.contract_files == ("harness/tasks/WP-T-01.json",)
    assert result.owner_review_files == ("harness/tasks/WP-T-01.json",)
    assert result.status == "owner_review_required"


def test_base_ref_cannot_be_head_current_sha_or_later_sha(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path = repo / "harness/tasks/WP-T-01.json"
    original = json.loads(path.read_text(encoding="utf-8"))

    for replacement, message in (
        ("HEAD", "CONTRACT_BASE_REF_FORMAT"),
        (_git(repo, "rev-parse", "HEAD"), "CONTRACT_ACTIVATION_BASE_REF"),
    ):
        value = dict(original)
        value["base_ref"] = replacement
        path.write_text(json.dumps(value), encoding="utf-8")
        with pytest.raises(ContractError, match=message):
            _load(repo)

    path.write_text(json.dumps(original), encoding="utf-8")
    _write(repo / "src/later.txt", "later\n")
    _git(repo, "add", "src/later.txt")
    _git(repo, "commit", "-m", "later implementation")
    value = dict(original)
    value["base_ref"] = _git(repo, "rev-parse", "HEAD")
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ContractError, match="CONTRACT_ACTIVATION_BASE_REF"):
        _load(repo)


def test_activation_anchor_cannot_share_commit_with_implementation(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    contract = _load(repo)
    _write(
        repo / "harness/activations/WP-T-01/0002.json",
        json.dumps(
            {
                "schema_version": 1,
                "sequence": 2,
                "task_id": "WP-T-01",
                "kind": "contract_revision",
                "base_ref": contract.base_ref,
                "supersedes": "0001",
            }
        ),
    )
    _write(repo / "src/hidden.txt", "implementation\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "invalid mixed anchor")

    with pytest.raises(ContractError, match="CONTRACT_ACTIVATION_SCOPE"):
        _load(repo)


def test_committed_activation_anchor_is_immutable(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path = repo / "harness/activations/WP-T-01/0001.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["kind"] = "contract_revision"
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ContractError, match="CONTRACT_ACTIVATION_FROZEN"):
        _load(repo)


def test_status_source_change_requires_owner_review(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    contract = _load(repo)
    status_source = repo / contract.status_source
    status_source.write_text(
        status_source.read_text(encoding="utf-8") + "\n<!-- lifecycle edit -->\n",
        encoding="utf-8",
    )

    result = evaluate_scope(
        collect_git_state(repo, contract.base_sha), contract, repo_root=repo
    )

    assert result.status == "owner_review_required"
    assert result.owner_review_files == (contract.status_source,)

    preflight_exit, preflight = preflight_task("WP-T-01", repo_root=repo)
    check_exit, check = check_task("WP-T-01", repo_root=repo)
    assert preflight_exit == EXIT_MANUAL_PENDING
    assert preflight["status"] == "owner_review_required"
    assert check_exit == EXIT_MANUAL_PENDING
    assert check["status"] == "owner_review_required"

    verify_exit, verify = verify_task(
        "WP-T-01", repo_root=repo, report_path=repo / "temp/owner-review.json"
    )
    assert verify_exit == EXIT_MANUAL_PENDING
    assert verify["status"] == "owner_review_required"
    assert verify["profiles"] == []
    assert verify["acceptance"]["automated"][0]["status"] == "pending"


def test_scope_ignores_checkout_line_endings_for_frozen_documents(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    contract = _load(repo)
    for references in contract.documents.values():
        for reference in references:
            path = repo / reference
            lf_content = path.read_bytes().replace(b"\r\n", b"\n")
            path.write_bytes(lf_content.replace(b"\n", b"\r\n"))

    result = evaluate_scope(
        collect_git_state(repo, contract.base_sha), contract, repo_root=repo
    )

    assert result.contract_files == ()


def test_preflight_rejects_unaccepted_dependency(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    work_packages = repo / "docs/plans/runtime-v2/work-packages.md"
    work_packages.write_text(
        _work_packages().replace(
            "| WP-D-01 | dependency | 无 | accepted |",
            "| WP-D-01 | dependency | 无 | planned |",
        ),
        encoding="utf-8",
    )

    exit_code, report = preflight_task("WP-T-01", repo_root=repo)

    assert exit_code == EXIT_VALIDATION_FAILED
    assert any(check["code"] == "PREFLIGHT_DEPENDENCY" for check in report["checks"])


def test_check_fails_for_out_of_scope_change(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write(repo / "outside.txt", "change\n")

    exit_code, report = check_task("WP-T-01", repo_root=repo)

    assert exit_code == EXIT_VALIDATION_FAILED
    assert report["scope"]["out_of_scope_files"] == ["outside.txt"]


def test_verify_propagates_profile_failure_and_writes_report(tmp_path: Path) -> None:
    repo = _repo(tmp_path, profile_exit_code=9)
    report_path = repo / "temp/report.json"

    exit_code, report = verify_task(
        "WP-T-01", repo_root=repo, report_path=report_path
    )

    assert exit_code == EXIT_VALIDATION_FAILED
    assert report["profiles"][0]["status"] == "failed"
    assert json.loads(report_path.read_text(encoding="utf-8"))["status"] == "failed"


def test_verify_preflight_failure_skips_expensive_profiles(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write(repo / "outside.txt", "change\n")
    report_path = repo / "temp/report.json"

    exit_code, report = verify_task(
        "WP-T-01", repo_root=repo, report_path=report_path
    )

    assert exit_code == EXIT_VALIDATION_FAILED
    assert report["profiles"] == []
    assert report["acceptance"]["automated"][0]["status"] == "blocked"
    assert report_path.is_file()


def test_verify_success_and_manual_pending_exit_codes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    report_path = repo / "temp/报告.json"

    exit_code, report = verify_task(
        "WP-T-01", repo_root=repo, report_path=report_path
    )
    assert exit_code == 0
    assert report["status"] == "passed"
    assert len(report["base_ref"]) == 40

    repo = _repo(tmp_path / "manual case", manual_acceptance=True)
    report_path = repo / "temp/报告.json"
    exit_code, report = verify_task(
        "WP-T-01", repo_root=repo, report_path=report_path
    )
    assert exit_code == EXIT_MANUAL_PENDING
    assert report["status"] == "manual_pending"


def test_atomic_report_handles_unicode_and_leaves_no_temporary_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "有 空格" / "报告.json"

    write_json_atomic(path, {"message": "中文", "argv": [sys.executable]})

    assert json.loads(path.read_text(encoding="utf-8"))["message"] == "中文"
    assert not list(path.parent.glob(".*.tmp"))


def test_atomic_report_cleans_temporary_file_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "report.json"

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("harness.report.os.replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        write_json_atomic(path, {"status": "failed"})

    assert not list(tmp_path.glob(".*.tmp"))
