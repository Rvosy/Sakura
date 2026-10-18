"""Explicit installer for Plugin API v4 private dependency roots."""

from __future__ import annotations

import json
import os
import re
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
from urllib.parse import urlsplit

from app.core.diagnostics import TRACE_LIMIT, bounded_text
from app.core.runtime_log import log_event
from app.plugin_sdk.sakura_downloads import DEFAULT_PYPI_INDEX, uv_download_environment
from app.storage.atomic import atomic_write_text
from app.storage.paths import StoragePaths
from app.storage.runtime_roots import DistributionPaths


_MARKER = ".sakura-dependencies.json"
_PYPI_INDEX = "https://pypi.org/simple"
_BACKUP_PYPI_INDEX = "https://repo.huaweicloud.com/repository/pypi/simple"
_PACKAGE_FILE_NOT_FOUND = re.compile(
    r"HTTP status client error \(404\b[^)]*\) for url\s+"
    r"\((https://[^\s)]+\.whl(?:\.metadata)?)\)"
)


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
    operations. Runtime startup checks the installed marker and Python ABI;
    declaration format changes do not invalidate the installed environment.
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
    ) -> Path | None:
        with self.prepare(plugin_id, plugin_root) as staging:
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
    ) -> Iterator[Path | None]:
        """在独立目录准备依赖，插件代码在正式启动时加载。"""
        declaration = self.declaration(plugin_root)
        if declaration is None:
            yield None
            return
        parent = self._paths.plugin_dependency_roots_dir
        parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".build-", dir=parent))
        try:
            command, exported = self._install_command(declaration, staging)
            self._install_dependencies(command, Path(plugin_root), plugin_id)
            marker = {
                "schemaVersion": 1,
                "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            }
            atomic_write_text(
                staging / _MARKER,
                json.dumps(marker, ensure_ascii=False, sort_keys=True),
            )
            yield staging
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
                raise PluginDependencyError("PLUGIN_DEPENDENCIES_MISSING", f"插件 {plugin_id} 未配置发行包依赖目录")
            root = self._distribution.plugin_dependency_root_for(plugin_id)
        elif source == "user":
            root = self._paths.plugin_dependency_root_for(plugin_id)
        else:
            raise PluginDependencyError("PLUGIN_DEPENDENCY_SOURCE_INVALID")
        return self.verified_path(declaration, root)

    def verified_path(self, declaration: DependencyDeclaration | None, root: Path) -> Path | None:
        """Check an installed dependency root, including an offline migration payload."""
        if declaration is None:
            return None
        marker_path = root / _MARKER
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise PluginDependencyError("PLUGIN_DEPENDENCIES_MISSING", str(error)) from error
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PluginDependencyError("PLUGIN_DEPENDENCIES_INVALID", f"{marker_path}: {error}") from error
        expected_python = f"{sys.version_info.major}.{sys.version_info.minor}"
        if (
            not isinstance(marker, dict)
            or marker.get("schemaVersion") != 1
            or marker.get("python") != expected_python
        ):
            actual = {key: marker.get(key) for key in ("schemaVersion", "python")} if isinstance(marker, dict) else type(marker).__name__
            raise PluginDependencyError("PLUGIN_DEPENDENCIES_STALE",
                                        f"{marker_path}: expected schemaVersion=1, python={expected_python}; actual={actual}")
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

    def _install_dependencies(self, command: list[str], directory: Path, plugin_id: str) -> None:
        environment = self._uv_environment(directory)
        errors: list[str] = []
        for attempt, (index, label) in enumerate((
            (None, "首次安装"),
            (DEFAULT_PYPI_INDEX, "刷新阿里云索引后重试"),
            (_BACKUP_PYPI_INDEX, "华为云镜像兜底"),
            (_PYPI_INDEX, "官方 PyPI 兜底"),
        ), start=1):
            arguments = command
            if index is not None:
                environment = {**environment, "UV_DEFAULT_INDEX": index}
                arguments = [*command, "--refresh"]
            attributes = {
                "plugin_id": plugin_id,
                "attempt": attempt,
                "dependency_source": {
                    DEFAULT_PYPI_INDEX: "aliyun", _BACKUP_PYPI_INDEX: "huawei", _PYPI_INDEX: "pypi",
                }.get(environment.get("UV_DEFAULT_INDEX"), "configured"),
                "action": "install" if attempt == 1 else "refresh" if index == DEFAULT_PYPI_INDEX else "fallback",
            }
            log_event("Plugin", "开始安装插件依赖", attributes,
                      event="plugin.dependencies.attempt.started", severity="info")
            try:
                result = subprocess.run(
                    arguments, cwd=directory, env=environment,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, check=False,
                )
            except OSError as error:
                errors.append(f"{label}\n{error}")
                log_event("Plugin", "插件依赖安装失败",
                          {**attributes, "reason_code": "PLUGIN_DEPENDENCY_INSTALL_FAILED", "diagnostic": str(error)},
                          event="plugin.dependencies.install.failed", severity="error")
                raise PluginDependencyError("PLUGIN_DEPENDENCY_INSTALL_FAILED", "\n\n".join(errors)) from error
            if not result.returncode:
                log_event("Plugin", "插件依赖下载安装完成", {**attributes, "exit_code": 0},
                          event="plugin.dependencies.install.finished", severity="info")
                return
            output = (result.stderr or result.stdout).strip()
            errors.append(f"{label}\nexit_code={result.returncode}\n{output}")
            log_event("Plugin", "插件依赖安装尝试失败",
                      {**attributes, "exit_code": result.returncode, "diagnostic": output},
                      event="plugin.dependencies.attempt.failed", severity="debug")
            missing = _PACKAGE_FILE_NOT_FOUND.search(output)
            if not missing or urlsplit(missing[1]).netloc != urlsplit(environment.get("UV_DEFAULT_INDEX", "")).netloc:
                break
            if index is None:
                if environment.get("UV_DEFAULT_INDEX") != DEFAULT_PYPI_INDEX or environment.get("UV_EXTRA_INDEX_URL"):
                    break
                # An explicit source remains unchanged when the default is replaced.
                fallback = uv_download_environment(directory, default_index=_BACKUP_PYPI_INDEX)
                if fallback.get("UV_DEFAULT_INDEX") != _BACKUP_PYPI_INDEX:
                    break
        log_event("Plugin", "插件依赖安装失败",
                  {**attributes, "exit_code": result.returncode, "reason_code": "PLUGIN_DEPENDENCY_INSTALL_FAILED"},
                  event="plugin.dependencies.install.failed", severity="error")
        raise PluginDependencyError("PLUGIN_DEPENDENCY_INSTALL_FAILED", "\n\n".join(errors))

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


__all__ = [
    "DependencyDeclaration",
    "PluginDependencyError",
    "PluginDependencyRoots",
]
