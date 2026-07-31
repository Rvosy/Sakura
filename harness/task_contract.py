"""Strict, dependency-free loader for Agent Development Harness task contracts."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


WP_ID = re.compile(r"^WP-[A-Z0-9]+(?:-[A-Z0-9]+)+$")
ROOT_FIELDS = {
    "schema_version",
    "id",
    "title",
    "status_source",
    "documents",
    "dependencies",
    "base_ref",
    "allowed_paths",
    "forbidden_paths",
    "protected_paths",
    "dependency_policy",
    "required_profiles",
    "acceptance",
    "rollback",
}
DOCUMENT_FIELDS = {"specs", "adrs", "plans"}
DEPENDENCY_POLICY_FIELDS = {"mode", "allowed_files"}
ACCEPTANCE_FIELDS = {"automated", "manual"}


class ContractError(ValueError):
    """Raised when a task contract is structurally or semantically invalid."""


@dataclass(frozen=True)
class TaskContract:
    task_id: str
    title: str
    status_source: str
    documents: dict[str, tuple[str, ...]]
    dependencies: tuple[str, ...]
    base_ref: str
    base_sha: str
    allowed_paths: tuple[str, ...]
    forbidden_paths: tuple[str, ...]
    protected_paths: tuple[str, ...]
    dependency_mode: str
    dependency_allowed_files: tuple[str, ...]
    required_profiles: tuple[str, ...]
    automated_acceptance: tuple[str, ...]
    manual_acceptance: tuple[str, ...]
    rollback: tuple[str, ...]
    task_path: str


def _object(value: Any, field: str, allowed: set[str], errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"CONTRACT_TYPE: {field} must be an object")
        return {}
    unknown = sorted(set(value) - allowed)
    if unknown:
        errors.append(f"CONTRACT_UNKNOWN: {field} has unknown fields: {', '.join(unknown)}")
    missing = sorted(allowed - set(value))
    if missing:
        errors.append(f"CONTRACT_REQUIRED: {field} is missing: {', '.join(missing)}")
    return value


def _string(value: Any, field: str, errors: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"CONTRACT_TYPE: {field} must be a non-empty string")
        return ""
    return value


def _strings(
    value: Any,
    field: str,
    errors: list[str],
    *,
    non_empty: bool,
) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        errors.append(f"CONTRACT_TYPE: {field} must be a string array")
        return ()
    items = tuple(value)
    if non_empty and not items:
        errors.append(f"CONTRACT_REQUIRED: {field} must not be empty")
    if len(set(items)) != len(items):
        code = "CONTRACT_DUPLICATE_PATH" if "paths" in field else "CONTRACT_DUPLICATE"
        errors.append(f"{code}: {field} contains duplicate values")
    return items


def _validate_path(pattern: str, field: str, errors: list[str]) -> None:
    pure = PurePosixPath(pattern)
    if (
        "\\" in pattern
        or pattern.startswith(("/", "./"))
        or pure.is_absolute()
        or ".." in pure.parts
        or pattern.endswith("/")
    ):
        errors.append(
            f"CONTRACT_PATH: {field} must contain repository-relative POSIX patterns: {pattern!r}"
        )


def _prefix(pattern: str) -> str | None:
    if pattern.endswith("/**"):
        return pattern[:-3].rstrip("/")
    if not any(token in pattern for token in "*?["):
        return pattern.rstrip("/")
    return None


def _patterns_conflict(left: str, right: str) -> bool:
    if left == right:
        return True
    left_prefix = _prefix(left)
    right_prefix = _prefix(right)
    if left_prefix is None or right_prefix is None:
        return False
    return left_prefix == right_prefix or left_prefix.startswith(
        right_prefix + "/"
    ) or right_prefix.startswith(left_prefix + "/")


def _resolve_base(repo_root: Path, base_ref: str, errors: list[str]) -> str:
    if not base_ref:
        return ""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--verify", f"{base_ref}^{{commit}}"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        errors.append(f"CONTRACT_BASE_REF: cannot resolve {base_ref!r}: {error}")
        return ""
    sha = completed.stdout.strip()
    if completed.returncode != 0 or not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        errors.append(f"CONTRACT_BASE_REF: {base_ref!r} is not a commit")
        return ""
    return sha.lower()


def load_task_contract(
    path: Path,
    *,
    repo_root: Path,
    manifest: dict[str, Any],
) -> TaskContract:
    """Load and semantically validate one v1 task contract."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError(f"CONTRACT_LOAD: cannot load {path}: {error}") from error
    errors: list[str] = []
    root = _object(raw, "contract", ROOT_FIELDS, errors)
    if root.get("schema_version") != 1:
        errors.append("CONTRACT_SCHEMA: schema_version must be 1")
    task_id = _string(root.get("id"), "id", errors)
    if task_id and not WP_ID.fullmatch(task_id):
        errors.append(f"CONTRACT_ID: invalid Work Package id {task_id!r}")
    title = _string(root.get("title"), "title", errors)
    status_source = _string(root.get("status_source"), "status_source", errors)
    if status_source and status_source != "docs/plans/runtime-v2/work-packages.md":
        errors.append("CONTRACT_STATUS_SOURCE: unsupported status source")

    document_object = _object(
        root.get("documents"), "documents", DOCUMENT_FIELDS, errors
    )
    documents = {
        name: _strings(
            document_object.get(name), f"documents.{name}", errors, non_empty=False
        )
        for name in sorted(DOCUMENT_FIELDS)
    }
    if not any(documents.values()):
        errors.append(
            "CONTRACT_DOCUMENTS_EMPTY: documents must reference at least one "
            "spec, ADR, or plan"
        )
    dependencies = _strings(
        root.get("dependencies"), "dependencies", errors, non_empty=False
    )
    if any(not WP_ID.fullmatch(item) for item in dependencies):
        errors.append("CONTRACT_DEPENDENCY: dependencies must be Work Package IDs")
    base_ref = _string(root.get("base_ref"), "base_ref", errors)

    path_groups: dict[str, tuple[str, ...]] = {}
    for name in ("allowed_paths", "forbidden_paths", "protected_paths"):
        values = _strings(root.get(name), name, errors, non_empty=True)
        for value in values:
            _validate_path(value, name, errors)
        path_groups[name] = values
    group_names = tuple(path_groups)
    for index, left_name in enumerate(group_names):
        for right_name in group_names[index + 1 :]:
            for left in path_groups[left_name]:
                for right in path_groups[right_name]:
                    if _patterns_conflict(left, right):
                        errors.append(
                            "CONTRACT_PATH_CONFLICT: "
                            f"{left_name} {left!r} conflicts with {right_name} {right!r}"
                        )

    policy = _object(
        root.get("dependency_policy"),
        "dependency_policy",
        DEPENDENCY_POLICY_FIELDS,
        errors,
    )
    dependency_mode = policy.get("mode")
    if dependency_mode not in {"forbidden", "allowlisted"}:
        errors.append("CONTRACT_DEPENDENCY_POLICY: mode must be forbidden or allowlisted")
        dependency_mode = "forbidden"
    dependency_allowed = _strings(
        policy.get("allowed_files"),
        "dependency_policy.allowed_files",
        errors,
        non_empty=False,
    )
    if dependency_mode == "forbidden" and dependency_allowed:
        errors.append(
            "CONTRACT_DEPENDENCY_POLICY: forbidden mode cannot allow dependency files"
        )
    for value in dependency_allowed:
        _validate_path(value, "dependency_policy.allowed_files", errors)

    profiles = _strings(
        root.get("required_profiles"), "required_profiles", errors, non_empty=True
    )
    known_profiles = manifest.get("profiles", {}) if isinstance(manifest, dict) else {}
    unknown_profiles = [profile for profile in profiles if profile not in known_profiles]
    if unknown_profiles:
        errors.append(
            "CONTRACT_PROFILE_UNKNOWN: unknown profiles: " + ", ".join(unknown_profiles)
        )

    acceptance = _object(
        root.get("acceptance"), "acceptance", ACCEPTANCE_FIELDS, errors
    )
    automated = _strings(
        acceptance.get("automated"),
        "acceptance.automated",
        errors,
        non_empty=False,
    )
    manual = _strings(
        acceptance.get("manual"), "acceptance.manual", errors, non_empty=False
    )
    if not automated:
        errors.append("CONTRACT_ACCEPTANCE_EMPTY: automated acceptance must not be empty")
    rollback = _strings(root.get("rollback"), "rollback", errors, non_empty=True)

    expected_roots = {
        "specs": "docs/specs/",
        "adrs": "docs/adr/",
        "plans": "docs/plans/",
    }
    for kind, references in documents.items():
        for reference in references:
            _validate_path(reference, f"documents.{kind}", errors)
            candidate = repo_root / reference
            if not reference.startswith(expected_roots[kind]) or not candidate.is_file():
                errors.append(
                    f"CONTRACT_DOCUMENT_MISSING: {kind} reference does not exist: {reference}"
                )

    base_sha = _resolve_base(repo_root, base_ref, errors)
    try:
        task_path = path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        errors.append("CONTRACT_PATH: task contract must be inside the repository")
        task_path = path.as_posix()
    if errors:
        raise ContractError("\n".join(dict.fromkeys(errors)))
    return TaskContract(
        task_id=task_id,
        title=title,
        status_source=status_source,
        documents=documents,
        dependencies=dependencies,
        base_ref=base_ref,
        base_sha=base_sha,
        allowed_paths=path_groups["allowed_paths"],
        forbidden_paths=path_groups["forbidden_paths"],
        protected_paths=path_groups["protected_paths"],
        dependency_mode=dependency_mode,
        dependency_allowed_files=dependency_allowed,
        required_profiles=profiles,
        automated_acceptance=automated,
        manual_acceptance=manual,
        rollback=rollback,
        task_path=task_path,
    )
