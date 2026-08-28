"""Explicit installer for Plugin API v4 private dependency roots."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from app.storage.atomic import atomic_write_text
from app.storage.paths import StoragePaths
from app.storage.runtime_roots import DistributionPaths


INSTALL_TIMEOUT_SECONDS = 600.0
INITIALIZE_IMPORT_TIMEOUT_SECONDS = 15.0
_MARKER = ".sakura-dependencies.json"


class PluginDependencyError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail[:2000]


@dataclass(frozen=True)
class DependencyDeclaration:
    kind: str
    path: Path
    fingerprint: str
    dependencies: tuple[str, ...] = ()


class PluginDependencyRoots:
    """Build and validate one ``uv pip --target`` root per plugin.

    Resolution is intentionally invoked only from explicit install/update/retry
    operations.  Runtime startup calls ``verified_root`` and fails if the exact
    declared environment is absent; it never repairs the environment.
    """

    def __init__(
        self,
        user_root: Path,
        *,
        distribution_root: Path | None = None,
        python: Path | None = None,
    ) -> None:
        self._paths = StoragePaths(user_root)
        self._distribution = (
            DistributionPaths(distribution_root)
            if distribution_root is not None
            else None
        )
        self._python = Path(python or sys.executable)

    def declaration(self, plugin_root: Path) -> DependencyDeclaration | None:
        root = Path(plugin_root)
        requirements_lock = root / "requirements.lock"
        if requirements_lock.is_file():
            return self._file_declaration("requirements.lock", requirements_lock)
        requirements = root / "requirements.txt"
        if requirements.is_file():
            return self._file_declaration("requirements.txt", requirements)
        pyproject = root / "pyproject.toml"
        if not pyproject.is_file():
            return None
        try:
            raw_bytes = pyproject.read_bytes()
            raw = tomllib.loads(raw_bytes.decode("utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            raise PluginDependencyError("PLUGIN_DEPENDENCY_DECLARATION_INVALID") from error
        project = raw.get("project", {})
        dependencies = project.get("dependencies", []) if isinstance(project, dict) else []
        if not isinstance(dependencies, list) or any(not isinstance(item, str) for item in dependencies):
            raise PluginDependencyError("PLUGIN_DEPENDENCY_DECLARATION_INVALID")
        uv_lock = root / "uv.lock"
        digest = hashlib.sha256(raw_bytes)
        kind = "pyproject.toml"
        if uv_lock.is_file():
            try:
                digest.update(uv_lock.read_bytes())
            except OSError as error:
                raise PluginDependencyError("PLUGIN_DEPENDENCY_DECLARATION_INVALID") from error
            kind = "uv.lock"
        if not dependencies and not uv_lock.is_file():
            return None
        return DependencyDeclaration(
            kind,
            pyproject,
            digest.hexdigest(),
            tuple(dependencies),
        )

    def install(
        self,
        plugin_id: str,
        plugin_root: Path,
        *,
        entry: str | None = None,
    ) -> Path | None:
        declaration = self.declaration(plugin_root)
        final = self._paths.plugin_dependency_root_for(plugin_id)
        if declaration is None:
            if entry is not None:
                self._validate_entry(plugin_id, plugin_root, None, entry)
            return None
        parent = self._paths.plugin_dependency_roots_dir
        parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".build-", dir=parent))
        try:
            command, exported = self._install_command(declaration, staging)
            result = subprocess.run(
                command,
                cwd=Path(plugin_root),
                env=self._uv_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=INSTALL_TIMEOUT_SECONDS,
                check=False,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip()
                raise PluginDependencyError("PLUGIN_DEPENDENCY_INSTALL_FAILED", detail)
            if entry is not None:
                self._validate_entry(plugin_id, plugin_root, staging, entry)
            marker = {
                "schemaVersion": 1,
                "kind": declaration.kind,
                "fingerprint": declaration.fingerprint,
                "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            }
            atomic_write_text(
                staging / _MARKER,
                json.dumps(marker, ensure_ascii=False, sort_keys=True),
            )
            if final.exists():
                raise PluginDependencyError("PLUGIN_DEPENDENCY_ROOT_CONFLICT")
            os.replace(staging, final)
            return final
        except subprocess.TimeoutExpired as error:
            raise PluginDependencyError("PLUGIN_DEPENDENCY_INSTALL_TIMEOUT") from error
        except OSError as error:
            raise PluginDependencyError("PLUGIN_DEPENDENCY_INSTALL_FAILED") from error
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            if 'exported' in locals() and exported is not None:
                try:
                    exported.unlink()
                except OSError:
                    pass

    def verified_root(
        self,
        plugin_id: str,
        plugin_root: Path,
        *,
        source: str = "user",
    ) -> Path | None:
        declaration = self.declaration(plugin_root)
        if declaration is None:
            return None
        if source == "bundled":
            if self._distribution is None:
                raise PluginDependencyError("PLUGIN_DEPENDENCIES_MISSING")
            root = self._distribution.plugin_dependency_root_for(plugin_id)
        elif source == "user":
            root = self._paths.plugin_dependency_root_for(plugin_id)
        else:
            raise PluginDependencyError("PLUGIN_DEPENDENCY_SOURCE_INVALID")
        marker_path = root / _MARKER
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise PluginDependencyError("PLUGIN_DEPENDENCIES_MISSING")
        expected_python = f"{sys.version_info.major}.{sys.version_info.minor}"
        if (
            not isinstance(marker, dict)
            or marker.get("schemaVersion") != 1
            or marker.get("kind") != declaration.kind
            or marker.get("fingerprint") != declaration.fingerprint
            or marker.get("python") != expected_python
        ):
            raise PluginDependencyError("PLUGIN_DEPENDENCIES_STALE")
        return root

    def remove(self, plugin_id: str) -> None:
        root = self._paths.plugin_dependency_root_for(plugin_id)
        if root.exists():
            shutil.rmtree(root)

    @staticmethod
    def _file_declaration(kind: str, path: Path) -> DependencyDeclaration:
        try:
            content = path.read_bytes()
        except OSError as error:
            raise PluginDependencyError("PLUGIN_DEPENDENCY_DECLARATION_INVALID") from error
        if not content.strip():
            return DependencyDeclaration(kind, path, hashlib.sha256(content).hexdigest())
        return DependencyDeclaration(kind, path, hashlib.sha256(content).hexdigest())

    def _install_command(
        self,
        declaration: DependencyDeclaration,
        staging: Path,
    ) -> tuple[list[str], Path | None]:
        uv = self._uv_command()
        base = [
            *uv,
            "pip",
            "install",
            "--target",
            str(staging),
            "--python",
            str(self._python),
            "--no-python-downloads",
            "--link-mode",
            "clone" if sys.platform == "darwin" else "hardlink",
            "--no-progress",
        ]
        if declaration.kind in {"requirements.lock", "requirements.txt"}:
            return [*base, "--requirements", str(declaration.path)], None
        if declaration.kind == "uv.lock":
            descriptor, name = tempfile.mkstemp(prefix="sakura-plugin-", suffix=".txt")
            os.close(descriptor)
            exported = Path(name)
            export = subprocess.run(
                [
                    *uv,
                    "export",
                    "--frozen",
                    "--no-dev",
                    "--no-emit-project",
                    "--output-file",
                    str(exported),
                ],
                cwd=declaration.path.parent,
                env=self._uv_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=INSTALL_TIMEOUT_SECONDS,
                check=False,
            )
            if export.returncode != 0:
                exported.unlink(missing_ok=True)
                raise PluginDependencyError(
                    "PLUGIN_DEPENDENCY_INSTALL_FAILED",
                    (export.stderr or export.stdout).strip(),
                )
            return [*base, "--requirements", str(exported)], exported
        return [*base, *declaration.dependencies], None

    def _uv_command(self) -> list[str]:
        adjacent = self._python.with_name("uv.exe" if os.name == "nt" else "uv")
        if adjacent.is_file():
            return [str(adjacent)]
        executable = shutil.which("uv")
        if executable:
            return [executable]
        return [str(self._python), "-m", "uv"]

    def _uv_environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        environment["UV_CACHE_DIR"] = str(self._paths.uv_cache_dir)
        environment["UV_PYTHON_DOWNLOADS"] = "never"
        return environment

    def _validate_entry(
        self,
        plugin_id: str,
        plugin_root: Path,
        dependency_root: Path | None,
        entry: str,
    ) -> None:
        runner = Path(__file__).with_name("plugin_runner_v4.py")
        command = [
            str(self._python),
            "-I",
            "-S",
            str(runner),
            "--plugin-id",
            plugin_id,
            "--generation-id",
            "install-validation",
            "--plugin-root",
            str(plugin_root),
            "--data-dir",
            str(plugin_root),
            "--entry",
            entry,
            "--validate-entry",
        ]
        if dependency_root is not None:
            command.extend(["--dependency-root", str(dependency_root)])
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
        environment["PYTHONNOUSERSITE"] = "1"
        try:
            result = subprocess.run(
                command,
                cwd=plugin_root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=INITIALIZE_IMPORT_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise PluginDependencyError("PLUGIN_ENTRY_IMPORT_FAILED") from error
        if result.returncode != 0:
            raise PluginDependencyError(
                "PLUGIN_ENTRY_IMPORT_FAILED",
                (result.stderr or result.stdout).strip(),
            )


__all__ = [
    "DependencyDeclaration",
    "PluginDependencyError",
    "PluginDependencyRoots",
]
