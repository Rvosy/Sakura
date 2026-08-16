"""Persistent, Qt-free Runtime v2 voice recording repository."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
import wave
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from app.storage.atomic import atomic_write_text, rename_with_retry
from app.storage.paths import StoragePaths


RECORDING_SCHEMA_VERSION = 1
DEFAULT_NON_FAVORITE_LIMIT = 100
RECORDING_MEDIA_TYPE = "audio/wav"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


class VoiceRecordingError(RuntimeError):
    def __init__(self, code: str, message: str, *, stage: str = "unknown") -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.public_message = message
        self.stage = stage


@dataclass(frozen=True)
class VoiceRecording:
    recording_id: str
    character_id: str
    history_entry_id: str
    created_at: str
    tone: str
    portrait: str
    provider: str
    media_type: str
    byte_length: int
    sha256: str
    favorite: bool
    directory: Path
    audio_path: Path


@dataclass(frozen=True)
class RecordingDiagnostic:
    recording_id: str
    character_id: str
    code: str


@dataclass(frozen=True)
class PlaybackCopy:
    opaque_id: str
    recording_id: str
    media_type: str
    byte_length: int
    expires_at: str
    path: Path


class VoiceRecordingStore:
    """Own atomic recording commits, validation, lookup and retention."""

    def __init__(
        self,
        app_root: Path,
        *,
        non_favorite_limit: int = DEFAULT_NON_FAVORITE_LIMIT,
        diagnostic_sink: Callable[[RecordingDiagnostic], None] | None = None,
    ) -> None:
        if non_favorite_limit < 0:
            raise ValueError("non_favorite_limit must not be negative")
        self.paths = StoragePaths(Path(app_root))
        self.non_favorite_limit = non_favorite_limit
        self._diagnostic_sink = diagnostic_sink
        self._diagnostics: list[RecordingDiagnostic] = []

    @property
    def diagnostics(self) -> tuple[RecordingDiagnostic, ...]:
        return tuple(self._diagnostics)

    def scan_and_prune(self) -> tuple[VoiceRecording, ...]:
        healthy: list[VoiceRecording] = []
        root = self.paths.voice_recordings_dir
        if not root.is_dir():
            return ()
        for character_dir in sorted(root.iterdir(), key=lambda item: item.name):
            if character_dir.is_symlink() or not character_dir.is_dir():
                continue
            records = list(self._scan_character_directory(character_dir))
            healthy.extend(records)
            self._prune_records(records)
        return tuple(record for record in healthy if record.directory.is_dir())

    def commit(
        self,
        source_wav: Path,
        *,
        character_id: str,
        history_entry_id: str,
        tone: str = "",
        portrait: str = "",
        provider: str,
        recording_id: str | None = None,
        created_at: str | None = None,
    ) -> VoiceRecording:
        source_wav = Path(source_wav)
        stage = "validate_source"
        staging: Path | None = None
        try:
            _validate_wav(source_wav)
            recording_id = recording_id or uuid.uuid4().hex
            _require_safe_id(recording_id, "recording_id")
            if not character_id.strip() or not history_entry_id.strip() or not provider.strip():
                raise ValueError("recording associations must not be empty")
            created_at = created_at or datetime.now(timezone.utc).astimezone().isoformat(
                timespec="milliseconds"
            )
            _parse_timestamp(created_at)

            stage = "create_staging"
            character_dir = self.paths.voice_recordings_for(character_id)
            character_dir.mkdir(parents=True, exist_ok=True)
            final_dir = character_dir / recording_id
            if final_dir.exists():
                raise VoiceRecordingError(
                    "AUDIO_RECORDING_INVALID",
                    "recording identity already exists",
                    stage=stage,
                )
            staging = character_dir / f".staging-{recording_id}-{uuid.uuid4().hex}"
            staging.mkdir()

            stage = "copy_audio"
            staged_audio = staging / "audio.wav"
            _copy_and_sync(source_wav, staged_audio)
            byte_length = staged_audio.stat().st_size
            sha256 = _sha256_file(staged_audio)
            metadata = {
                "schemaVersion": RECORDING_SCHEMA_VERSION,
                "recordingId": recording_id,
                "characterId": character_id,
                "historyEntryId": history_entry_id,
                "createdAt": created_at,
                "tone": tone,
                "portrait": portrait,
                "provider": provider,
                "mediaType": RECORDING_MEDIA_TYPE,
                "byteLength": byte_length,
                "sha256": sha256,
                "favorite": False,
            }
            stage = "write_metadata"
            atomic_write_text(
                staging / "record.json",
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                backup=False,
            )
            stage = "validate_staging"
            _validate_wav(staged_audio)
            _sync_directory(staging)
            stage = "rename"
            rename_with_retry(staging, final_dir)
            staging = None
            _sync_directory(character_dir)
        except VoiceRecordingError as exc:
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)
            if exc.stage == "unknown":
                raise VoiceRecordingError(exc.code, exc.public_message, stage=stage) from exc
            raise
        except (OSError, ValueError, TypeError, wave.Error) as exc:
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)
            raise VoiceRecordingError(
                "AUDIO_RECORDING_INVALID",
                "recording could not be committed",
                stage=stage,
            ) from exc
        except BaseException:
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)
            raise

        stage = "validate_commit"
        record = self._load_record(final_dir, expected_character_id=character_id)
        if record is None:
            raise VoiceRecordingError(
                "AUDIO_RECORDING_INVALID",
                "committed recording is invalid",
                stage=stage,
            )
        try:
            self.prune_character(character_id)
        except (OSError, ValueError) as exc:
            raise VoiceRecordingError(
                "AUDIO_RECORDING_INVALID",
                "recording retention could not be applied",
                stage="prune",
            ) from exc
        return record

    def prune_character(self, character_id: str) -> tuple[str, ...]:
        records = list(
            self._scan_character_directory(self.paths.voice_recordings_for(character_id))
        )
        return self._prune_records(records)

    def latest_for_history(self, history_entry_id: str) -> VoiceRecording | None:
        candidates = [
            record
            for record in self.scan_and_prune()
            if record.history_entry_id == history_entry_id
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda item: (_timestamp(item.created_at), item.recording_id))

    def get(self, recording_id: str) -> VoiceRecording | None:
        _require_safe_id(recording_id, "recording_id")
        root = self.paths.voice_recordings_dir
        if not root.is_dir():
            return None
        for character_dir in root.iterdir():
            if character_dir.is_symlink() or not character_dir.is_dir():
                continue
            candidate = character_dir / recording_id
            if candidate.is_dir() and not candidate.is_symlink():
                return self._load_record(candidate)
        return None

    def set_favorite(self, recording_id: str, favorite: bool) -> VoiceRecording:
        record = self.get(recording_id)
        if record is None:
            raise VoiceRecordingError("AUDIO_RECORDING_INVALID", "recording is unavailable")
        metadata_path = record.directory / "record.json"
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
        data["favorite"] = bool(favorite)
        atomic_write_text(
            metadata_path,
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            backup=False,
        )
        updated = replace(record, favorite=bool(favorite))
        self.prune_character(record.character_id)
        return updated

    def create_playback_copy(
        self,
        recording_id: str,
        *,
        generation_id: str,
        expires_at: str,
    ) -> PlaybackCopy:
        record = self.get(recording_id)
        if record is None:
            raise VoiceRecordingError("AUDIO_RECORDING_INVALID", "recording is unavailable")
        _parse_timestamp(expires_at)
        generation_dir = self.paths.runtime_v2_tts_generation_dir(generation_id)
        generation_dir.mkdir(parents=True, exist_ok=True)
        opaque_id = uuid.uuid4().hex
        target = generation_dir / f"{opaque_id}.wav"
        try:
            os.link(record.audio_path, target)
        except OSError:
            _copy_and_sync(record.audio_path, target)
        return PlaybackCopy(
            opaque_id=opaque_id,
            recording_id=record.recording_id,
            media_type=record.media_type,
            byte_length=record.byte_length,
            expires_at=expires_at,
            path=target,
        )

    def cleanup_generation(self, generation_id: str) -> None:
        generation_dir = self.paths.runtime_v2_tts_generation_dir(generation_id)
        if generation_dir.is_symlink():
            generation_dir.unlink(missing_ok=True)
        elif generation_dir.is_dir():
            shutil.rmtree(generation_dir)

    def _scan_character_directory(self, character_dir: Path) -> Iterable[VoiceRecording]:
        if not character_dir.is_dir() or character_dir.is_symlink():
            return ()
        records: list[VoiceRecording] = []
        for candidate in sorted(character_dir.iterdir(), key=lambda item: item.name):
            if candidate.name.startswith(".staging-"):
                continue
            if candidate.is_symlink() or not candidate.is_dir():
                self._report(candidate.name, character_dir.name, "AUDIO_RECORDING_INVALID")
                continue
            record = self._load_record(candidate)
            if record is not None:
                records.append(record)
        return records

    def _load_record(
        self, directory: Path, *, expected_character_id: str | None = None
    ) -> VoiceRecording | None:
        recording_id = directory.name
        character_hint = expected_character_id or directory.parent.name
        try:
            if directory.is_symlink() or not directory.is_dir() or not _SAFE_ID.fullmatch(recording_id):
                raise ValueError("unsafe recording directory")
            metadata_path = directory / "record.json"
            audio_path = directory / "audio.wav"
            if metadata_path.is_symlink() or audio_path.is_symlink():
                raise ValueError("recording files must not be links")
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
            record = _record_from_json(data, directory)
            if record.recording_id != recording_id:
                raise ValueError("recording identity mismatch")
            if expected_character_id is not None and record.character_id != expected_character_id:
                raise ValueError("character identity mismatch")
            _validate_wav(audio_path)
            if audio_path.stat().st_size != record.byte_length:
                raise ValueError("recording length mismatch")
            if _sha256_file(audio_path) != record.sha256:
                raise ValueError("recording hash mismatch")
            return record
        except (OSError, ValueError, TypeError, json.JSONDecodeError, wave.Error):
            self._report(recording_id, character_hint, "AUDIO_RECORDING_INVALID")
            return None

    def _prune_records(self, records: Iterable[VoiceRecording]) -> tuple[str, ...]:
        candidates = sorted(
            (record for record in records if not record.favorite),
            key=lambda item: (_timestamp(item.created_at), item.recording_id),
        )
        excess = max(0, len(candidates) - self.non_favorite_limit)
        removed: list[str] = []
        for record in candidates[:excess]:
            shutil.rmtree(record.directory)
            removed.append(record.recording_id)
        return tuple(removed)

    def _report(self, recording_id: str, character_id: str, code: str) -> None:
        item = RecordingDiagnostic(recording_id, character_id, code)
        self._diagnostics.append(item)
        if self._diagnostic_sink is not None:
            self._diagnostic_sink(item)


def _record_from_json(data: Any, directory: Path) -> VoiceRecording:
    if not isinstance(data, dict) or data.get("schemaVersion") != RECORDING_SCHEMA_VERSION:
        raise ValueError("unsupported recording schema")
    required_strings = (
        "recordingId",
        "characterId",
        "historyEntryId",
        "createdAt",
        "tone",
        "portrait",
        "provider",
        "mediaType",
        "sha256",
    )
    if any(not isinstance(data.get(key), str) for key in required_strings):
        raise ValueError("invalid recording metadata")
    if data["mediaType"] != RECORDING_MEDIA_TYPE:
        raise ValueError("invalid recording media type")
    if not isinstance(data.get("byteLength"), int) or isinstance(data.get("byteLength"), bool):
        raise ValueError("invalid recording byte length")
    if data["byteLength"] <= 0 or not re.fullmatch(r"[0-9a-f]{64}", data["sha256"]):
        raise ValueError("invalid recording integrity fields")
    if not isinstance(data.get("favorite"), bool):
        raise ValueError("invalid favorite flag")
    _parse_timestamp(data["createdAt"])
    return VoiceRecording(
        recording_id=data["recordingId"],
        character_id=data["characterId"],
        history_entry_id=data["historyEntryId"],
        created_at=data["createdAt"],
        tone=data["tone"],
        portrait=data["portrait"],
        provider=data["provider"],
        media_type=data["mediaType"],
        byte_length=data["byteLength"],
        sha256=data["sha256"],
        favorite=data["favorite"],
        directory=directory,
        audio_path=directory / "audio.wav",
    )


def _validate_wav(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise VoiceRecordingError("AUDIO_RECORDING_INVALID", "audio file is unavailable")
    try:
        with wave.open(str(path), "rb") as handle:
            if handle.getnchannels() not in {1, 2}:
                raise ValueError("unsupported channel count")
            if handle.getsampwidth() not in {1, 2, 3, 4}:
                raise ValueError("unsupported sample width")
            if handle.getframerate() <= 0 or handle.getnframes() <= 0:
                raise ValueError("empty WAV")
            handle.readframes(min(handle.getnframes(), 1))
    except (OSError, ValueError, EOFError, wave.Error) as exc:
        raise VoiceRecordingError("AUDIO_RECORDING_INVALID", "audio WAV is invalid") from exc


def _copy_and_sync(source: Path, target: Path) -> None:
    with source.open("rb") as reader, target.open("xb") as writer:
        shutil.copyfileobj(reader, writer)
        writer.flush()
        os.fsync(writer.fileno())


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed


def _timestamp(value: str) -> float:
    return _parse_timestamp(value).timestamp()


def _require_safe_id(value: str, field: str) -> None:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise ValueError(f"{field} is invalid")


def _sync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


__all__ = [
    "DEFAULT_NON_FAVORITE_LIMIT",
    "PlaybackCopy",
    "RECORDING_SCHEMA_VERSION",
    "RecordingDiagnostic",
    "VoiceRecording",
    "VoiceRecordingError",
    "VoiceRecordingStore",
]
