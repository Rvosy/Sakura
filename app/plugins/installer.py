"""Bounded local ZIP/folder installation for trusted Plugin API v3 code."""

from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
import time
import unicodedata
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterator

import yaml

from app.plugins.discovery import (
    PluginDiscovery,
    plugin_spec_from_manifest,
)
from app.plugins.models import PLUGIN_API_V3_VERSION, PluginSpec
from app.storage.atomic import atomic_write_text
from app.storage.paths import StoragePaths, sanitize_directory_component


MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_PLUGIN_BYTES = 32 * 1024 * 1024
MAX_PLUGIN_FILE_BYTES = 16 * 1024 * 1024
MAX_PLUGIN_FILES = 512
MAX_PLUGIN_ENTRIES = 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_DISCOVERED_PLUGINS = 64
_PLUGIN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_SERVICE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$")
_PYTHON_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_INVALID_COMPONENT = re.compile(r'[<>:"\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_RETRYABLE_WINERRORS = {5, 32}
_IO_RETRY_ATTEMPTS = 5
_IO_RETRY_INITIAL_DELAY_SECONDS = 0.05
_MISSING = object()


class PluginInstallError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class InstalledPlugin:
    plugin_id: str
    code_dir: Path
    config_before: str | None


@dataclass(frozen=True)
class PendingPluginRemoval:
    plugin_id: str
    code_dir: Path
    quarantine_dir: Path
    config_before: str | None
    priority: int


class LocalPluginInstaller:
    """Install code without importing it; runtime activation remains explicit."""

    def __init__(self, app_root: Path) -> None:
        self._app_root = Path(app_root).resolve()
        self._paths = StoragePaths(self._app_root)

    def install(self, source: Path, source_kind: str) -> InstalledPlugin:
        source_path = Path(source)
        if not source_path.is_absolute():
            raise PluginInstallError("PLUGIN_INSTALL_SOURCE_INVALID")
        try:
            if self._unsafe_node(source_path):
                raise PluginInstallError("PLUGIN_INSTALL_SYMLINK_FORBIDDEN")
            source_path = source_path.resolve()
            if source_kind == "folder":
                if not source_path.is_dir():
                    raise PluginInstallError("PLUGIN_INSTALL_SOURCE_INVALID")
            elif source_kind == "zip":
                if not source_path.is_file() or source_path.suffix.casefold() != ".zip":
                    raise PluginInstallError("PLUGIN_INSTALL_SOURCE_INVALID")
                if source_path.stat().st_size > MAX_ARCHIVE_BYTES:
                    raise PluginInstallError("PLUGIN_INSTALL_ARCHIVE_TOO_LARGE")
            else:
                raise PluginInstallError("PLUGIN_INSTALL_SOURCE_INVALID")
        except OSError as error:
            raise PluginInstallError("PLUGIN_INSTALL_SOURCE_INVALID") from error

        destination = self._paths.user_plugins_dir
        destination.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".install-", dir=destination))
        promoted: Path | None = None
        config_before: str | None | object = _MISSING
        disabled_reserved = False
        rollback_error: PluginInstallError | None = None
        completed = False
        try:
            payload = staging / "payload"
            if source_kind == "zip":
                payload.mkdir()
                self._extract_zip(source_path, payload)
                plugin_root = self._payload_root(payload)
            else:
                plugin_root = self._payload_root(source_path)
                copied = staging / "folder"
                self._copy_folder(plugin_root, copied)
                plugin_root = copied

            spec = self._validated_spec(plugin_root)
            self._reject_conflicts(spec)
            config_before = self._read_config_text()
            target = destination / sanitize_directory_component(spec.plugin_id)
            if target.exists() or any(
                child.name.casefold() == target.name.casefold()
                for child in destination.iterdir()
                if child != staging
            ):
                raise PluginInstallError("PLUGIN_ID_CONFLICT")
            self._write_disabled_override(spec.plugin_id, spec.priority)
            disabled_reserved = True
            self._replace_path(plugin_root, target)
            promoted = target
            completed = True
            assert config_before is None or isinstance(config_before, str)
            return InstalledPlugin(spec.plugin_id, target, config_before)
        except PluginInstallError:
            raise
        except (
            OSError,
            UnicodeDecodeError,
            yaml.YAMLError,
            zipfile.BadZipFile,
            NotImplementedError,
        ) as error:
            raise PluginInstallError("PLUGIN_INSTALL_IO_FAILED") from error
        finally:
            if not completed and disabled_reserved:
                code_removed = promoted is None
                if promoted is not None:
                    try:
                        self._remove_tree_checked(
                            promoted,
                            "PLUGIN_INSTALL_ROLLBACK_FAILED",
                        )
                        code_removed = True
                    except PluginInstallError as error:
                        rollback_error = error
                if code_removed and config_before is not _MISSING:
                    try:
                        assert config_before is None or isinstance(config_before, str)
                        self._restore_config_text(config_before)
                    except PluginInstallError as error:
                        rollback_error = PluginInstallError(
                            "PLUGIN_INSTALL_ROLLBACK_FAILED"
                        )
            shutil.rmtree(staging, ignore_errors=True)
            if rollback_error is not None:
                raise rollback_error

    def uninstall(self, plugin_id: str) -> None:
        pending = self.begin_uninstall(plugin_id)
        self.commit_uninstall(pending)

    def begin_uninstall(self, plugin_id: str) -> PendingPluginRemoval:
        if not _PLUGIN_ID.fullmatch(plugin_id):
            raise PluginInstallError("PLUGIN_ID_INVALID")
        spec = next(
            (item for item in PluginDiscovery(self._app_root).discover() if item.plugin_id == plugin_id),
            None,
        )
        if spec is None:
            raise PluginInstallError("PLUGIN_NOT_FOUND")
        if spec.source != "user" or spec.plugin_root is None:
            raise PluginInstallError("BUNDLED_PLUGIN_LOCKED")
        user_root = self._paths.user_plugins_dir.resolve()
        code_dir = spec.plugin_root.resolve()
        if code_dir.parent != user_root:
            raise PluginInstallError("PLUGIN_INSTALL_LAYOUT_INVALID")

        config_before = self._read_config_text()
        quarantine = Path(tempfile.mkdtemp(prefix=".uninstall-", dir=user_root)) / "code"
        try:
            self._replace_path(code_dir, quarantine)
        except OSError as error:
            shutil.rmtree(quarantine.parent, ignore_errors=True)
            raise PluginInstallError("PLUGIN_UNINSTALL_FAILED") from error
        try:
            self._remove_config_entry(plugin_id)
        except PluginInstallError:
            try:
                self._replace_path(quarantine, code_dir)
            except OSError as error:
                raise PluginInstallError("PLUGIN_UNINSTALL_ROLLBACK_FAILED") from error
            shutil.rmtree(quarantine.parent, ignore_errors=True)
            raise
        return PendingPluginRemoval(
            plugin_id,
            code_dir,
            quarantine,
            config_before,
            spec.priority,
        )

    def commit_uninstall(self, pending: PendingPluginRemoval) -> None:
        self._remove_tree_checked(
            pending.quarantine_dir.parent,
            "PLUGIN_UNINSTALL_CLEANUP_FAILED",
        )

    def rollback_uninstall(self, pending: PendingPluginRemoval) -> None:
        config_restored = False
        restore_error: PluginInstallError | None = None
        try:
            self._restore_config_text(pending.config_before)
            config_restored = True
        except PluginInstallError:
            try:
                self._write_disabled_override(pending.plugin_id, pending.priority)
            except PluginInstallError:
                restore_error = PluginInstallError("PLUGIN_UNINSTALL_ROLLBACK_FAILED")
        if config_restored or restore_error is None:
            try:
                if pending.quarantine_dir.is_dir() and not pending.code_dir.exists():
                    self._replace_path(pending.quarantine_dir, pending.code_dir)
            except OSError as error:
                restore_error = PluginInstallError("PLUGIN_UNINSTALL_ROLLBACK_FAILED")
        if pending.code_dir.is_dir() and not pending.quarantine_dir.exists():
            shutil.rmtree(pending.quarantine_dir.parent, ignore_errors=True)
        else:
            restore_error = PluginInstallError("PLUGIN_UNINSTALL_ROLLBACK_FAILED")
        if not config_restored:
            restore_error = PluginInstallError("PLUGIN_UNINSTALL_ROLLBACK_FAILED")
        if restore_error is not None:
            raise restore_error

    def remove_installed_code(self, installed: InstalledPlugin) -> None:
        """Rollback without ever removing the disabled guard before code is gone."""

        user_root = self._paths.user_plugins_dir.resolve()
        try:
            code_dir = installed.code_dir.resolve()
        except OSError as error:
            raise PluginInstallError("PLUGIN_INSTALL_ROLLBACK_FAILED") from error
        if code_dir.parent != user_root:
            raise PluginInstallError("PLUGIN_INSTALL_ROLLBACK_FAILED")
        if code_dir.exists():
            self._remove_tree_checked(code_dir, "PLUGIN_INSTALL_ROLLBACK_FAILED")
        if code_dir.exists():
            raise PluginInstallError("PLUGIN_INSTALL_ROLLBACK_FAILED")
        try:
            self._restore_config_text(installed.config_before)
        except PluginInstallError as error:
            raise PluginInstallError("PLUGIN_INSTALL_ROLLBACK_FAILED") from error

    def _read_config_text(self) -> str | None:
        path = self._paths.plugins_config()
        try:
            return path.read_text(encoding="utf-8") if path.is_file() else None
        except (OSError, UnicodeDecodeError) as error:
            raise PluginInstallError("PLUGIN_CONFIG_INVALID") from error

    def _restore_config_text(self, content: str | None) -> None:
        path = self._paths.plugins_config()
        if content is None:
            if not path.exists():
                return
            try:
                path.unlink()
            except OSError as error:
                raise PluginInstallError("PLUGIN_CONFIG_INVALID") from error
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(path, content)
        except OSError as error:
            raise PluginInstallError("PLUGIN_CONFIG_INVALID") from error

    def _write_disabled_override(self, plugin_id: str, priority: int) -> None:
        path = self._paths.plugins_config()
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else []
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
            raise PluginInstallError("PLUGIN_CONFIG_INVALID") from error
        if raw is not None and not isinstance(raw, list):
            raise PluginInstallError("PLUGIN_CONFIG_INVALID")
        next_entries: list[object] = []
        inserted = False
        for item in list(raw or []):
            if (
                isinstance(item, dict)
                and isinstance(item.get("id"), str)
                and item["id"].casefold() == plugin_id.casefold()
            ):
                if inserted:
                    continue
                entry = dict(item)
                entry["id"] = plugin_id
                entry["enabled"] = False
                entry["required"] = False
                try:
                    entry["priority"] = int(entry.get("priority", priority))
                except (TypeError, ValueError):
                    entry["priority"] = int(priority)
                next_entries.append(entry)
                inserted = True
            else:
                next_entries.append(item)
        if not inserted:
            next_entries.append(
                {
                    "id": plugin_id,
                    "enabled": False,
                    "required": False,
                    "priority": int(priority),
                }
            )
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(
                path,
                yaml.safe_dump(next_entries, allow_unicode=True, sort_keys=False),
            )
        except OSError as error:
            raise PluginInstallError("PLUGIN_CONFIG_INVALID") from error

    def _remove_config_entry(self, plugin_id: str) -> None:
        path = self._paths.plugins_config()
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else []
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
            raise PluginInstallError("PLUGIN_CONFIG_INVALID") from error
        if raw is not None and not isinstance(raw, list):
            raise PluginInstallError("PLUGIN_CONFIG_INVALID")
        entries = list(raw or [])
        filtered = [
            item
            for item in entries
            if not (
                isinstance(item, dict)
                and isinstance(item.get("id"), str)
                and item["id"].casefold() == plugin_id.casefold()
            )
        ]
        if filtered == entries:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(
                path,
                yaml.safe_dump(filtered, allow_unicode=True, sort_keys=False),
            )
        except OSError as error:
            raise PluginInstallError("PLUGIN_CONFIG_INVALID") from error

    def _payload_root(self, root: Path) -> Path:
        if self._unsafe_node(root) or not root.is_dir():
            raise PluginInstallError("PLUGIN_INSTALL_LAYOUT_INVALID")
        if (root / "plugin.yaml").is_file():
            return root
        candidates: list[Path] = []
        for index, child in enumerate(root.iterdir(), start=1):
            if index > MAX_PLUGIN_ENTRIES:
                raise PluginInstallError("PLUGIN_INSTALL_TOO_MANY_FILES")
            if child.is_dir() and not self._unsafe_node(child) and (child / "plugin.yaml").is_file():
                candidates.append(child)
        if len(candidates) != 1:
            raise PluginInstallError("PLUGIN_INSTALL_LAYOUT_INVALID")
        return candidates[0]

    def _validated_spec(self, plugin_root: Path) -> PluginSpec:
        manifest_path = plugin_root / "plugin.yaml"
        try:
            if not manifest_path.is_file() or manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
                raise PluginInstallError("PLUGIN_MANIFEST_INVALID")
            raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
            raise PluginInstallError("PLUGIN_MANIFEST_INVALID") from error
        if not isinstance(raw, dict):
            raise PluginInstallError("PLUGIN_MANIFEST_INVALID")
        self._validate_manifest_shape(raw)
        spec = plugin_spec_from_manifest(raw, plugin_root, source="user")
        if spec is None or spec.api_version != PLUGIN_API_V3_VERSION:
            raise PluginInstallError("API_VERSION_UNSUPPORTED")
        if (
            not _PLUGIN_ID.fullmatch(spec.plugin_id)
            or spec.plugin_id.endswith(".")
            or spec.required
        ):
            raise PluginInstallError("PLUGIN_MANIFEST_INVALID")
        if any(not _SERVICE_KEY.fullmatch(key) for key in (*spec.provides, *spec.requires, *spec.optional)):
            raise PluginInstallError("PLUGIN_MANIFEST_INVALID")
        module_name, separator, class_name = spec.entry.partition(":")
        module_parts = module_name.split(".")
        if (
            separator != ":"
            or not module_parts
            or any(not _PYTHON_NAME.fullmatch(part) for part in module_parts)
            or not _PYTHON_NAME.fullmatch(class_name)
            or not plugin_root.joinpath(*module_parts).with_suffix(".py").is_file()
        ):
            raise PluginInstallError("PLUGIN_ENTRY_INVALID")
        manifests = [path for path in plugin_root.rglob("plugin.yaml") if path.is_file()]
        if manifests != [manifest_path]:
            raise PluginInstallError("PLUGIN_INSTALL_LAYOUT_INVALID")
        self._validate_folder(plugin_root)
        return spec

    @staticmethod
    def _validate_manifest_shape(raw: dict[str, object]) -> None:
        for key in ("id", "plugin_id", "entry", "name", "author", "description", "version"):
            if key in raw and not isinstance(raw[key], str):
                raise PluginInstallError("PLUGIN_MANIFEST_INVALID")
        for key in ("api", "api_version", "priority"):
            if key in raw and (not isinstance(raw[key], int) or isinstance(raw[key], bool)):
                raise PluginInstallError("PLUGIN_MANIFEST_INVALID")
        for key in ("enabled", "required"):
            if key in raw and not isinstance(raw[key], bool):
                raise PluginInstallError("PLUGIN_MANIFEST_INVALID")
        for key in ("permissions", "provides", "requires", "optional"):
            if key not in raw:
                continue
            value = raw[key]
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise PluginInstallError("PLUGIN_MANIFEST_INVALID")

    def _reject_conflicts(self, spec: PluginSpec) -> None:
        existing = PluginDiscovery(self._app_root).discover()
        if any(item.plugin_id.casefold() == spec.plugin_id.casefold() for item in existing):
            raise PluginInstallError("PLUGIN_ID_CONFLICT")
        if len(existing) >= MAX_DISCOVERED_PLUGINS:
            raise PluginInstallError("PLUGIN_INSTALL_TOO_MANY_PLUGINS")
        try:
            raw_config = yaml.safe_load(self._paths.plugins_config().read_text(encoding="utf-8"))
        except FileNotFoundError:
            raw_config = []
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
            raise PluginInstallError("PLUGIN_CONFIG_INVALID") from error
        if raw_config is not None and not isinstance(raw_config, list):
            raise PluginInstallError("PLUGIN_CONFIG_INVALID")

    def _copy_folder(self, source: Path, destination: Path) -> None:
        try:
            root_stat = source.lstat()
        except OSError as error:
            raise PluginInstallError("PLUGIN_INSTALL_IO_FAILED") from error
        if self._unsafe_node(source):
            raise PluginInstallError("PLUGIN_INSTALL_SYMLINK_FORBIDDEN")
        if not stat.S_ISDIR(root_stat.st_mode):
            raise PluginInstallError("PLUGIN_INSTALL_LAYOUT_INVALID")
        destination.mkdir()
        files = 0
        entries = 0
        total = 0
        seen: set[str] = set()
        for path in source.rglob("*"):
            entries += 1
            if entries > MAX_PLUGIN_ENTRIES:
                raise PluginInstallError("PLUGIN_INSTALL_TOO_MANY_FILES")
            relative = path.relative_to(source)
            self._validate_parts(relative.parts)
            key = unicodedata.normalize("NFC", "/".join(relative.parts)).casefold()
            if key in seen:
                raise PluginInstallError("PLUGIN_INSTALL_PATH_CONFLICT")
            seen.add(key)
            target = destination / relative
            try:
                node_stat = path.lstat()
            except OSError as error:
                raise PluginInstallError("PLUGIN_INSTALL_IO_FAILED") from error
            if self._unsafe_node(path) or stat.S_ISLNK(node_stat.st_mode):
                raise PluginInstallError("PLUGIN_INSTALL_SYMLINK_FORBIDDEN")
            if stat.S_ISDIR(node_stat.st_mode):
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not stat.S_ISREG(node_stat.st_mode):
                raise PluginInstallError("PLUGIN_INSTALL_FILE_TYPE_INVALID")
            files += 1
            if files > MAX_PLUGIN_FILES:
                raise PluginInstallError("PLUGIN_INSTALL_TOO_MANY_FILES")
            target.parent.mkdir(parents=True, exist_ok=True)
            with self._open_regular_source(path) as (reader, declared_size):
                if (
                    declared_size > MAX_PLUGIN_FILE_BYTES
                    or total + declared_size > MAX_PLUGIN_BYTES
                ):
                    raise PluginInstallError("PLUGIN_INSTALL_TOO_LARGE")
                remaining = min(
                    MAX_PLUGIN_FILE_BYTES,
                    MAX_PLUGIN_BYTES - total,
                )
                with target.open("xb") as writer:
                    total += self._copy_bounded(reader, writer, remaining)

    def _validate_folder(self, root: Path) -> None:
        files = 0
        entries = 0
        total = 0
        seen: set[str] = set()
        for path in root.rglob("*"):
            entries += 1
            if entries > MAX_PLUGIN_ENTRIES:
                raise PluginInstallError("PLUGIN_INSTALL_TOO_MANY_FILES")
            if self._unsafe_node(path):
                raise PluginInstallError("PLUGIN_INSTALL_SYMLINK_FORBIDDEN")
            relative = path.relative_to(root)
            self._validate_parts(relative.parts)
            key = unicodedata.normalize("NFC", "/".join(relative.parts)).casefold()
            if key in seen:
                raise PluginInstallError("PLUGIN_INSTALL_PATH_CONFLICT")
            seen.add(key)
            if path.is_dir():
                continue
            if not path.is_file():
                raise PluginInstallError("PLUGIN_INSTALL_FILE_TYPE_INVALID")
            files += 1
            size = path.stat().st_size
            total += size
            if files > MAX_PLUGIN_FILES:
                raise PluginInstallError("PLUGIN_INSTALL_TOO_MANY_FILES")
            if size > MAX_PLUGIN_FILE_BYTES or total > MAX_PLUGIN_BYTES:
                raise PluginInstallError("PLUGIN_INSTALL_TOO_LARGE")

    def _extract_zip(self, source: Path, destination: Path) -> None:
        with self._open_regular_source(source) as (source_file, archive_size):
            if archive_size > MAX_ARCHIVE_BYTES:
                raise PluginInstallError("PLUGIN_INSTALL_ARCHIVE_TOO_LARGE")
            with zipfile.ZipFile(source_file) as archive:
                self._extract_zip_entries(archive, destination)

    def _extract_zip_entries(self, archive: zipfile.ZipFile, destination: Path) -> None:
        infos = archive.infolist()
        if len(infos) > MAX_PLUGIN_ENTRIES:
            raise PluginInstallError("PLUGIN_INSTALL_TOO_MANY_FILES")
        files = 0
        declared_total = 0
        actual_total = 0
        seen: set[str] = set()
        for info in infos:
            parts = self._zip_parts(info.filename)
            key = unicodedata.normalize("NFC", "/".join(parts)).casefold()
            if key in seen:
                raise PluginInstallError("PLUGIN_INSTALL_PATH_CONFLICT")
            seen.add(key)
            mode = (info.external_attr >> 16) & 0xFFFF
            file_type = stat.S_IFMT(mode)
            if stat.S_ISLNK(mode):
                raise PluginInstallError("PLUGIN_INSTALL_SYMLINK_FORBIDDEN")
            is_directory = info.is_dir()
            expected_types = {0, stat.S_IFDIR} if is_directory else {0, stat.S_IFREG}
            if file_type not in expected_types or info.flag_bits & 0x1:
                raise PluginInstallError("PLUGIN_INSTALL_FILE_TYPE_INVALID")
            target = destination.joinpath(*parts)
            if is_directory:
                target.mkdir(parents=True, exist_ok=True)
                continue
            files += 1
            declared_total += info.file_size
            if files > MAX_PLUGIN_FILES:
                raise PluginInstallError("PLUGIN_INSTALL_TOO_MANY_FILES")
            if info.file_size > MAX_PLUGIN_FILE_BYTES or declared_total > MAX_PLUGIN_BYTES:
                raise PluginInstallError("PLUGIN_INSTALL_TOO_LARGE")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as reader, target.open("xb") as writer:
                remaining = min(
                    MAX_PLUGIN_FILE_BYTES,
                    MAX_PLUGIN_BYTES - actual_total,
                )
                actual_total += self._copy_bounded(reader, writer, remaining)

    @contextmanager
    def _open_regular_source(self, path: Path) -> Iterator[tuple[BinaryIO, int]]:
        try:
            before = path.lstat()
        except OSError as error:
            raise PluginInstallError("PLUGIN_INSTALL_IO_FAILED") from error
        if self._unsafe_node(path) or stat.S_ISLNK(before.st_mode):
            raise PluginInstallError("PLUGIN_INSTALL_SYMLINK_FORBIDDEN")
        if not stat.S_ISREG(before.st_mode):
            raise PluginInstallError("PLUGIN_INSTALL_FILE_TYPE_INVALID")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as error:
            raise PluginInstallError("PLUGIN_INSTALL_IO_FAILED") from error
        try:
            opened = os.fstat(descriptor)
            current = path.lstat()
            if (
                not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(current.st_mode)
                or self._file_identity(before) != self._file_identity(opened)
                or self._file_identity(opened) != self._file_identity(current)
            ):
                raise PluginInstallError("PLUGIN_INSTALL_SOURCE_CHANGED")
            with os.fdopen(descriptor, "rb", closefd=True) as stream:
                descriptor = -1
                yield stream, int(opened.st_size)
        except OSError as error:
            raise PluginInstallError("PLUGIN_INSTALL_IO_FAILED") from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    @staticmethod
    def _file_identity(value: os.stat_result) -> tuple[int, int, int]:
        inode = int(value.st_ino)
        fallback = int(getattr(value, "st_ctime_ns", int(value.st_ctime * 1_000_000_000)))
        return (int(value.st_dev), inode if inode else fallback, stat.S_IFMT(value.st_mode))

    @staticmethod
    def _replace_path(source: Path, target: Path) -> None:
        for attempt in range(_IO_RETRY_ATTEMPTS):
            try:
                os.replace(source, target)
                return
            except OSError as error:
                if (
                    getattr(error, "winerror", None) not in _RETRYABLE_WINERRORS
                    or attempt == _IO_RETRY_ATTEMPTS - 1
                ):
                    raise
                time.sleep(_IO_RETRY_INITIAL_DELAY_SECONDS * (2**attempt))

    @staticmethod
    def _remove_tree_checked(path: Path, code: str) -> None:
        for attempt in range(_IO_RETRY_ATTEMPTS):
            try:
                shutil.rmtree(path)
            except FileNotFoundError:
                return
            except OSError as error:
                if (
                    getattr(error, "winerror", None) not in _RETRYABLE_WINERRORS
                    or attempt == _IO_RETRY_ATTEMPTS - 1
                ):
                    raise PluginInstallError(code) from error
                time.sleep(_IO_RETRY_INITIAL_DELAY_SECONDS * (2**attempt))
            else:
                if not path.exists():
                    return
        raise PluginInstallError(code)

    @staticmethod
    def _copy_bounded(reader: BinaryIO, writer: BinaryIO, remaining: int) -> int:
        written = 0
        while True:
            chunk = reader.read(min(64 * 1024, remaining - written + 1))
            if not chunk:
                return written
            written += len(chunk)
            if written > remaining:
                raise PluginInstallError("PLUGIN_INSTALL_TOO_LARGE")
            writer.write(chunk)

    @staticmethod
    def _zip_parts(name: str) -> tuple[str, ...]:
        if not name or "\\" in name or "\x00" in name:
            raise PluginInstallError("PLUGIN_INSTALL_PATH_INVALID")
        path = PurePosixPath(name)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise PluginInstallError("PLUGIN_INSTALL_PATH_INVALID")
        LocalPluginInstaller._validate_parts(path.parts)
        return path.parts

    @staticmethod
    def _validate_parts(parts: tuple[str, ...]) -> None:
        if any(
            not part
            or part in {".", ".."}
            or part.endswith((" ", "."))
            or _INVALID_COMPONENT.search(part)
            or part.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES
            or len(part.encode("utf-8")) > 240
            for part in parts
        ):
            raise PluginInstallError("PLUGIN_INSTALL_PATH_INVALID")

    @staticmethod
    def _unsafe_node(path: Path) -> bool:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(callable(is_junction) and is_junction())


__all__ = [
    "InstalledPlugin",
    "LocalPluginInstaller",
    "PendingPluginRemoval",
    "PluginInstallError",
]
