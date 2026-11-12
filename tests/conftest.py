from __future__ import annotations

import os
import shutil
import sys
import tempfile
import uuid
from collections.abc import Iterable
from pathlib import Path

import pytest


# Mirror the public SDK import path supplied by the isolated plugin runner.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app" / "plugin_sdk"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "builtin" / "sakura_assistant"))

_ORIGINAL_PATH_MKDIR = Path.mkdir
_TEST_TMP_ROOT = Path(tempfile.gettempdir()) / "sakura-tests"
_PYTEST_BASETEMP = _TEST_TMP_ROOT / f"pytest-{uuid.uuid4().hex}"


def pytest_configure(config: pytest.Config) -> None:
    global _PYTEST_BASETEMP
    if config.option.basetemp:
        _PYTEST_BASETEMP = Path(config.option.basetemp).resolve()
    else:
        _TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = str(_PYTEST_BASETEMP)


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
def tmp_path(tmp_path_factory: pytest.TempPathFactory) -> Iterable[Path]:
    """Isolated temporary directory without Windows private-ACL mkdir semantics."""
    path = tmp_path_factory.mktemp("case")
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(scope="session")
def assistant_dependencies(tmp_path_factory: pytest.TempPathFactory):
    """Offline private dependency root using installed, read-only runtime packages."""
    import importlib.util
    root = tmp_path_factory.mktemp("assistant-dependencies")
    packages = ("openai", "httpx", "httpcore", "h11", "anyio", "sniffio", "idna", "certifi", "pydantic",
                "pydantic_core", "typing_extensions", "annotated_types", "typing_inspection", "jiter", "distro", "tqdm", "socksio", "colorama", "json_repair")
    for name in packages:
        spec = importlib.util.find_spec(name)
        if spec is None:
            continue
        if spec.submodule_search_locations:
            source = Path(next(iter(spec.submodule_search_locations)))
            shutil.copytree(source, root / source.name, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            source = Path(spec.origin)
            shutil.copy2(source, root / source.name)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)
