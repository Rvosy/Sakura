"""Bounded Git state collection and task scope evaluation."""

from __future__ import annotations

import fnmatch
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .task_contract import TaskContract


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
        return bool(
            self.untracked_files
            or any(change.origin in {"staged", "unstaged"} for change in self.changes)
        )


@dataclass(frozen=True)
class ScopeResult:
    status: str
    changed_files: tuple[str, ...]
    untracked_files: tuple[str, ...]
    out_of_scope_files: tuple[str, ...]
    forbidden_files: tuple[str, ...]
    protected_files: tuple[str, ...]
    dependency_files: tuple[str, ...]
    dependency_changes: tuple[dict[str, str], ...]
    deleted_tests: tuple[str, ...]
    contract_files: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "changed_files": list(self.changed_files),
            "untracked_files": list(self.untracked_files),
            "out_of_scope_files": list(self.out_of_scope_files),
            "forbidden_files": list(self.forbidden_files),
            "protected_files": list(self.protected_files),
            "dependency_files": list(self.dependency_files),
            "deleted_tests": list(self.deleted_tests),
            "contract_files": list(self.contract_files),
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
            f"GIT_COMMAND: git {' '.join(argv)} exited {completed.returncode}: {stderr.strip()}"
        )
    return completed.stdout


def _normalize_path(value: str) -> str:
    return PurePosixPath(value.replace("\\", "/")).as_posix()


def _parse_name_status(data: bytes, origin: str) -> tuple[list[GitChange], list[str]]:
    tokens = [token.decode("utf-8", errors="surrogateescape") for token in data.split(b"\0") if token]
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
    """Collect committed, staged, unstaged and untracked changes as one state."""
    base_sha_value = _run_git(repo_root, ["rev-parse", "--verify", f"{base_ref}^{{commit}}"])
    head_sha_value = _run_git(repo_root, ["rev-parse", "--verify", "HEAD^{commit}"])
    assert isinstance(base_sha_value, str) and isinstance(head_sha_value, str)
    changes: list[GitChange] = []
    deleted: list[str] = []
    for origin, args in (
        ("committed", (f"{base_sha_value.strip()}..HEAD",)),
        ("staged", ("--cached",)),
        ("unstaged", ()),
    ):
        source_changes, source_deleted = _diff(repo_root, origin, *args)
        changes.extend(source_changes)
        deleted.extend(source_deleted)
    untracked_data = _run_git(
        repo_root,
        ["ls-files", "--others", "--exclude-standard", "-z"],
        binary=True,
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
    unique_changes = {
        (change.path, change.status, change.origin): change for change in changes
    }
    return GitState(
        base_sha=base_sha_value.strip().lower(),
        head_sha=head_sha_value.strip().lower(),
        changes=tuple(
            unique_changes[key]
            for key in sorted(unique_changes, key=lambda item: (item[0], item[2], item[1]))
        ),
        untracked_files=untracked,
        deleted_paths=tuple(sorted(set(deleted))),
    )


def is_ancestor(repo_root: Path, base_sha: str) -> bool:
    """Return whether base_sha is an ancestor of HEAD; fail on Git errors."""
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
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def _is_dependency(path: str) -> bool:
    name = PurePosixPath(path).name
    return (
        name in DEPENDENCY_NAMES
        or fnmatch.fnmatchcase(name, "requirements*.txt")
        or path == "desktop/rust-toolchain.toml"
    )


def _git_file(repo_root: Path, revision: str, path: str) -> bytes | None:
    try:
        completed = subprocess.run(
            ["git", "show", f"{revision}:{path}"],
            cwd=repo_root,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GitStateError(f"GIT_COMMAND: cannot read frozen file {path}: {error}") from error
    return completed.stdout if completed.returncode == 0 else None


def _normalized_contract(data: bytes) -> bytes | None:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    value = dict(value)
    value["base_ref"] = "<ACTIVATION_REF>"
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def evaluate_scope(
    state: GitState,
    contract: TaskContract,
    *,
    repo_root: Path,
) -> ScopeResult:
    """Apply allow/deny/protection, dependency, deletion and freeze rules."""
    paths = set(state.changed_files)
    out_of_scope = sorted(
        path for path in paths if not _matches(path, contract.allowed_paths)
    )
    forbidden = sorted(path for path in paths if _matches(path, contract.forbidden_paths))
    protected = sorted(path for path in paths if _matches(path, contract.protected_paths))
    dependencies = sorted(path for path in paths if _is_dependency(path))
    dependency_changes: list[dict[str, str]] = []
    rejected_dependencies: list[str] = []
    for path in dependencies:
        allowed = contract.dependency_mode == "allowlisted" and _matches(
            path, contract.dependency_allowed_files
        )
        dependency_changes.append(
            {"path": path, "status": "allowed" if allowed else "forbidden"}
        )
        if not allowed:
            rejected_dependencies.append(path)

    deleted_tests = sorted(
        path for path in state.deleted_paths if path == "tests" or path.startswith("tests/")
    )
    frozen_changes: list[str] = []
    current_contract_path = repo_root / contract.task_path
    try:
        current_contract = current_contract_path.read_bytes()
    except OSError:
        current_contract = b""
    frozen_contract = _git_file(repo_root, state.base_sha, contract.task_path)
    if (
        frozen_contract is None
        or _normalized_contract(frozen_contract) != _normalized_contract(current_contract)
    ):
        frozen_changes.append(contract.task_path)
    for references in contract.documents.values():
        for reference in references:
            frozen = _git_file(repo_root, state.base_sha, reference)
            try:
                current = (repo_root / reference).read_bytes()
            except OSError:
                current = None
            if frozen is None or current != frozen:
                frozen_changes.append(reference)

    failed = bool(
        out_of_scope
        or forbidden
        or protected
        or rejected_dependencies
        or deleted_tests
        or frozen_changes
    )
    return ScopeResult(
        status="failed" if failed else "passed",
        changed_files=tuple(sorted(paths)),
        untracked_files=state.untracked_files,
        out_of_scope_files=tuple(out_of_scope),
        forbidden_files=tuple(forbidden),
        protected_files=tuple(protected),
        dependency_files=tuple(rejected_dependencies),
        dependency_changes=tuple(dependency_changes),
        deleted_tests=tuple(deleted_tests),
        contract_files=tuple(sorted(set(frozen_changes))),
    )
