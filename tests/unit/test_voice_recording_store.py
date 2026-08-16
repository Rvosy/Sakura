from __future__ import annotations

import json
import os
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.voice.recording_store import VoiceRecordingStore


def _wav(path: Path, marker: int = 1) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16_000)
        handle.writeframes(bytes([marker % 255, 0]) * 160)
    return path


def _stamp(index: int) -> str:
    return (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=index)).isoformat()


def test_commit_is_atomic_and_lookup_and_playback_copy_are_controlled(tmp_path: Path) -> None:
    store = VoiceRecordingStore(tmp_path)
    record = store.commit(
        _wav(tmp_path / "source.wav"),
        character_id="sakura",
        history_entry_id="entry-0001",
        provider="gpt-sovits",
        recording_id="record-0001",
        created_at=_stamp(1),
        tone="happy",
        portrait="smile",
    )

    assert record.audio_path.read_bytes()[:4] == b"RIFF"
    metadata = json.loads((record.directory / "record.json").read_text(encoding="utf-8"))
    assert metadata["schemaVersion"] == 1
    assert metadata["historyEntryId"] == "entry-0001"
    assert metadata["favorite"] is False
    assert not list(record.directory.parent.glob(".staging-*"))
    assert store.latest_for_history("entry-0001") == record

    playback = store.create_playback_copy(
        record.recording_id,
        generation_id="generation-1",
        expires_at=_stamp(100),
    )
    assert playback.opaque_id not in str(record.audio_path)
    assert playback.path.parent.name == "generation-1"
    assert playback.path.read_bytes() == record.audio_path.read_bytes()
    store.cleanup_generation("generation-1")
    assert not playback.path.exists()
    assert record.audio_path.exists()


def test_pruning_is_per_character_and_favorites_are_exempt(tmp_path: Path) -> None:
    store = VoiceRecordingStore(tmp_path, non_favorite_limit=2)
    first = store.commit(
        _wav(tmp_path / "one.wav", 1),
        character_id="sakura",
        history_entry_id="entry-0001",
        provider="gpt-sovits",
        recording_id="record-0001",
        created_at=_stamp(1),
    )
    store.set_favorite(first.recording_id, True)
    for index in range(2, 5):
        store.commit(
            _wav(tmp_path / f"{index}.wav", index),
            character_id="sakura",
            history_entry_id=f"entry-{index:04d}",
            provider="gpt-sovits",
            recording_id=f"record-{index:04d}",
            created_at=_stamp(index),
        )
    other = store.commit(
        _wav(tmp_path / "other.wav", 9),
        character_id="other",
        history_entry_id="entry-other",
        provider="gpt-sovits",
        recording_id="record-other",
        created_at=_stamp(1),
    )

    assert store.get("record-0001").favorite is True  # type: ignore[union-attr]
    assert store.get("record-0002") is None
    assert store.get("record-0003") is not None
    assert store.get("record-0004") is not None
    assert store.get(other.recording_id) is not None


def test_corrupt_and_future_records_are_isolated_and_not_counted(tmp_path: Path) -> None:
    store = VoiceRecordingStore(tmp_path, non_favorite_limit=1)
    healthy = store.commit(
        _wav(tmp_path / "healthy.wav"),
        character_id="sakura",
        history_entry_id="entry-good",
        provider="gpt-sovits",
        recording_id="record-good",
        created_at=_stamp(1),
    )
    corrupt_dir = healthy.directory.parent / "record-bad0"
    corrupt_dir.mkdir()
    _wav(corrupt_dir / "audio.wav")
    (corrupt_dir / "record.json").write_text('{"schemaVersion":99}', encoding="utf-8")
    staging = healthy.directory.parent / ".staging-active-record"
    staging.mkdir()

    scanned = store.scan_and_prune()

    assert [record.recording_id for record in scanned] == ["record-good"]
    assert corrupt_dir.exists()
    assert staging.exists()
    assert any(item.recording_id == "record-bad0" for item in store.diagnostics)


def test_invalid_wav_is_rejected_without_recording(tmp_path: Path) -> None:
    source = tmp_path / "bad.wav"
    source.write_bytes(b"not-wave")
    store = VoiceRecordingStore(tmp_path)

    try:
        store.commit(
            source,
            character_id="sakura",
            history_entry_id="entry-bad0",
            provider="gpt-sovits",
        )
    except Exception as error:
        assert getattr(error, "code", None) == "AUDIO_RECORDING_INVALID"
    else:
        raise AssertionError("invalid WAV was accepted")
    assert not store.paths.voice_recordings_for("sakura").exists()


def test_trailing_dot_character_commits_through_windows_verbatim_root(tmp_path: Path) -> None:
    app_root = tmp_path
    if os.name == "nt":
        app_root = Path("\\\\?\\" + str(tmp_path.resolve()))
    store = VoiceRecordingStore(app_root)

    record = store.commit(
        _wav(tmp_path / "navi.wav"),
        character_id="N.A.V.I.",
        history_entry_id="entry-navi-0001",
        provider="genie-tts",
        recording_id="record-navi-0001",
        created_at=_stamp(1),
    )

    assert not record.directory.parent.name.endswith((".", " "))
    assert record.character_id == "N.A.V.I."
    assert store.latest_for_history("entry-navi-0001") == record
    playback = store.create_playback_copy(
        record.recording_id,
        generation_id="generation-navi",
        expires_at=_stamp(100),
    )
    assert playback.path.read_bytes() == record.audio_path.read_bytes()
