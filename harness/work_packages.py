"""Strict parser for the Runtime v2 Work Package status source."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


CURRENT_STATUSES = {"active", "stabilizing"}
VALID_STATUSES = {"planned", "active", "stabilizing", "accepted"}
WP_ID = re.compile(r"^WP-[A-Z0-9]+(?:-[A-Z0-9]+)+$")
EXPECTED_HEADER = ("Work Package", "主要结果", "依赖", "当前状态")


class WorkPackageError(ValueError):
    """Raised when the Work Package source cannot be trusted."""


@dataclass(frozen=True)
class WorkPackage:
    task_id: str
    title: str
    dependencies: tuple[str, ...]
    status: str


@dataclass(frozen=True)
class WorkPackageRegistry:
    packages: dict[str, WorkPackage]
    current: WorkPackage
    status_source: str


def _cells(line: str) -> tuple[str, ...]:
    if not line.strip().startswith("|") or not line.strip().endswith("|"):
        return ()
    return tuple(cell.strip() for cell in line.strip()[1:-1].split("|"))


def _metadata(lines: list[str]) -> dict[str, str]:
    if not lines or lines[0].strip() != "---":
        raise WorkPackageError("WORK_PACKAGE_FRONT_MATTER: missing YAML front matter")
    try:
        end = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
    except StopIteration as error:
        raise WorkPackageError(
            "WORK_PACKAGE_FRONT_MATTER: unterminated YAML front matter"
        ) from error
    values: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip():
            continue
        if ":" not in line:
            raise WorkPackageError(
                "WORK_PACKAGE_FRONT_MATTER: metadata must use key: value"
            )
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip()
    return values


def _dependencies(value: str) -> tuple[str, ...]:
    if value in {"无", "none", "None", "-"}:
        return ()
    return tuple(item.strip() for item in re.split(r"[、,]", value) if item.strip())


def load_work_packages(path: Path) -> WorkPackageRegistry:
    """Load the one exact four-column Work Package table and current metadata."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise WorkPackageError(f"WORK_PACKAGE_LOAD: cannot read {path}: {error}") from error
    lines = text.splitlines()
    metadata = _metadata(lines)
    header_indexes = [
        index for index, line in enumerate(lines) if _cells(line) == EXPECTED_HEADER
    ]
    if len(header_indexes) != 1:
        raise WorkPackageError(
            "WORK_PACKAGE_TABLE: expected exactly one four-column Work Package table"
        )
    header_index = header_indexes[0]
    if header_index + 1 >= len(lines):
        raise WorkPackageError("WORK_PACKAGE_TABLE: missing separator row")
    separator = _cells(lines[header_index + 1])
    if len(separator) != 4 or any(not re.fullmatch(r":?-{3,}:?", cell) for cell in separator):
        raise WorkPackageError("WORK_PACKAGE_TABLE: invalid separator row")

    packages: dict[str, WorkPackage] = {}
    for line in lines[header_index + 2 :]:
        cells = _cells(line)
        if not cells:
            if not line.strip():
                continue
            break
        if len(cells) != 4:
            raise WorkPackageError("WORK_PACKAGE_TABLE: every row must have four columns")
        task_id, title, dependency_text, status = cells
        if not WP_ID.fullmatch(task_id):
            raise WorkPackageError(f"WORK_PACKAGE_ID: invalid id {task_id!r}")
        if task_id in packages:
            raise WorkPackageError(f"WORK_PACKAGE_DUPLICATE: duplicate id {task_id}")
        if status not in VALID_STATUSES:
            raise WorkPackageError(
                f"WORK_PACKAGE_STATUS: {task_id} has unsupported status {status!r}"
            )
        dependencies = _dependencies(dependency_text)
        if any(not WP_ID.fullmatch(item) for item in dependencies):
            raise WorkPackageError(
                f"WORK_PACKAGE_DEPENDENCY: {task_id} has an invalid dependency"
            )
        packages[task_id] = WorkPackage(task_id, title, dependencies, status)

    if not packages:
        raise WorkPackageError("WORK_PACKAGE_TABLE: table has no Work Packages")
    for package in packages.values():
        unknown = [item for item in package.dependencies if item not in packages]
        if unknown:
            raise WorkPackageError(
                f"WORK_PACKAGE_DEPENDENCY: {package.task_id} references unknown "
                + ", ".join(unknown)
            )
    current = [package for package in packages.values() if package.status in CURRENT_STATUSES]
    if len(current) != 1:
        raise WorkPackageError(
            f"WORK_PACKAGE_CURRENT_COUNT: expected one active/stabilizing package, found {len(current)}"
        )
    metadata_current = metadata.get("active_work_package", "")
    if metadata_current != current[0].task_id:
        raise WorkPackageError(
            "WORK_PACKAGE_METADATA_MISMATCH: active_work_package does not match the table"
        )
    return WorkPackageRegistry(
        packages=packages,
        current=current[0],
        status_source=path.as_posix(),
    )
