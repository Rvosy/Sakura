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


@pytest.fixture(autouse=True)
def isolate_runtime_log_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterable[None]:
    """Keep in-process tests from appending to the user's runtime log."""
    from app.core import runtime_log

    real_path = runtime_log._FILE_LOG_PATH
    before = (
        (real_path.stat().st_size, real_path.stat().st_mtime_ns)
        if real_path.exists()
        else None
    )
    isolated_path = tmp_path / "logs" / "sakura-runtime.log"
    monkeypatch.setenv(runtime_log.RUNTIME_LOG_PATH_KEY, str(isolated_path))
    monkeypatch.setattr(runtime_log, "_FILE_LOG_PATH", isolated_path)

    yield

    after = (
        (real_path.stat().st_size, real_path.stat().st_mtime_ns)
        if real_path.exists()
        else None
    )
    assert after == before, "test wrote to the user's real runtime log"
