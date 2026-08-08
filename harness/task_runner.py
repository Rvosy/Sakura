"""Task-level scope checks and de-duplicated verification orchestration."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .git_state import collect_git_state, evaluate_scope, is_ancestor
from .report import write_json_atomic
from .runner import (
    DEFAULT_MANIFEST,
    REPO_ROOT,
    create_runtime_tmp_root,
    execute_cases,
    expand_profiles,
    load_manifest,
)
from .task_contract import TaskContract, load_task_contract
from .work_packages import WorkPackageRegistry, load_work_packages


EXIT_PASSED = 0
EXIT_VALIDATION_FAILED = 1
EXIT_INVOCATION_ERROR = 2
EXIT_MANUAL_PENDING = 3
DEFAULT_WORK_PACKAGES = REPO_ROOT / "docs" / "plans" / "runtime-v2" / "work-packages.md"


def _paths(repo_root: Path) -> tuple[Path, Path]:
    return (
        repo_root / "harness" / "tasks",
        repo_root / "docs" / "plans" / "runtime-v2" / "work-packages.md",
    )


def _load(
    task_id: str,
    repo_root: Path,
    manifest_path: Path | None,
) -> tuple[dict[str, Any], WorkPackageRegistry, TaskContract]:
    manifest = load_manifest(manifest_path or repo_root / "harness" / "suites.json")
    tasks_root, work_packages_path = _paths(repo_root)
    registry = load_work_packages(work_packages_path)
    contract = load_task_contract(
        tasks_root / f"{task_id}.json", repo_root=repo_root, manifest=manifest
    )
    return manifest, registry, contract


def current_task(*, repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    registry = load_work_packages(_paths(repo_root)[1])
    return {
        "schema_version": 2,
        "task": registry.current.task_id,
        "status": registry.current.status,
        "status_source": "docs/plans/runtime-v2/work-packages.md",
    }


def _check_loaded(
    task_id: str,
    *,
    repo_root: Path,
    registry: WorkPackageRegistry,
    contract: TaskContract,
) -> tuple[int, dict[str, Any]]:
    state = collect_git_state(repo_root, contract.base_sha)
    scope = evaluate_scope(state, contract)
    package = registry.packages.get(task_id)
    checks: list[dict[str, str]] = []

    def add(code: str, status: str, message: str) -> None:
        checks.append({"code": code, "status": status, "message": message})

    current = package is not None and registry.current.task_id == task_id
    add(
        "CHECK_CURRENT",
        "passed" if current else "failed",
        f"current Work Package is {registry.current.task_id}",
    )
    dependency_items: list[dict[str, str]] = []
    for dependency in package.dependencies if package else ():
        dependency_package = registry.packages.get(dependency)
        status = dependency_package.status if dependency_package else "missing"
        accepted = status == "accepted"
        dependency_items.append(
            {
                "id": dependency,
                "status": status,
                "result": "passed" if accepted else "failed",
            }
        )
        add(
            "CHECK_DEPENDENCY",
            "passed" if accepted else "failed",
            f"dependency {dependency} is {status}",
        )
    ancestor = is_ancestor(repo_root, contract.base_sha)
    add(
        "CHECK_BASE_ANCESTOR",
        "passed" if ancestor else "failed",
        "base_ref is an ancestor of HEAD" if ancestor else "base_ref is not an ancestor of HEAD",
    )
    add(
        "CHECK_ALLOWED_PATHS",
        "passed" if not scope.out_of_scope_files else "failed",
        "out-of-scope files: "
        + (", ".join(scope.out_of_scope_files) if scope.out_of_scope_files else "none"),
    )
    add(
        "CHECK_GLOBAL_PROTECTED",
        "passed" if not scope.protected_files else "failed",
        "protected files: "
        + (", ".join(scope.protected_files) if scope.protected_files else "none"),
    )
    add(
        "CHECK_ACTIVATION_RETIRED",
        "passed" if not scope.retired_activation_files else "failed",
        "new or modified activation files: "
        + (
            ", ".join(scope.retired_activation_files)
            if scope.retired_activation_files
            else "none"
        ),
    )
    add(
        "CHECK_TEST_DELETION",
        "passed" if not scope.deleted_tests else "failed",
        "deleted tests: "
        + (", ".join(scope.deleted_tests) if scope.deleted_tests else "none"),
    )
    add(
        "CHECK_TASK_REVISION",
        "pending" if scope.owner_review_files else "passed",
        "uncommitted task files: "
        + (", ".join(scope.owner_review_files) if scope.owner_review_files else "none"),
    )
    hard_failure = any(item["status"] == "failed" for item in checks)
    pending = bool(scope.owner_review_files) and not hard_failure
    status = "failed" if hard_failure else "owner_review_required" if pending else "passed"
    dependency_changes = list(scope.dependency_changes)
    report = {
        "schema_version": 2,
        "command": "check",
        "task": task_id,
        "status": status,
        "base_ref": state.base_sha,
        "head_ref": "WORKTREE" if state.has_worktree_changes else state.head_sha,
        "checks": checks,
        "contract": {
            "initial_task_sha": contract.initial_task_sha,
            "revision_fields": list(contract.revision_fields),
            "owner_review_required": bool(scope.owner_review_files),
        },
        "scope": scope.as_dict(),
        "dependencies": {
            "status": (
                "failed"
                if any(item["result"] == "failed" for item in dependency_items)
                else "changed"
                if dependency_changes
                else "passed"
            ),
            "items": dependency_items,
            "changes": dependency_changes,
        },
    }
    exit_code = (
        EXIT_VALIDATION_FAILED
        if hard_failure
        else EXIT_MANUAL_PENDING
        if pending
        else EXIT_PASSED
    )
    return exit_code, report


def check_task(
    task_id: str,
    *,
    repo_root: Path = REPO_ROOT,
    manifest_path: Path | None = None,
) -> tuple[int, dict[str, Any]]:
    _, registry, contract = _load(task_id, repo_root, manifest_path)
    return _check_loaded(
        task_id, repo_root=repo_root, registry=registry, contract=contract
    )


def _default_task_report(repo_root: Path, task_id: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    return repo_root / "temp" / "harness" / f"{stamp}-{task_id}.json"


def _profile_reports(
    profile_cases: dict[str, tuple[str, ...]],
    result_by_id: dict[str, dict[str, Any]],
    *,
    blocked_status: str,
) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for profile, case_ids in profile_cases.items():
        statuses = [result_by_id[case_id]["status"] for case_id in case_ids if case_id in result_by_id]
        if "failed" in statuses:
            status = "failed"
        elif len(statuses) == len(case_ids) and all(item == "passed" for item in statuses):
            status = "passed"
        else:
            status = blocked_status
        reports.append(
            {
                "profile": profile,
                "status": status,
                "case_ids": list(case_ids),
                "summary": {
                    "total": len(case_ids),
                    "passed": statuses.count("passed"),
                    "failed": statuses.count("failed"),
                    "blocked": len(case_ids) - len(statuses),
                },
            }
        )
    return reports


def verify_task(
    task_id: str,
    *,
    repo_root: Path = REPO_ROOT,
    manifest_path: Path | None = None,
    report_path: Path | None = None,
) -> tuple[int, dict[str, Any]]:
    started_at = datetime.now(UTC)
    started_clock = time.monotonic()
    manifest, registry, contract = _load(task_id, repo_root, manifest_path)
    check_exit, check = _check_loaded(
        task_id, repo_root=repo_root, registry=registry, contract=contract
    )
    profile_cases, cases = expand_profiles(manifest, contract.required_profiles)
    runtime_tmp_root = create_runtime_tmp_root(repo_root)
    results: list[dict[str, Any]] = []
    if check_exit == EXIT_PASSED:
        results = execute_cases(
            cases,
            repo_root=repo_root,
            runtime_tmp_root=runtime_tmp_root,
            stop_on_failure=True,
        )
    result_by_id = {result["id"]: result for result in results}
    case_failed = any(result["status"] == "failed" for result in results)
    if check_exit == EXIT_VALIDATION_FAILED or case_failed:
        exit_code = EXIT_VALIDATION_FAILED
        status = "failed"
        unexecuted_status = "blocked"
    elif check_exit == EXIT_MANUAL_PENDING:
        exit_code = EXIT_MANUAL_PENDING
        status = "owner_review_required"
        unexecuted_status = "pending"
    else:
        exit_code = EXIT_MANUAL_PENDING
        status = "manual_pending"
        unexecuted_status = "blocked"

    profiles = _profile_reports(
        profile_cases, result_by_id, blocked_status=unexecuted_status
    )
    automated = [
        {
            "case_id": case.case_id,
            "status": result_by_id.get(case.case_id, {}).get(
                "status", unexecuted_status
            ),
        }
        for case in cases
    ]
    finished_at = datetime.now(UTC)
    report = {
        "schema_version": 2,
        "command": "verify",
        "task": task_id,
        "status": status,
        "base_ref": check["base_ref"],
        "head_ref": check["head_ref"],
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": round(time.monotonic() - started_clock, 3),
        "runtime_tmp_root": str(runtime_tmp_root),
        "contract": check["contract"],
        "scope": check["scope"],
        "dependencies": check["dependencies"],
        "checks": check["checks"],
        "profiles": profiles,
        "cases": results,
        "acceptance": {
            "automated": automated,
            "manual": {
                "status": "blocked" if status == "failed" else "pending",
                "source": "corresponding normative Spec",
            },
        },
        "summary": {
            "total": len(cases),
            "passed": sum(item["status"] == "passed" for item in automated),
            "failed": sum(item["status"] == "failed" for item in automated),
            "pending": sum(item["status"] == "pending" for item in automated),
            "blocked": sum(item["status"] == "blocked" for item in automated),
        },
    }
    write_json_atomic(report_path or _default_task_report(repo_root, task_id), report)
    return exit_code, report
