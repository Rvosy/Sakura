from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Iterable
from pathlib import Path

import pytest


_ORIGINAL_PATH_MKDIR = Path.mkdir
_PYTEST_BASETEMP = (Path.cwd() / ".pytest-basetemp").resolve()
_TEST_TMP_ROOT = Path(__file__).resolve().parents[1] / "temp" / "pytest_tmp_path"


def _is_pytest_basetemp_path(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path.absolute()
    return resolved == _PYTEST_BASETEMP or _PYTEST_BASETEMP in resolved.parents


def _mkdir_without_private_windows_acl(
    self: Path,
    mode: int = 0o777,
    parents: bool = False,
    exist_ok: bool = False,
) -> None:
    if os.name == "nt" and mode == 0o700 and _is_pytest_basetemp_path(self):
        mode = 0o777
    return _ORIGINAL_PATH_MKDIR(self, mode=mode, parents=parents, exist_ok=exist_ok)


if os.name == "nt":
    Path.mkdir = _mkdir_without_private_windows_acl  # type: ignore[method-assign]


@pytest.fixture
def tmp_path() -> Iterable[Path]:
    """Repo-local tmp_path replacement for Windows sandboxes with broken %TEMP% ACLs."""
    path = _TEST_TMP_ROOT / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
