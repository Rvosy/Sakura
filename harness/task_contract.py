"""Strict loader for the deliberately small Harness task contract v2."""

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
    "base_ref",
    "allowed_paths",
    "required_profiles",
}
REVISION_FIELDS = ("allowed_paths", "required_profiles")


class ContractError(ValueError):
    """Raised when a task contract is invalid or its base was moved."""


@dataclass(frozen=True)
class TaskContract:
    task_id: str
    base_ref: str
    base_sha: str
    allowed_paths: tuple[str, ...]
    required_profiles: tuple[str, ...]
    task_path: str
    initial_task_sha: str
    revision_fields: tuple[str, ...]


def _git(repo_root: Path, argv: list[str]) -> str:
    try:
        completed = subprocess.run(
            ["git", *argv],
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ContractError(
            f"CONTRACT_HISTORY: git {' '.join(argv)} failed: {error}"
        ) from error
    if completed.returncode != 0:
        raise ContractError(
            f"CONTRACT_HISTORY: git {' '.join(argv)} exited "
            f"{completed.returncode}: {completed.stderr.strip()}"
        )
    return completed.stdout


def _strings(value: Any, field: str, *, non_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ContractError(f"CONTRACT_TYPE: {field} must be a string array")
    result = tuple(value)
    if non_empty and not result:
        raise ContractError(f"CONTRACT_REQUIRED: {field} must not be empty")
    if len(result) != len(set(result)):
        raise ContractError(f"CONTRACT_DUPLICATE: {field} contains duplicate values")
    return result


def _validate_path(pattern: str) -> None:
    pure = PurePosixPath(pattern)
    recursive = pattern.endswith("/**")
    prefix = pattern[:-3] if recursive else pattern
    if (
        "\\" in pattern
        or pattern.startswith(("/", "./"))
        or pure.is_absolute()
        or ".." in pure.parts
        or pattern.endswith("/")
        or not prefix
        or any(token in prefix for token in "*?[")
    ):
        raise ContractError(
            "CONTRACT_PATH: allowed_paths only accepts repository-relative exact "
            f"paths or directory/**: {pattern!r}"
        )


def _json_from_git(repo_root: Path, revision: str, task_path: str) -> dict[str, Any]:
    text = _git(repo_root, ["show", f"{revision}:{task_path}"])
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ContractError(
            f"CONTRACT_HISTORY: {revision}:{task_path} is not valid JSON"
        ) from error
    if not isinstance(value, dict):
        raise ContractError(
            f"CONTRACT_HISTORY: {revision}:{task_path} must be an object"
        )
    return value


def _resolve_base(repo_root: Path, base_ref: Any) -> tuple[str, str]:
    if not isinstance(base_ref, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", base_ref):
        raise ContractError(
            "CONTRACT_BASE_REF_FORMAT: base_ref must be a full 40-character commit SHA"
        )
    try:
        resolved = _git(repo_root, ["rev-parse", "--verify", f"{base_ref}^{{commit}}"])
    except ContractError as error:
        raise ContractError(f"CONTRACT_BASE_REF: {base_ref!r} is not a commit") from error
    return base_ref.lower(), resolved.strip().lower()


def load_task_contract(
    path: Path,
    *,
    repo_root: Path,
    manifest: dict[str, Any],
) -> TaskContract:
    """Load one active v2 task and verify its immutable changed-set base."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError(f"CONTRACT_LOAD: cannot load {path}: {error}") from error
    if not isinstance(raw, dict):
        raise ContractError("CONTRACT_TYPE: contract must be an object")
    if raw.get("schema_version") != 2:
        version = raw.get("schema_version")
        suffix = "; v1 tasks are historical only" if version == 1 else ""
        raise ContractError(
            f"CONTRACT_SCHEMA: schema_version must be 2 (got {version!r}){suffix}"
        )
    unknown = sorted(set(raw) - ROOT_FIELDS)
    missing = sorted(ROOT_FIELDS - set(raw))
    if unknown:
        raise ContractError(
            "CONTRACT_UNKNOWN: contract has unknown fields: " + ", ".join(unknown)
        )
    if missing:
        raise ContractError(
            "CONTRACT_REQUIRED: contract is missing: " + ", ".join(missing)
        )

    task_id = raw["id"]
    if not isinstance(task_id, str) or not WP_ID.fullmatch(task_id):
        raise ContractError(f"CONTRACT_ID: invalid Work Package id {task_id!r}")
    if path.stem != task_id:
        raise ContractError(
            f"CONTRACT_ID: id {task_id!r} does not match task filename {path.stem!r}"
        )
    base_ref, base_sha = _resolve_base(repo_root, raw["base_ref"])
    allowed_paths = _strings(raw["allowed_paths"], "allowed_paths")
    for pattern in allowed_paths:
        _validate_path(pattern)
    profiles = _strings(raw["required_profiles"], "required_profiles")
    known_profiles = manifest.get("profiles") if isinstance(manifest, dict) else None
    known_profiles = known_profiles if isinstance(known_profiles, dict) else {}
    unknown_profiles = [name for name in profiles if name not in known_profiles]
    if unknown_profiles:
        raise ContractError(
            "CONTRACT_PROFILE_UNKNOWN: unknown profiles: " + ", ".join(unknown_profiles)
        )
    if {"core-host", "python-full"} <= set(profiles):
        raise ContractError(
            "CONTRACT_PROFILE_OVERLAP: core-host and python-full cannot both be required"
        )
    if {"smoke", "python-full"} <= set(profiles):
        raise ContractError(
            "CONTRACT_PROFILE_OVERLAP: smoke and python-full cannot both be required"
        )

    try:
        task_path = path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError as error:
        raise ContractError(
            "CONTRACT_PATH: task contract must be inside the repository"
        ) from error
    additions = [
        line
        for line in _git(
            repo_root,
            ["log", "--format=%H", "--diff-filter=A", "--", task_path],
        ).splitlines()
        if line
    ]
    if len(additions) != 1:
        raise ContractError(
            f"CONTRACT_HISTORY: {task_path} must have exactly one committed addition"
        )
    initial_task_sha = additions[0].lower()
    initial = _json_from_git(repo_root, initial_task_sha, task_path)
    initial_base = initial.get("base_ref")
    if not isinstance(initial_base, str) or base_ref != initial_base.lower():
        raise ContractError(
            "CONTRACT_BASE_REF_MOVED: base_ref differs from the task file's first commit"
        )
    head = _json_from_git(repo_root, "HEAD", task_path)
    revision_fields = tuple(
        field for field in REVISION_FIELDS if head.get(field) != initial.get(field)
    )
    return TaskContract(
        task_id=task_id,
        base_ref=base_ref,
        base_sha=base_sha,
        allowed_paths=allowed_paths,
        required_profiles=profiles,
        task_path=task_path,
        initial_task_sha=initial_task_sha,
        revision_fields=revision_fields,
    )
