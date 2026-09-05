"""Core-owned Plugin API v4 inventory and desired-state storage.

Discovery deliberately keeps every installation record, including malformed and
duplicate user directories.  Only validated, uniquely selected records are
projected into ``RuntimePluginSpec`` objects for per-plugin processes.
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from app.plugins.models import PLUGIN_API_V4_VERSION, PluginSpec
from app.storage.atomic import atomic_write_text
from app.storage.paths import StoragePaths
from app.storage.runtime_roots import DistributionPaths, RuntimeRoots, coerce_runtime_roots


PLUGIN_ID_PATTERN = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,62}[A-Za-z0-9_-])?$"
)
SERVICE_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$")
_PYTHON_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class RuntimePluginSpec:
    install_id: str
    plugin_id: str
    name: str
    author: str
    description: str
    version: str
    api_version: int
    entry: str
    enabled: bool
    required: bool
    provides: tuple[str, ...]
    requires: tuple[str, ...]
    source: str
    directory_name: str

    def to_plugin_spec(self, roots: RuntimeRoots | Path) -> PluginSpec:
        resolved = coerce_runtime_roots(roots)
        root = (
            DistributionPaths(resolved.distribution_root).builtin_plugins_dir
            / self.directory_name
            if self.source == "bundled"
            else StoragePaths(resolved.user_root).user_plugins_dir / self.directory_name
        )
        return PluginSpec(
            entry=self.entry,
            enabled=self.enabled,
            plugin_id=self.plugin_id,
            name=self.name,
            author=self.author,
            description=self.description,
            version=self.version,
            api_version=self.api_version,
            required=self.required,
            provides=self.provides,
            requires=self.requires,
            plugin_root=root,
            source=self.source,
        )

    def private_dict(self) -> dict[str, Any]:
        return {
            "installId": self.install_id,
            "pluginId": self.plugin_id,
            "name": self.name,
            "author": self.author,
            "description": self.description,
            "version": self.version,
            "apiVersion": self.api_version,
            "entry": self.entry,
            "enabled": self.enabled,
            "required": self.required,
            "provides": list(self.provides),
            "requires": list(self.requires),
            "source": self.source,
            "directoryName": self.directory_name,
        }

    @classmethod
    def from_private_dict(cls, value: Mapping[str, Any]) -> "RuntimePluginSpec":
        expected = {
            "installId", "pluginId", "name", "author", "description", "version", "apiVersion",
            "entry", "enabled", "required", "provides", "requires",
            "source", "directoryName",
        }
        if set(value) != expected:
            raise ValueError("PLUGIN_RUNTIME_SPEC_INVALID")
        plugin_id = value.get("pluginId")
        source = value.get("source")
        directory_name = value.get("directoryName")
        if (
            not isinstance(plugin_id, str)
            or not PLUGIN_ID_PATTERN.fullmatch(plugin_id)
            or source not in {"bundled", "user"}
            or not isinstance(directory_name, str)
            or not directory_name
            or Path(directory_name).name != directory_name
        ):
            raise ValueError("PLUGIN_RUNTIME_SPEC_INVALID")
        services: dict[str, tuple[str, ...]] = {}
        for key in ("provides", "requires"):
            raw = value.get(key)
            if (
                not isinstance(raw, list)
                or any(not isinstance(item, str) or not SERVICE_KEY_PATTERN.fullmatch(item) for item in raw)
            ):
                raise ValueError("PLUGIN_RUNTIME_SPEC_INVALID")
            services[key] = tuple(dict.fromkeys(raw))
        strings = {}
        for key, maximum in (
            ("installId", 40), ("name", 120), ("author", 120),
            ("description", 500), ("version", 64), ("entry", 200),
        ):
            raw = value.get(key)
            if not isinstance(raw, str) or len(raw) > maximum:
                raise ValueError("PLUGIN_RUNTIME_SPEC_INVALID")
            strings[key] = raw
        if (
            value.get("apiVersion") != PLUGIN_API_V4_VERSION
            or not isinstance(value.get("enabled"), bool)
            or not isinstance(value.get("required"), bool)
        ):
            raise ValueError("PLUGIN_RUNTIME_SPEC_INVALID")
        if source == "user" and value["required"]:
            raise ValueError("PLUGIN_RUNTIME_SPEC_INVALID")
        return cls(
            install_id=strings["installId"],
            plugin_id=plugin_id,
            name=strings["name"],
            author=strings["author"],
            description=strings["description"],
            version=strings["version"],
            api_version=int(value["apiVersion"]),
            entry=strings["entry"],
            enabled=value["enabled"],
            required=value["required"],
            provides=services["provides"],
            requires=services["requires"],
            source=source,
            directory_name=directory_name,
        )


@dataclass(frozen=True)
class InstalledPluginRecord:
    install_id: str
    source: str
    directory_name: str
    plugin_id: str | None
    name: str
    author: str
    description: str
    version: str
    api_version: int | None
    entry: str
    desired_enabled: bool
    required: bool
    provides: tuple[str, ...]
    requires: tuple[str, ...]
    reason_code: str
    supported: bool
    runtime_eligible: bool
    presentation_kind: str = "extension"
    presentation_category: str = "other"
    presentation_icon: str = ""

    @property
    def can_uninstall(self) -> bool:
        return self.source == "user"

    def runtime_spec(self) -> RuntimePluginSpec | None:
        if not self.runtime_eligible or self.plugin_id is None:
            return None
        return RuntimePluginSpec(
            install_id=self.install_id,
            plugin_id=self.plugin_id,
            name=self.name,
            author=self.author,
            description=self.description,
            version=self.version,
            api_version=int(self.api_version or 0),
            entry=self.entry,
            enabled=self.desired_enabled,
            required=self.required,
            provides=self.provides,
            requires=self.requires,
            source=self.source,
            directory_name=self.directory_name,
        )


@dataclass(frozen=True)
class PluginInventorySnapshot:
    records: tuple[InstalledPluginRecord, ...]
    runtime_specs: tuple[RuntimePluginSpec, ...]
    revision: str

    def record(self, install_id: str) -> InstalledPluginRecord | None:
        return next((item for item in self.records if item.install_id == install_id), None)


class PluginDesiredStateStore:
    """The Core-owned canonical ``plugins.yaml`` writer."""

    def __init__(self, app_root: Path, path: Path | None = None) -> None:
        self._path = path or StoragePaths(app_root).plugins_config()

    @property
    def path(self) -> Path:
        return self._path

    def read(self) -> dict[str, bool]:
        try:
            raw = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeDecodeError, yaml.YAMLError, ValueError) as exc:
            raise ValueError("PLUGIN_CONFIG_INVALID") from exc
        if not isinstance(raw, list):
            raise ValueError("PLUGIN_CONFIG_INVALID")
        result: dict[str, bool] = {}
        for item in raw:
            if not isinstance(item, Mapping) or set(item) != {"id", "enabled"}:
                raise ValueError("PLUGIN_CONFIG_INVALID")
            plugin_id = item.get("id")
            enabled = item.get("enabled")
            if (
                not isinstance(plugin_id, str)
                or not PLUGIN_ID_PATTERN.fullmatch(plugin_id)
                or not isinstance(enabled, bool)
                or plugin_id in result
            ):
                raise ValueError("PLUGIN_CONFIG_INVALID")
            result[plugin_id] = enabled
        return result

    def write(self, enabled_by_id: Mapping[str, bool]) -> bool:
        entries = [
            {"id": plugin_id, "enabled": bool(enabled_by_id[plugin_id])}
            for plugin_id in sorted(enabled_by_id, key=str.casefold)
            if PLUGIN_ID_PATTERN.fullmatch(plugin_id)
        ]
        next_text = yaml.safe_dump(entries, allow_unicode=True, sort_keys=False)
        try:
            previous = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            previous = ""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if previous == next_text:
            return False
        atomic_write_text(self._path, next_text)
        return True

    def set(self, plugin_id: str, enabled: bool) -> bool:
        if not PLUGIN_ID_PATTERN.fullmatch(plugin_id):
            raise ValueError("PLUGIN_ID_INVALID")
        desired = self.read()
        desired[plugin_id] = bool(enabled)
        return self.write(desired)


class PluginInventory:
    def __init__(
        self,
        roots: RuntimeRoots | Path,
        desired: PluginDesiredStateStore | None = None,
    ) -> None:
        self._roots = coerce_runtime_roots(roots)
        self._distribution = DistributionPaths(self._roots.distribution_root)
        self._paths = StoragePaths(self._roots.user_root)
        self._desired = desired or PluginDesiredStateStore(self._roots.user_root)

    def scan(self) -> PluginInventorySnapshot:
        desired = self._desired.read()
        records: list[InstalledPluginRecord] = []
        roots = (
            ("bundled", self._distribution.builtin_plugins_dir),
            ("user", self._paths.user_plugins_dir),
        )
        for source, root in roots:
            if not root.is_dir():
                continue
            for directory in sorted(root.iterdir(), key=lambda path: path.name.casefold()):
                if directory.name.startswith("."):
                    continue
                # The bundled root is also a Python package and may retain
                # auxiliary directories across an in-place upgrade.
                # Only a directory that declares a manifest is a bundled
                # installation.  The user root is different: every directory
                # is user-owned installation state, so malformed/missing
                # manifests must remain visible and uninstallable.
                if source == "bundled" and not (directory / "plugin.yaml").is_file():
                    continue
                if _unsafe_directory(directory):
                    records.append(
                        _invalid_record(
                            _install_id(source, directory.name),
                            source,
                            directory.name,
                        )
                    )
                    continue
                if not directory.is_dir():
                    continue
                records.append(self._record(source, directory, desired))

        records = self._resolve_duplicates(records)
        runtime_specs = tuple(
            spec
            for record in records
            if (spec := record.runtime_spec()) is not None
        )
        digest = hashlib.sha256()
        try:
            digest.update(self._desired.path.read_bytes())
        except OSError:
            pass
        for record in records:
            digest.update(json.dumps({
                "installId": record.install_id,
                "pluginId": record.plugin_id,
                "reason": record.reason_code,
                "enabled": record.desired_enabled,
            }, sort_keys=True).encode("utf-8"))
            manifest = self._manifest_path(record)
            try:
                digest.update(manifest.read_bytes())
            except OSError:
                digest.update(b"<missing>")
        return PluginInventorySnapshot(tuple(records), runtime_specs, digest.hexdigest()[:16])

    def _record(
        self,
        source: str,
        directory: Path,
        desired: Mapping[str, bool],
    ) -> InstalledPluginRecord:
        install_id = _install_id(source, directory.name)
        manifest = directory / "plugin.yaml"
        try:
            raw = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError, ValueError):
            return _invalid_record(install_id, source, directory.name)
        if not isinstance(raw, Mapping):
            return _invalid_record(install_id, source, directory.name)

        if "plugin_id" in raw or "api_version" in raw:
            return _invalid_record(install_id, source, directory.name)
        plugin_id = raw.get("id")
        entry = raw.get("entry")
        api_version = raw.get("api")
        if not isinstance(plugin_id, str) or not PLUGIN_ID_PATTERN.fullmatch(plugin_id):
            return _invalid_record(install_id, source, directory.name)
        if (
            ("enabled" in raw and not isinstance(raw.get("enabled"), bool))
            or ("required" in raw and not isinstance(raw.get("required"), bool))
        ):
            return _invalid_record(
                install_id,
                source,
                directory.name,
                plugin_id=plugin_id,
            )
        enabled = desired.get(plugin_id, raw.get("enabled", True))
        assert isinstance(enabled, bool)
        metadata = {
            "name": raw.get("name", plugin_id),
            "author": raw.get("author", ""),
            "description": raw.get("description", ""),
            "version": raw.get("version", "0.0.0"),
        }
        if (
            not isinstance(entry, str)
            or not _valid_entry(entry, directory)
            or isinstance(api_version, bool)
            or not isinstance(api_version, int)
            or any(not isinstance(value, str) for value in metadata.values())
            or len(metadata["name"]) > 120
            or len(metadata["author"]) > 120
            or len(metadata["description"]) > 500
            or not 1 <= len(metadata["version"]) <= 64
        ):
            return replace(
                _invalid_record(install_id, source, directory.name, plugin_id=plugin_id),
                desired_enabled=enabled,
            )
        required = raw.get("required", False) if source == "bundled" else False
        assert isinstance(required, bool)
        if "optional" in raw:
            return replace(
                _invalid_record(install_id, source, directory.name, plugin_id=plugin_id),
                desired_enabled=enabled,
            )
        services = {}
        for key in ("provides", "requires"):
            value = raw.get(key, [])
            if (
                not isinstance(value, list)
                or any(not isinstance(item, str) or not SERVICE_KEY_PATTERN.fullmatch(item) for item in value)
            ):
                return replace(
                    _invalid_record(install_id, source, directory.name, plugin_id=plugin_id),
                    desired_enabled=enabled,
                )
            services[key] = tuple(dict.fromkeys(value))
        supported = api_version == PLUGIN_API_V4_VERSION
        presentation = raw.get("presentation")
        presentation = presentation if isinstance(presentation, Mapping) else {}
        kind = presentation.get("kind")
        category = presentation.get("category")
        icon = presentation.get("icon")
        return InstalledPluginRecord(
            install_id=install_id,
            source=source,
            directory_name=directory.name,
            plugin_id=plugin_id,
            name=metadata["name"],
            author=metadata["author"],
            description=metadata["description"],
            version=metadata["version"],
            api_version=api_version,
            entry=entry,
            desired_enabled=True if required else enabled,
            required=required,
            provides=services["provides"],
            requires=services["requires"],
            reason_code="READY" if supported else "API_VERSION_UNSUPPORTED",
            supported=supported,
            runtime_eligible=supported,
            presentation_kind=kind if kind in ("extension", "provider", "infrastructure") else "extension",
            presentation_category=category if category in ("model", "voice", "memory", "tools", "connectivity", "other") else "other",
            presentation_icon=icon if isinstance(icon, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,63}", icon) else "",
        )

    @staticmethod
    def _resolve_duplicates(records: Sequence[InstalledPluginRecord]) -> list[InstalledPluginRecord]:
        result = list(records)
        groups: dict[str, list[int]] = {}
        for index, record in enumerate(result):
            if record.plugin_id is not None and record.runtime_eligible:
                groups.setdefault(record.plugin_id.casefold(), []).append(index)
        for indexes in groups.values():
            if len(indexes) < 2:
                continue
            bundled = [index for index in indexes if result[index].source == "bundled"]
            winners = bundled if len(bundled) == 1 else []
            if not bundled and len(indexes) == 1:
                winners = indexes
            for index in indexes:
                if index in winners:
                    continue
                result[index] = replace(
                    result[index],
                    reason_code="PLUGIN_ID_CONFLICT",
                    runtime_eligible=False,
                )
            if len(bundled) > 1:
                for index in bundled:
                    result[index] = replace(
                        result[index],
                        reason_code="PLUGIN_ID_CONFLICT",
                        runtime_eligible=False,
                    )
        return result

    def _manifest_path(self, record: InstalledPluginRecord) -> Path:
        root = (
            self._distribution.builtin_plugins_dir
            if record.source == "bundled"
            else self._paths.user_plugins_dir
        )
        return root / record.directory_name / "plugin.yaml"


def _install_id(source: str, directory_name: str) -> str:
    digest = hashlib.sha256(f"{source}\0{directory_name}".encode("utf-8")).hexdigest()[:24]
    return f"pi_{digest}"


def _unsafe_directory(path: Path) -> bool:
    try:
        value = path.lstat()
    except OSError:
        return True
    if stat.S_ISLNK(value.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(value, "st_file_attributes", 0) & reparse_flag)


def _invalid_record(
    install_id: str,
    source: str,
    directory_name: str,
    *,
    plugin_id: str | None = None,
) -> InstalledPluginRecord:
    return InstalledPluginRecord(
        install_id=install_id,
        source=source,
        directory_name=directory_name,
        plugin_id=plugin_id,
        name=plugin_id or "Invalid plugin",
        author="",
        description="",
        version="0.0.0",
        api_version=None,
        entry="",
        desired_enabled=False,
        required=False,
        provides=(),
        requires=(),
        reason_code="PLUGIN_MANIFEST_INVALID",
        supported=False,
        runtime_eligible=False,
    )


def _valid_entry(entry: str, root: Path) -> bool:
    module_name, separator, class_name = entry.partition(":")
    parts = module_name.split(".")
    return bool(
        separator
        and parts
        and all(_PYTHON_NAME.fullmatch(part) for part in parts)
        and _PYTHON_NAME.fullmatch(class_name)
        and root.joinpath(*parts).with_suffix(".py").is_file()
    )


__all__ = [
    "InstalledPluginRecord",
    "PLUGIN_ID_PATTERN",
    "PluginDesiredStateStore",
    "PluginInventory",
    "PluginInventorySnapshot",
    "RuntimePluginSpec",
    "SERVICE_KEY_PATTERN",
]
