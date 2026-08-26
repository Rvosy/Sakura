"""TTS storage override owned by the Runtime v2 user root."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from app.storage.atomic import atomic_write_text


TTS_STORAGE_UNAVAILABLE: Final = "TTS_STORAGE_UNAVAILABLE"
TTS_ROOT_MISSING: Final = "TTS_ROOT_MISSING"
TTS_ROOT_NOT_DIRECTORY: Final = "TTS_ROOT_NOT_DIRECTORY"
TTS_ROOT_NOT_WRITABLE: Final = "TTS_ROOT_NOT_WRITABLE"
TTS_STORAGE_CONFIG_INVALID: Final = "TTS_STORAGE_CONFIG_INVALID"


class TtsStorageUnavailable(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(TTS_STORAGE_UNAVAILABLE)
        self.code = TTS_STORAGE_UNAVAILABLE
        self.reason_code = reason_code


@dataclass(frozen=True)
class TtsStorageSnapshot:
    user_root: Path
    tts_root: Path
    source: str
    available: bool
    reason_code: str | None

    def to_payload(self) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "userRoot": str(self.user_root),
            "ttsRoot": str(self.tts_root),
            "ttsRootSource": self.source,
            "ttsRootAvailable": self.available,
            "reasonCode": self.reason_code,
        }


class TtsStorage:
    """Resolve the one optional TTS override without adding an ownership root."""

    def __init__(self, user_root: Path) -> None:
        self.user_root = Path(user_root).resolve(strict=False)
        self.config_path = self.user_root / "config" / "storage.json"

    def snapshot(self, *, create_default: bool = True) -> TtsStorageSnapshot:
        configured = self._read_configured_root()
        source = "custom" if configured is not None else "default"
        root = configured if configured is not None else self.user_root / "tts"
        if configured is None and create_default:
            try:
                root.mkdir(parents=True, exist_ok=True)
            except OSError:
                return TtsStorageSnapshot(
                    self.user_root, root, source, False, TTS_ROOT_NOT_WRITABLE
                )
        reason = _availability_reason(root)
        return TtsStorageSnapshot(self.user_root, root, source, reason is None, reason)

    def require_root(self) -> Path:
        snapshot = self.snapshot(create_default=True)
        if not snapshot.available:
            raise TtsStorageUnavailable(snapshot.reason_code or TTS_ROOT_NOT_WRITABLE)
        return snapshot.tts_root

    def set_custom_root(self, value: Path) -> TtsStorageSnapshot:
        root = Path(value)
        if not root.is_absolute():
            raise TtsStorageUnavailable(TTS_ROOT_MISSING)
        root = root.resolve(strict=False)
        reason = _availability_reason(root, probe_write=True)
        if reason is not None:
            raise TtsStorageUnavailable(reason)
        self._write(root)
        return self.snapshot(create_default=False)

    def reset(self) -> TtsStorageSnapshot:
        self._write(None)
        return self.snapshot(create_default=True)

    def _read_configured_root(self) -> Path | None:
        if not self.config_path.exists():
            return None
        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise TtsStorageUnavailable(TTS_STORAGE_CONFIG_INVALID) from error
        if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
            raise TtsStorageUnavailable(TTS_STORAGE_CONFIG_INVALID)
        value = payload.get("ttsRoot")
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise TtsStorageUnavailable(TTS_STORAGE_CONFIG_INVALID)
        root = Path(value)
        if not root.is_absolute():
            raise TtsStorageUnavailable(TTS_STORAGE_CONFIG_INVALID)
        return root.resolve(strict=False)

    def _write(self, root: Path | None) -> None:
        payload = {"schemaVersion": 1, "ttsRoot": str(root) if root is not None else None}
        atomic_write_text(
            self.config_path,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            backup=True,
        )


def _availability_reason(root: Path, *, probe_write: bool = False) -> str | None:
    if not root.exists():
        return TTS_ROOT_MISSING
    if not root.is_dir():
        return TTS_ROOT_NOT_DIRECTORY
    if not os.access(root, os.W_OK):
        return TTS_ROOT_NOT_WRITABLE
    if probe_write:
        try:
            descriptor, probe = tempfile.mkstemp(prefix=".sakura-write-probe-", dir=root)
            os.close(descriptor)
            Path(probe).unlink()
        except OSError:
            return TTS_ROOT_NOT_WRITABLE
    return None


__all__ = [
    "TTS_ROOT_MISSING",
    "TTS_ROOT_NOT_DIRECTORY",
    "TTS_ROOT_NOT_WRITABLE",
    "TTS_STORAGE_CONFIG_INVALID",
    "TTS_STORAGE_UNAVAILABLE",
    "TtsStorage",
    "TtsStorageSnapshot",
    "TtsStorageUnavailable",
]
