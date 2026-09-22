"""Explicit installer for Plugin API v4 private dependency roots."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from app.core.diagnostics import TRACE_LIMIT, bounded_text
from app.plugin_sdk.sakura_downloads import uv_download_environment
from app.plugins.process_paths import process_path
from app.storage.atomic import atomic_write_text
from app.storage.paths import StoragePaths
from app.storage.runtime_roots import DistributionPaths


INSTALL_TIMEOUT_SECONDS = 600.0
INITIALIZE_IMPORT_TIMEOUT_SECONDS = 15.0
_MARKER = ".sakura-dependencies.json"


class PluginDependencyError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = bounded_text(detail, TRACE_LIMIT)
        super().__init__(f"{code}: {self.detail}" if self.detail else code)


@dataclass(frozen=True)
class DependencyDeclaration:
    kind: str
    path: Path
    dependencies: tuple[str, ...] = ()


class PluginDependencyRoots:
    """Build and validate one ``uv pip --target`` root per plugin.

    Resolution is intentionally invoked only from explicit install/update/retry
    operations. Runtime startup checks the installed marker, declaration kind
    and Python ABI; it never repairs or fingerprints the environment.
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
            return DependencyDeclaration("requirements.lock", requirements_lock)
        requirements = root / "requirements.txt"
        if requirements.is_file():
            return DependencyDeclaration("requirements.txt", requirements)
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
        kind = "pyproject.toml"
        if uv_lock.is_file():
            kind = "uv.lock"
        if not dependencies and not uv_lock.is_file():
            return None
        return DependencyDeclaration(
            kind,
            pyproject,
            tuple(dependencies),
        )

    def install(
        self,
        plugin_id: str,
        plugin_root: Path,
        *,
        entry: str | None = None,
    ) -> Path | None:
        with self.prepare(plugin_id, plugin_root, entry=entry) as staging:
            if staging is None:
                return None
            final = self._paths.plugin_dependency_root_for(plugin_id)
            if final.exists():
                raise PluginDependencyError("PLUGIN_DEPENDENCY_ROOT_CONFLICT")
            os.replace(staging, final)
            return final

    @contextmanager
    def prepare(
        self,
        plugin_id: str,
        plugin_root: Path,
        *,
        entry: str | None = None,
        bundled: bool = False,
    ) -> Iterator[Path | None]:
        """Prepare and import-check dependencies before replacing an existing root."""
        declaration = self.declaration(plugin_root)
        if declaration is None:
            if entry is not None:
                self._validate_entry(plugin_id, plugin_root, None, entry)
            yield None
            return
        parent = self._paths.plugin_dependency_roots_dir
        parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".build-", dir=parent))
        try:
            if bundled:
                source = self.verified_root(plugin_id, plugin_root, source="bundled")
                assert source is not None
                shutil.copytree(source, staging, dirs_exist_ok=True)
            else:
                command, exported = self._install_command(declaration, staging)
                result = subprocess.run(
                    command,
                    cwd=Path(plugin_root),
                    env=self._uv_environment(plugin_root),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=INSTALL_TIMEOUT_SECONDS,
                    check=False,
                )
                if result.returncode != 0:
                    detail = f"exit_code={result.returncode}\n{(result.stderr or result.stdout).strip()}"
                    raise PluginDependencyError("PLUGIN_DEPENDENCY_INSTALL_FAILED", detail)
            if entry is not None:
                self._validate_entry(plugin_id, plugin_root, staging, entry)
            marker = {
                "schemaVersion": 1,
                "kind": declaration.kind,
                "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            }
            atomic_write_text(
                staging / _MARKER,
                json.dumps(marker, ensure_ascii=False, sort_keys=True),
            )
            yield staging
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

    def install_bundled(self, plugin_id: str, plugin_root: Path, *, entry: str) -> Path | None:
        """复制发行包已准备的依赖，不执行下载；用于内置插件迁移。"""
        source = self.verified_root(plugin_id, plugin_root, source="bundled")
        if source is None:
            self._validate_entry(plugin_id, plugin_root, None, entry)
            return None
        final = self._paths.plugin_dependency_root_for(plugin_id)
        if final.exists():
            existing = self.verified_root(plugin_id, plugin_root)
            self._validate_entry(plugin_id, plugin_root, existing, entry)
            return existing
        final.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".migrate-", dir=final.parent))
        try:
            payload = staging / "dependencies"
            shutil.copytree(source, payload)
            self._validate_entry(plugin_id, plugin_root, payload, entry)
            os.replace(payload, final)
            return final
        finally:
            shutil.rmtree(staging, ignore_errors=True)

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
        return self.verified_path(plugin_root, root)

    def verified_path(self, plugin_root: Path, root: Path) -> Path | None:
        """Check an installed dependency root, including an offline migration payload."""
        declaration = self.declaration(plugin_root)
        if declaration is None:
            return None
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
            or marker.get("python") != expected_python
        ):
            raise PluginDependencyError("PLUGIN_DEPENDENCIES_STALE")
        return root

    def remove(self, plugin_id: str) -> None:
        root = self._paths.plugin_dependency_root_for(plugin_id)
        if root.exists():
            shutil.rmtree(root)

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
                env=self._uv_environment(declaration.path.parent),
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
                    f"exit_code={export.returncode}\n{(export.stderr or export.stdout).strip()}",
                )
            return [*base, "--requirements", str(exported)], exported
        return [*base, *declaration.dependencies], None

    def _uv_command(self) -> list[str]:
        uv_name = "uv.exe" if os.name == "nt" else "uv"
        if self._distribution is not None:
            bundled = self._distribution.python_tools_dir / uv_name
            if bundled.is_file():
                return [str(bundled)]
        adjacent = self._python.with_name(uv_name)
        if adjacent.is_file():
            return [str(adjacent)]
        executable = shutil.which("uv")
        if executable:
            return [executable]
        return [str(self._python), "-m", "uv"]

    def _uv_environment(self, directory: Path | None = None) -> dict[str, str]:
        environment = uv_download_environment(directory or Path.cwd())
        # uv consumes environment proxies. Snapshot the current system settings
        # when starting this download job, not when Sakura starts.
        for scheme, proxy in urllib.request.getproxies().items():
            if scheme in {"http", "https", "all", "no"}:
                environment[f"{scheme.upper()}_PROXY"] = proxy
                environment[f"{scheme}_proxy"] = proxy
        environment["UV_CACHE_DIR"] = str(self._paths.uv_cache_dir)
        environment["UV_PYTHON_DOWNLOADS"] = "never"
        return environment

    def _validate_entry(
        self,
        plugin_id: str,
        plugin_root: Path,
        dependency_root: Path | None,
        entry: str,
        *,
        runtime_imports: tuple[str, ...] | list[str] = (),
    ) -> None:
        runner = Path(__file__).with_name("plugin_runner_v4.py")
        command = [
            str(self._python),
            "-I",
            "-S",
            "-B",
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
        for module in runtime_imports:
            command.extend(["--validate-import", module])
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
        environment["PYTHONNOUSERSITE"] = "1"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            result = subprocess.run(
                command,
                cwd=process_path(plugin_root),
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
                f"exit_code={result.returncode}\n{(result.stderr or result.stdout).strip()}",
            )


__all__ = [
    "DependencyDeclaration",
    "PluginDependencyError",
    "PluginDependencyRoots",
]
