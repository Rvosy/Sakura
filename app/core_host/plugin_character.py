"""Plugin-scoped access to opaque Character extensions and package resources."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Mapping

from app.config.character_loader import CharacterConfigError, CharacterRegistry
from app.config.settings_service import AppSettingsService
from app.storage.atomic import atomic_write_text


MAX_CHARACTER_EXTENSIONS_BYTES = 256 * 1024
MAX_CHARACTER_EXTENSION_BYTES = 64 * 1024


class PluginCharacterError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class PluginCharacterStore:
    """Preserve Character Core fields while exposing only the caller's extension."""

    def __init__(self, app_root: Path) -> None:
        self._app_root = Path(app_root)
        self._lock = threading.RLock()
        self._manifest_paths: dict[str, Path] = {}

    def get(self, plugin_id: str, character_id: str) -> dict[str, Any]:
        with self._lock:
            _path, manifest = self._manifest(character_id)
            extensions = self._extensions(manifest)
            value = extensions.get(plugin_id, {})
            if not isinstance(value, Mapping):
                raise PluginCharacterError("CHARACTER_EXTENSION_INVALID")
            if _encoded_size(value) > MAX_CHARACTER_EXTENSION_BYTES:
                raise PluginCharacterError("CHARACTER_EXTENSION_TOO_LARGE")
            return _clone_object(value)

    def current(self, plugin_id: str) -> dict[str, str]:
        if not isinstance(plugin_id, str) or not plugin_id:
            raise PluginCharacterError("PLUGIN_ID_INVALID")
        with self._lock:
            registry = CharacterRegistry(self._app_root)
            character_id = AppSettingsService(self._app_root).load_current_character_id(
                registry
            )
            if character_id is None:
                raise PluginCharacterError("CHARACTER_NOT_FOUND")
            profile = registry.get(character_id)
            try:
                prompt = profile.card_path.read_text(encoding="utf-8").strip()
            except OSError as error:
                raise PluginCharacterError("CHARACTER_RESOURCE_INVALID") from error
            if not prompt:
                raise PluginCharacterError("CHARACTER_RESOURCE_INVALID")
            return {"id": profile.id, "systemPrompt": prompt}

    def update(
        self,
        plugin_id: str,
        character_id: str,
        values: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(values, Mapping):
            raise PluginCharacterError("CHARACTER_EXTENSION_INVALID")
        patch = _clone_object(values)
        if _encoded_size(patch) > MAX_CHARACTER_EXTENSION_BYTES:
            raise PluginCharacterError("CHARACTER_EXTENSION_TOO_LARGE")
        with self._lock:
            path, manifest = self._manifest(character_id)
            extensions = self._extensions(manifest)
            current = extensions.get(plugin_id, {})
            if not isinstance(current, Mapping):
                raise PluginCharacterError("CHARACTER_EXTENSION_INVALID")
            updated = _clone_object(current)
            updated.update(patch)
            if _encoded_size(updated) > MAX_CHARACTER_EXTENSION_BYTES:
                raise PluginCharacterError("CHARACTER_EXTENSION_TOO_LARGE")
            extensions[plugin_id] = updated
            if _encoded_size(extensions) > MAX_CHARACTER_EXTENSIONS_BYTES:
                raise PluginCharacterError("CHARACTER_EXTENSIONS_TOO_LARGE")
            manifest["extensions"] = extensions
            atomic_write_text(
                path,
                json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False),
            )
            return _clone_object(updated)

    def resolve_resource(self, character_id: str, relative_path: str) -> str:
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise PluginCharacterError("CHARACTER_RESOURCE_INVALID")
        raw = relative_path.strip()
        lexical = Path(raw)
        if (
            lexical.is_absolute()
            or lexical.drive
            or raw.startswith(("\\", "//"))
            or ".." in lexical.parts
        ):
            raise PluginCharacterError("CHARACTER_RESOURCE_INVALID")
        with self._lock:
            manifest_path = self._manifest_path(character_id)
            package_root = manifest_path.parent.resolve(strict=True)
            candidate = manifest_path.parent / lexical
            try:
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(package_root)
                cursor = manifest_path.parent
                for part in lexical.parts:
                    cursor = cursor / part
                    if cursor.is_symlink():
                        raise OSError("character resource symlinks are not supported")
            except (OSError, ValueError) as error:
                raise PluginCharacterError("CHARACTER_RESOURCE_INVALID") from error
            if not resolved.exists():
                raise PluginCharacterError("CHARACTER_RESOURCE_INVALID")
            return str(resolved)

    def _manifest(self, character_id: str) -> tuple[Path, dict[str, Any]]:
        path = self._manifest_path(character_id)
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise PluginCharacterError("CHARACTER_NOT_FOUND") from error
        if not isinstance(manifest, dict):
            raise PluginCharacterError("CHARACTER_EXTENSION_INVALID")
        return path, manifest

    def _manifest_path(self, character_id: str) -> Path:
        if not isinstance(character_id, str) or not character_id.strip():
            raise PluginCharacterError("CHARACTER_NOT_FOUND")
        cached = self._manifest_paths.get(character_id)
        if cached is not None:
            return cached
        try:
            profile = CharacterRegistry(self._app_root).get(character_id)
            path = (profile.package_dir / "character.json").resolve(strict=True)
        except (CharacterConfigError, OSError) as error:
            raise PluginCharacterError("CHARACTER_NOT_FOUND") from error
        self._manifest_paths[character_id] = path
        return path

    @staticmethod
    def _extensions(manifest: Mapping[str, Any]) -> dict[str, Any]:
        value = manifest.get("extensions", {})
        if not isinstance(value, Mapping):
            raise PluginCharacterError("CHARACTER_EXTENSION_INVALID")
        return _clone_object(value)


def _clone_object(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        encoded = json.dumps(dict(value), ensure_ascii=False, allow_nan=False)
        cloned = json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise PluginCharacterError("CHARACTER_EXTENSION_INVALID") from error
    if not isinstance(cloned, dict):
        raise PluginCharacterError("CHARACTER_EXTENSION_INVALID")
    return cloned


def _encoded_size(value: Mapping[str, Any]) -> int:
    try:
        return len(
            json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
                "utf-8"
            )
        )
    except (TypeError, ValueError) as error:
        raise PluginCharacterError("CHARACTER_EXTENSION_INVALID") from error


__all__ = ["PluginCharacterError", "PluginCharacterStore"]
