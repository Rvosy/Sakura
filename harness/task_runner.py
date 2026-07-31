"""Task-level preflight, scope checks, verification and unified reporting."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .git_state import collect_git_state, evaluate_scope, is_ancestor
from .report import write_json_atomic
from .runner import DEFAULT_MANIFEST, REPO_ROOT, load_manifest, run_profile
from .task_contract import TaskContract, load_task_contract
from .work_packages import WorkPackageRegistry, load_work_packages


EXIT_PASSED = 0
EXIT_VALIDATION_FAILED = 1
EXIT_INVOCATION_ERROR = 2
EXIT_MANUAL_PENDING = 3
DEFAULT_TASKS_ROOT = REPO_ROOT / "harness" / "tasks"
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
        "schema_version": 1,
        "task": registry.current.task_id,
        "status": registry.current.status,
        "status_source": "docs/plans/runtime-v2/work-packages.md",
    }


def preflight_task(
    task_id: str,
    *,
    repo_root: Path = REPO_ROOT,
    manifest_path: Path | None = None,
) -> tuple[int, dict[str, Any]]:
    _, registry, contract = _load(task_id, repo_root, manifest_path)
    checks: list[dict[str, str]] = []

    def add(code: str, passed: bool, message: str) -> None:
        checks.append(
            {"code": code, "status": "passed" if passed else "failed", "message": message}
        )

    package = registry.packages.get(task_id)
    add("PREFLIGHT_TASK_EXISTS", package is not None, f"Work Package {task_id} exists")
    add(
        "PREFLIGHT_CONTRACT_ID",
        contract.task_id == task_id,
        f"contract id is {contract.task_id}",
    )
    is_current = package is not None and registry.current.task_id == task_id
    add("PREFLIGHT_CURRENT", is_current, f"current Work Package is {registry.current.task_id}")
    if package is not None:
        dependency_mismatch = tuple(package.dependencies) != contract.dependencies
        add(
            "PREFLIGHT_DEPENDENCY_CONTRACT",
            not dependency_mismatch,
            "contract dependencies match the Work Package table",
        )
    for dependency in contract.dependencies:
        dependency_package = registry.packages.get(dependency)
        accepted = dependency_package is not None and dependency_package.status == "accepted"
        add(
            "PREFLIGHT_DEPENDENCY",
            accepted,
            f"dependency {dependency} is "
            + (dependency_package.status if dependency_package else "missing"),
        )
    state = collect_git_state(repo_root, contract.base_sha)
    add(
        "PREFLIGHT_BASE_ANCESTOR",
        is_ancestor(repo_root, contract.base_sha),
        "base_ref is an ancestor of HEAD",
    )
    scope = evaluate_scope(state, contract, repo_root=repo_root)
    scope_checks = (
        ("PREFLIGHT_ALLOWED_PATHS", scope.out_of_scope_files, "out-of-scope files"),
        ("PREFLIGHT_FORBIDDEN_PATHS", scope.forbidden_files, "forbidden files"),
        ("PREFLIGHT_PROTECTED_PATHS", scope.protected_files, "protected files"),
        ("PREFLIGHT_DEPENDENCY_FILES", scope.dependency_files, "dependency files"),
        ("PREFLIGHT_TEST_DELETION", scope.deleted_tests, "deleted tests"),
        ("PREFLIGHT_CONTRACT_FROZEN", scope.contract_files, "frozen contract files"),
    )
    for code, values, label in scope_checks:
        add(
            code,
            not values,
            f"{label}: " + (", ".join(values) if values else "none"),
        )
    failed = any(check["status"] == "failed" for check in checks)
    return (
        EXIT_VALIDATION_FAILED if failed else EXIT_PASSED,
        {
            "status": "failed" if failed else "passed",
            "checks": checks,
            "base_ref": contract.base_sha,
        },
    )


def check_task(
    task_id: str,
    *,
    repo_root: Path = REPO_ROOT,
    manifest_path: Path | None = None,
) -> tuple[int, dict[str, Any]]:
    _, _, contract = _load(task_id, repo_root, manifest_path)
    state = collect_git_state(repo_root, contract.base_sha)
    scope = evaluate_scope(state, contract, repo_root=repo_root)
    report = {
        "schema_version": 1,
        "command": "check",
        "task": task_id,
        "status": scope.status,
        "base_ref": state.base_sha,
        "head_ref": "WORKTREE" if state.has_worktree_changes else state.head_sha,
        "scope": scope.as_dict(),
        "dependencies": {
            "status": "failed" if scope.dependency_files else "passed",
            "changes": list(scope.dependency_changes),
        },
    }
    return (
        EXIT_PASSED if scope.status == "passed" else EXIT_VALIDATION_FAILED,
        report,
    )


def _default_task_report(repo_root: Path, task_id: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return repo_root / "temp" / "harness" / f"{stamp}-{task_id}.json"


def verify_task(
    task_id: str,
    *,
    repo_root: Path = REPO_ROOT,
    manifest_path: Path | None = None,
    report_path: Path | None = None,
) -> tuple[int, dict[str, Any]]:
    started_at = datetime.now(UTC)
    started_clock = time.monotonic()
    manifest, _, contract = _load(task_id, repo_root, manifest_path)
    state = collect_git_state(repo_root, contract.base_sha)
    preflight_exit, preflight = preflight_task(
        task_id, repo_root=repo_root, manifest_path=manifest_path
    )
    scope = evaluate_scope(state, contract, repo_root=repo_root)
    destination = report_path or _default_task_report(repo_root, task_id)
    profiles: list[dict[str, Any]] = []
    automated: list[dict[str, str]] = []
    manual = [
        {"description": description, "status": "pending"}
        for description in contract.manual_acceptance
    ]

    if preflight_exit == EXIT_PASSED and scope.status == "passed":
        for profile_name in contract.required_profiles:
            profile_report_path = destination.parent / f".{destination.stem}-{profile_name}.json"
            exit_code, profile_report, _ = run_profile(
                manifest, profile_name, report_path=profile_report_path
            )
            profiles.append(
                {
                    "profile": profile_name,
                    "status": profile_report["status"],
                    "exit_code": exit_code,
                    "summary": profile_report["summary"],
                }
            )
            if exit_code != 0:
                break
        profiles_passed = len(profiles) == len(contract.required_profiles) and all(
            profile["status"] == "passed" for profile in profiles
        )
        automated = [
            {
                "description": description,
                "status": "passed" if profiles_passed else "failed",
            }
            for description in contract.automated_acceptance
        ]
    else:
        profiles_passed = False
        automated = [
            {"description": description, "status": "blocked"}
            for description in contract.automated_acceptance
        ]

    automatic_failure = (
        preflight_exit != EXIT_PASSED
        or scope.status != "passed"
        or not profiles_passed
    )
    if automatic_failure:
        exit_code = EXIT_VALIDATION_FAILED
        status = "failed"
    elif manual:
        exit_code = EXIT_MANUAL_PENDING
        status = "manual_pending"
    else:
        exit_code = EXIT_PASSED
        status = "passed"
    finished_at = datetime.now(UTC)
    statuses = [item["status"] for item in preflight["checks"]]
    statuses.extend(profile["status"] for profile in profiles)
    statuses.extend(item["status"] for item in automated)
    statuses.extend(item["status"] for item in manual)
    report = {
        "schema_version": 1,
        "command": "verify",
        "task": task_id,
        "status": status,
        "base_ref": state.base_sha,
        "head_ref": "WORKTREE" if state.has_worktree_changes else state.head_sha,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": round(time.monotonic() - started_clock, 3),
        "preflight": preflight,
        "scope": scope.as_dict(),
        "dependencies": {
            "status": "failed" if scope.dependency_files else "passed",
            "changes": list(scope.dependency_changes),
        },
        "profiles": profiles,
        "acceptance": {"automated": automated, "manual": manual},
        "summary": {
            "passed": statuses.count("passed"),
            "failed": statuses.count("failed") + statuses.count("blocked"),
            "pending": statuses.count("pending"),
        },
    }
    write_json_atomic(destination, report)
    return exit_code, report
