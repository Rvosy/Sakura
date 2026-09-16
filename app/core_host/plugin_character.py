"""Plugin-scoped access to opaque Character extensions and package resources."""

from __future__ import annotations

from copy import deepcopy
import json
import threading
from pathlib import Path
from typing import Any, Mapping

from app.config.character_loader import CharacterConfigError, CharacterRegistry
from app.config.settings_service import AppSettingsService
from app.storage.atomic import atomic_write_text


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
        self._active_character_id = self._load_active_character_id()

    def set_current(self, character_id: str) -> None:
        with self._lock:
            self._active_character_id = character_id

    def get(self, plugin_id: str, character_id: str) -> dict[str, Any]:
        with self._lock:
            _path, manifest = self._manifest(character_id)
            extensions = self._extensions(manifest)
            value = extensions.get(plugin_id, {})
            if not isinstance(value, Mapping):
                raise PluginCharacterError("CHARACTER_EXTENSION_INVALID")
            return _clone_object(value)

    def current(self, plugin_id: str) -> dict[str, str]:
        if not isinstance(plugin_id, str) or not plugin_id:
            raise PluginCharacterError("PLUGIN_ID_INVALID")
        with self._lock:
            character_id = self._active_character_id
            if character_id is None:
                raise PluginCharacterError("CHARACTER_NOT_FOUND")
            registry = CharacterRegistry(self._app_root)
            profile = registry.get(character_id)
            try:
                prompt = profile.card_path.read_text(encoding="utf-8").strip()
            except OSError as error:
                raise PluginCharacterError("CHARACTER_RESOURCE_INVALID") from error
            if not prompt:
                raise PluginCharacterError("CHARACTER_RESOURCE_INVALID")
            return {"id": profile.id, "systemPrompt": prompt}

    def _load_active_character_id(self) -> str | None:
        try:
            registry = CharacterRegistry(self._app_root)
            return AppSettingsService(self._app_root).load_current_character_id(registry)
        except (CharacterConfigError, OSError, ValueError):
            return None

    def update(
        self,
        plugin_id: str,
        character_id: str,
        values: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(values, Mapping):
            raise PluginCharacterError("CHARACTER_EXTENSION_INVALID")
        patch = _clone_object(values)
        with self._lock:
            path, manifest = self._manifest(character_id)
            extensions = self._extensions(manifest)
            current = extensions.get(plugin_id, {})
            if not isinstance(current, Mapping):
                raise PluginCharacterError("CHARACTER_EXTENSION_INVALID")
            updated = _clone_object(current)
            updated.update(patch)
            extensions[plugin_id] = updated
            manifest["extensions"] = extensions
            try:
                encoded = json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False)
            except (TypeError, ValueError) as error:
                raise PluginCharacterError("CHARACTER_EXTENSION_INVALID") from error
            atomic_write_text(path, encoded)
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
    return deepcopy(dict(value))


__all__ = ["PluginCharacterError", "PluginCharacterStore"]
