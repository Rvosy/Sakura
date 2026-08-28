"""Generation-bound binary artifacts exchanged through Plugin Host Services."""

from __future__ import annotations

import re
import secrets
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from app.storage.paths import StoragePaths, sanitize_directory_component


MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_ARTIFACTS_PER_PLUGIN = 16
_MEDIA_TYPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,63}/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,63}$")
_SUFFIX = re.compile(r"^\.[A-Za-z0-9]{1,10}$")


class PluginArtifactError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class _Artifact:
    artifact_id: str
    plugin_id: str
    path: Path
    media_type: str
    committed: bool = False
    byte_length: int = 0


@dataclass(frozen=True)
class CommittedPluginArtifact:
    artifact_id: str
    plugin_id: str
    path: Path
    media_type: str
    byte_length: int


class PluginArtifactStore:
    """Own bounded temporary files for exactly one Core generation."""

    def __init__(self, app_root: Path, generation_id: str) -> None:
        self._root = StoragePaths(app_root).plugin_artifacts_generation_dir(generation_id)
        self._lock = threading.RLock()
        self._artifacts: dict[str, _Artifact] = {}

    def allocate(self, plugin_id: str, descriptor: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(descriptor, Mapping) or any(
            key not in {"mediaType", "suffix"} for key in descriptor
        ):
            raise PluginArtifactError("ARTIFACT_DESCRIPTOR_INVALID")
        media_type = descriptor.get("mediaType")
        suffix = descriptor.get("suffix", ".bin")
        if (
            not isinstance(media_type, str)
            or not _MEDIA_TYPE.fullmatch(media_type)
            or not isinstance(suffix, str)
            or not _SUFFIX.fullmatch(suffix)
        ):
            raise PluginArtifactError("ARTIFACT_DESCRIPTOR_INVALID")
        with self._lock:
            if sum(item.plugin_id == plugin_id for item in self._artifacts.values()) >= MAX_ARTIFACTS_PER_PLUGIN:
                raise PluginArtifactError("ARTIFACT_LIMIT_EXCEEDED")
            artifact_id = ""
            while not artifact_id or artifact_id in self._artifacts:
                artifact_id = f"artifact_{secrets.token_hex(16)}"
            plugin_root = self._root / sanitize_directory_component(plugin_id)
            directory = plugin_root / artifact_id
            directory.mkdir(parents=True, exist_ok=False)
            path = directory / f"payload{suffix.lower()}"
            self._artifacts[artifact_id] = _Artifact(
                artifact_id,
                plugin_id,
                path,
                media_type,
            )
        return {
            "artifactId": artifact_id,
            "path": str(path),
            "mediaType": media_type,
        }

    def commit(self, plugin_id: str, artifact_id: str) -> dict[str, Any]:
        with self._lock:
            artifact = self._owned(plugin_id, artifact_id)
            if artifact.committed:
                return self._descriptor(artifact)
            try:
                root = self._root.resolve(strict=True)
                directory = artifact.path.parent
                resolved_directory = directory.resolve(strict=True)
                resolved_path = artifact.path.resolve(strict=True)
                resolved_directory.relative_to(root)
                resolved_path.relative_to(resolved_directory)
                if directory.is_symlink() or artifact.path.is_symlink() or not artifact.path.is_file():
                    raise OSError("artifact is not a regular file")
                byte_length = artifact.path.stat().st_size
            except (OSError, ValueError) as error:
                raise PluginArtifactError("ARTIFACT_INVALID") from error
            if byte_length <= 0 or byte_length > MAX_ARTIFACT_BYTES:
                raise PluginArtifactError("ARTIFACT_SIZE_INVALID")
            artifact.committed = True
            artifact.byte_length = byte_length
            return self._descriptor(artifact)

    def release(self, plugin_id: str, artifact_id: str) -> bool:
        with self._lock:
            artifact = self._artifacts.get(artifact_id)
            if artifact is None:
                return False
            if artifact.plugin_id != plugin_id:
                raise PluginArtifactError("ARTIFACT_NOT_FOUND")
            del self._artifacts[artifact_id]
        shutil.rmtree(artifact.path.parent, ignore_errors=True)
        return True

    def release_plugin(self, plugin_id: str) -> int:
        """Release every artifact still owned by one departed plugin scope."""

        with self._lock:
            owned = [
                artifact
                for artifact in self._artifacts.values()
                if artifact.plugin_id == plugin_id
            ]
            for artifact in owned:
                self._artifacts.pop(artifact.artifact_id, None)
        for artifact in owned:
            shutil.rmtree(artifact.path.parent, ignore_errors=True)
        return len(owned)

    def resolve_committed(self, plugin_id: str, artifact_id: str) -> CommittedPluginArtifact:
        with self._lock:
            artifact = self._owned(plugin_id, artifact_id)
            if not artifact.committed:
                raise PluginArtifactError("ARTIFACT_NOT_COMMITTED")
            return CommittedPluginArtifact(
                artifact.artifact_id,
                artifact.plugin_id,
                artifact.path,
                artifact.media_type,
                artifact.byte_length,
            )

    def resolve_committed_by_id(self, artifact_id: str) -> CommittedPluginArtifact:
        """Resolve an opaque artifact for a trusted Core consumer."""

        with self._lock:
            if not isinstance(artifact_id, str) or not artifact_id.startswith("artifact_"):
                raise PluginArtifactError("ARTIFACT_NOT_FOUND")
            artifact = self._artifacts.get(artifact_id)
            if artifact is None:
                raise PluginArtifactError("ARTIFACT_NOT_FOUND")
            if not artifact.committed:
                raise PluginArtifactError("ARTIFACT_NOT_COMMITTED")
            return CommittedPluginArtifact(
                artifact.artifact_id,
                artifact.plugin_id,
                artifact.path,
                artifact.media_type,
                artifact.byte_length,
            )

    def clear(self) -> None:
        with self._lock:
            self._artifacts.clear()
            root = self._root
        shutil.rmtree(root, ignore_errors=True)

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._artifacts)

    def _owned(self, plugin_id: str, artifact_id: str) -> _Artifact:
        if not isinstance(artifact_id, str) or not artifact_id.startswith("artifact_"):
            raise PluginArtifactError("ARTIFACT_NOT_FOUND")
        artifact = self._artifacts.get(artifact_id)
        if artifact is None or artifact.plugin_id != plugin_id:
            raise PluginArtifactError("ARTIFACT_NOT_FOUND")
        return artifact

    @staticmethod
    def _descriptor(artifact: _Artifact) -> dict[str, Any]:
        return {
            "artifactId": artifact.artifact_id,
            "mediaType": artifact.media_type,
            "byteLength": artifact.byte_length,
        }


__all__ = [
    "CommittedPluginArtifact",
    "PluginArtifactError",
    "PluginArtifactStore",
]
