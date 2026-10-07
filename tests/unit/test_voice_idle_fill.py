from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.voice.cache_settings import load_voice_cache_settings
from plugins.builtin.sakura_tts_hub._device_load import device_below_peak
from plugins.builtin.sakura_tts_hub._idle_fill import IdleFill
from app.voice.recording_store import VoiceRecordingError, VoiceRecordingStore
from tests.unit.test_voice_recording_store import _stamp, _wav


def test_idle_fill_walks_past_recorded_and_suppressed_segments(monkeypatch):
    from types import SimpleNamespace
    from plugins.builtin.sakura_tts_hub import _idle_fill
    monkeypatch.setattr(_idle_fill, "device_below_peak", lambda: True)
    cursors, requests = [], []
    def page(character, before, limit):
        cursors.append(before)
        if before is None:
            return {"entries": [{"entryId": "new", "kind": "assistant", "segments": [
                {"hasText": True, "recorded": True, "suppressed": False},
                {"hasText": True, "recorded": False, "suppressed": True}]}],
                "nextCursor": "older", "hasMore": True}
        return {"entries": [{"entryId": "old", "kind": "assistant", "segments": [
            {"hasText": True, "recorded": False, "suppressed": False}]}],
            "nextCursor": None, "hasMore": False}
    speech = SimpleNamespace(
        cache_status=lambda *_: {"characterId": "alpha", "latestCursor": "latest", "idleFill": True, "busy": False, "hasRoom": True},
        cache_page=page,
        begin=lambda *args: requests.append(args) or {"jobId": "job"},
        poll=lambda _: {"status": "completed"},
    )
    worker = IdleFill(speech, SimpleNamespace(warning=lambda *_args, **_kwargs: None))
    assert worker.fill_once() is False
    assert worker.fill_once() is True
    assert cursors == [None, "older"]
    assert requests == [("alpha", "old", 0, {"background": True, "exportAudio": False})]


def test_device_peak_uses_cpu_load_and_gpu_busy_percent() -> None:
    assert device_below_peak(load_reader=lambda: 0.2, gpu_reader=lambda: 10, cpu_count=4)
    assert not device_below_peak(load_reader=lambda: 3.0, gpu_reader=lambda: 0, cpu_count=4)
    assert not device_below_peak(load_reader=lambda: 0.1, gpu_reader=lambda: 70, cpu_count=4)
    assert device_below_peak(load_reader=lambda: None, gpu_reader=lambda: None, cpu_count=4)


def test_cache_settings_load_shell_document_and_invalid_document_falls_back(tmp_path: Path) -> None:
    fallback = load_voice_cache_settings(tmp_path)
    assert fallback.directory is None
    assert fallback.idle_fill is False
    assert fallback.max_bytes == 512 * 1024 * 1024

    custom = tmp_path / "elsewhere"
    config = tmp_path / "config/voice_cache.json"
    config.parent.mkdir()
    config.write_text(json.dumps({
        "schemaVersion": 1, "directory": str(custom), "maxBytes": 64 * 1024 * 1024, "idleFill": True,
    }), encoding="utf-8")
    loaded = load_voice_cache_settings(tmp_path)
    assert loaded.directory == custom.resolve()
    assert loaded.idle_fill is True
    assert loaded.max_bytes == 64 * 1024 * 1024

    (tmp_path / "config" / "voice_cache.json").write_text("{", encoding="utf-8")
    broken = load_voice_cache_settings(tmp_path)
    assert broken.directory is None
    assert broken.idle_fill is False


@pytest.mark.parametrize("max_megabytes", [1, 32768])
def test_cache_settings_respect_user_capacity_outside_old_range(tmp_path: Path, max_megabytes: int) -> None:
    config = tmp_path / "config/voice_cache.json"
    config.parent.mkdir()
    custom = tmp_path / "custom-cache"
    config.write_text(json.dumps({
        "schemaVersion": 1, "directory": str(custom),
        "maxBytes": max_megabytes * 1024 * 1024, "idleFill": True,
    }), encoding="utf-8")

    settings = load_voice_cache_settings(tmp_path)

    assert settings.max_bytes == max_megabytes * 1024 * 1024
    assert settings.directory == custom
    assert settings.idle_fill


def test_unavailable_cache_does_not_silently_write_to_default_directory(tmp_path: Path) -> None:
    custom = tmp_path / "unavailable-cache"
    custom.write_text("a file occupies the selected directory", encoding="utf-8")
    config = tmp_path / "config/voice_cache.json"
    config.parent.mkdir()
    config.write_text(json.dumps({
        "schemaVersion": 1, "directory": str(custom), "maxBytes": 1024 * 1024,
    }), encoding="utf-8")
    settings = load_voice_cache_settings(tmp_path)
    store = VoiceRecordingStore(tmp_path)
    store.apply_cache_settings(settings.directory, settings.max_bytes)

    with pytest.raises(VoiceRecordingError) as caught:
        store.commit(_wav(tmp_path / "source.wav"), character_id="sakura", history_entry_id="entry", provider="fixture")
    assert caught.value.code == "AUDIO_RECORDING_INVALID"
    assert not store.paths.voice_recordings_dir.exists()
    assert custom.is_file()
