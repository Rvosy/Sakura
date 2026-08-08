"""Bounded Git changed-set collection and v2 task scope checks."""

from __future__ import annotations

import fnmatch
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .task_contract import TaskContract


GLOBAL_PROTECTED_PATHS = ("data/**", "characters/**", "third_party/**")
FINAL_ACTIVATION = "harness/activations/WP-H-02/0001.json"
DEPENDENCY_NAMES = {
    "pyproject.toml",
    "uv.lock",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "Cargo.toml",
    "Cargo.lock",
}


class GitStateError(ValueError):
    """Raised when Git state cannot be collected deterministically."""


@dataclass(frozen=True)
class GitChange:
    path: str
    status: str
    origin: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "status": self.status, "origin": self.origin}


@dataclass(frozen=True)
class GitState:
    base_sha: str
    head_sha: str
    changes: tuple[GitChange, ...]
    untracked_files: tuple[str, ...]
    deleted_paths: tuple[str, ...]

    @property
    def changed_files(self) -> tuple[str, ...]:
        return tuple(sorted({change.path for change in self.changes}))

    @property
    def has_worktree_changes(self) -> bool:
        return any(
            change.origin in {"staged", "unstaged", "untracked"}
            for change in self.changes
        )


@dataclass(frozen=True)
class ScopeResult:
    status: str
    changes: tuple[GitChange, ...]
    changed_files: tuple[str, ...]
    untracked_files: tuple[str, ...]
    out_of_scope_files: tuple[str, ...]
    protected_files: tuple[str, ...]
    retired_activation_files: tuple[str, ...]
    dependency_files: tuple[str, ...]
    dependency_changes: tuple[dict[str, str], ...]
    deleted_tests: tuple[str, ...]
    owner_review_files: tuple[str, ...]
    contract_revision_fields: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "changes": [change.as_dict() for change in self.changes],
            "changed_files": list(self.changed_files),
            "untracked_files": list(self.untracked_files),
            "out_of_scope_files": list(self.out_of_scope_files),
            "protected_files": list(self.protected_files),
            "retired_activation_files": list(self.retired_activation_files),
            "dependency_files": list(self.dependency_files),
            "deleted_tests": list(self.deleted_tests),
            "owner_review_files": list(self.owner_review_files),
            "contract_revision_fields": list(self.contract_revision_fields),
        }


def _run_git(repo_root: Path, argv: list[str], *, binary: bool = False) -> bytes | str:
    try:
        completed = subprocess.run(
            ["git", *argv],
            cwd=repo_root,
            capture_output=True,
            text=not binary,
            encoding=None if binary else "utf-8",
            errors=None if binary else "replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GitStateError(f"GIT_COMMAND: git {' '.join(argv)} failed: {error}") from error
    if completed.returncode != 0:
        stderr = (
            completed.stderr.decode("utf-8", errors="replace")
            if binary
            else completed.stderr
        )
        raise GitStateError(
            f"GIT_COMMAND: git {' '.join(argv)} exited "
            f"{completed.returncode}: {stderr.strip()}"
        )
    return completed.stdout


def _normalize_path(value: str) -> str:
    return PurePosixPath(value.replace("\\", "/")).as_posix()


def _parse_name_status(data: bytes, origin: str) -> tuple[list[GitChange], list[str]]:
    tokens = [
        token.decode("utf-8", errors="surrogateescape")
        for token in data.split(b"\0")
        if token
    ]
    changes: list[GitChange] = []
    deleted: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if "\t" in token:
            status, first_path = token.split("\t", 1)
            index += 1
        else:
            status = token
            index += 1
            if index >= len(tokens):
                raise GitStateError("GIT_PARSE: name-status output ended before a path")
            first_path = tokens[index]
            index += 1
        first_path = _normalize_path(first_path)
        code = status[:1]
        if code in {"R", "C"}:
            if index >= len(tokens):
                raise GitStateError("GIT_PARSE: rename output ended before destination")
            second_path = _normalize_path(tokens[index])
            index += 1
            changes.append(GitChange(first_path, "D" if code == "R" else code, origin))
            changes.append(GitChange(second_path, "A", origin))
            if code == "R":
                deleted.append(first_path)
        else:
            changes.append(GitChange(first_path, code, origin))
            if code == "D":
                deleted.append(first_path)
    return changes, deleted


def _diff(repo_root: Path, origin: str, *args: str) -> tuple[list[GitChange], list[str]]:
    data = _run_git(
        repo_root,
        ["diff", "--name-status", "--find-renames", "-z", *args],
        binary=True,
    )
    assert isinstance(data, bytes)
    return _parse_name_status(data, origin)


def collect_git_state(repo_root: Path, base_ref: str) -> GitState:
    """Merge committed, staged, unstaged and untracked changes."""
    base = _run_git(repo_root, ["rev-parse", "--verify", f"{base_ref}^{{commit}}"])
    head = _run_git(repo_root, ["rev-parse", "--verify", "HEAD^{commit}"])
    assert isinstance(base, str) and isinstance(head, str)
    changes: list[GitChange] = []
    deleted: list[str] = []
    for origin, args in (
        ("committed", (f"{base.strip()}..HEAD",)),
        ("staged", ("--cached",)),
        ("unstaged", ()),
    ):
        source_changes, source_deleted = _diff(repo_root, origin, *args)
        changes.extend(source_changes)
        deleted.extend(source_deleted)
    untracked_data = _run_git(
        repo_root, ["ls-files", "--others", "--exclude-standard", "-z"], binary=True
    )
    assert isinstance(untracked_data, bytes)
    untracked = tuple(
        sorted(
            _normalize_path(item.decode("utf-8", errors="surrogateescape"))
            for item in untracked_data.split(b"\0")
            if item
        )
    )
    changes.extend(GitChange(path, "?", "untracked") for path in untracked)
    unique = {(item.path, item.status, item.origin): item for item in changes}
    ordered = tuple(
        unique[key]
        for key in sorted(unique, key=lambda item: (item[0], item[2], item[1]))
    )
    return GitState(
        base_sha=base.strip().lower(),
        head_sha=head.strip().lower(),
        changes=ordered,
        untracked_files=untracked,
        deleted_paths=tuple(sorted(set(deleted))),
    )


def is_ancestor(repo_root: Path, base_sha: str) -> bool:
    try:
        completed = subprocess.run(
            ["git", "merge-base", "--is-ancestor", base_sha, "HEAD"],
            cwd=repo_root,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GitStateError(f"GIT_COMMAND: cannot compare base ancestry: {error}") from error
    if completed.returncode not in {0, 1}:
        raise GitStateError(
            "GIT_COMMAND: git merge-base --is-ancestor failed: "
            + completed.stderr.decode("utf-8", errors="replace").strip()
        )
    return completed.returncode == 0


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(
        path == pattern
        or (
            pattern.endswith("/**")
            and (
                path == pattern[:-3].rstrip("/")
                or path.startswith(pattern[:-3].rstrip("/") + "/")
            )
        )
        for pattern in patterns
    )


def _is_dependency(path: str) -> bool:
    name = PurePosixPath(path).name
    return (
        name in DEPENDENCY_NAMES
        or fnmatch.fnmatchcase(name, "requirements*.txt")
        or path == "desktop/rust-toolchain.toml"
    )


def evaluate_scope(state: GitState, contract: TaskContract) -> ScopeResult:
    """Apply allowlist, global protection, dependency and test-deletion checks."""
    paths = set(state.changed_files)
    out_of_scope = tuple(
        sorted(path for path in paths if not _matches(path, contract.allowed_paths))
    )
    protected = tuple(
        sorted(path for path in paths if _matches(path, GLOBAL_PROTECTED_PATHS))
    )
    retired_activations = tuple(
        sorted(
            path
            for path in paths
            if path.startswith("harness/activations/") and path != FINAL_ACTIVATION
        )
    )
    dependencies = tuple(sorted(path for path in paths if _is_dependency(path)))
    dependency_changes = tuple(
        {
            "path": path,
            "status": (
                "allowed"
                if path not in out_of_scope and path not in protected
                else "out_of_scope"
            ),
        }
        for path in dependencies
    )
    deleted_tests = tuple(
        sorted(
            path
            for path in state.deleted_paths
            if path == "tests" or path.startswith("tests/")
        )
    )
    owner_review = tuple(
        [contract.task_path]
        if any(
            change.path == contract.task_path
            and change.origin in {"staged", "unstaged", "untracked"}
            for change in state.changes
        )
        else []
    )
    hard_failure = bool(
        out_of_scope or protected or retired_activations or deleted_tests
    )
    status = (
        "failed"
        if hard_failure
        else "owner_review_required"
        if owner_review
        else "passed"
    )
    return ScopeResult(
        status=status,
        changes=state.changes,
        changed_files=state.changed_files,
        untracked_files=state.untracked_files,
        out_of_scope_files=out_of_scope,
        protected_files=protected,
        retired_activation_files=retired_activations,
        dependency_files=dependencies,
        dependency_changes=dependency_changes,
        deleted_tests=deleted_tests,
        owner_review_files=owner_review,
        contract_revision_fields=contract.revision_fields,
    )
