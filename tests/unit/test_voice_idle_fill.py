from __future__ import annotations

import json
from pathlib import Path

from app.storage.timeline import TimelineKind
from app.voice.cache_settings import load_voice_cache_settings, save_voice_cache_settings
from app.voice.device_load import device_below_peak
from app.voice.idle_fill import IdleFillCursor, next_missing_speech
from app.voice.recording_store import VoiceRecordingStore
from tests.unit.test_voice_recording_store import _stamp, _wav


class _Entry:
    def __init__(self, entry_id: str, kind: TimelineKind, segments: list[dict[str, object]]) -> None:
        self.entry_id = entry_id
        self.kind = kind
        self.payload = {"segments": segments}


class _Timeline:
    def __init__(self, pages: list[list[_Entry]]) -> None:
        self.pages = pages
        self.cursors: list[str | None] = []

    def latest_cursor(self, character_id: str) -> str:
        return f"{character_id}:{len(self.pages)}"

    def read_page_before(self, character_id: str, limit: int, *, before_cursor: str | None = None):
        del character_id, limit
        self.cursors.append(before_cursor)
        index = 0 if before_cursor is None else int(before_cursor)
        page = self.pages[index]
        has_more = index + 1 < len(self.pages)
        return page, str(index + 1) if has_more else None, has_more, sum(len(item) for item in self.pages)


def _segment(text: str, *, suppressed: bool = False) -> dict[str, object]:
    return {"text": text, "tone": "calm", "portrait": "base", "suppressTts": suppressed}


def test_missing_speech_skips_cached_and_suppressed_lines_then_walks_older_pages(tmp_path: Path) -> None:
    store = VoiceRecordingStore(tmp_path)
    store.commit(
        _wav(tmp_path / "cached.wav"),
        character_id="sakura",
        history_entry_id="entry-new",
        segment_index=0,
        provider="gpt-sovits",
        recording_id="record-0001",
        created_at=_stamp(1),
    )
    timeline = _Timeline([
        [
            _Entry("entry-new", TimelineKind.ASSISTANT, [_segment("已有"), _segment("不读", suppressed=True)]),
            _Entry("entry-human", TimelineKind.HUMAN, []),
        ],
        [
            _Entry("entry-old", TimelineKind.ASSISTANT, [_segment("更早的一句")]),
        ],
    ])
    cursor = IdleFillCursor()

    assert next_missing_speech(timeline, store, "sakura", cursor) is None
    missing = next_missing_speech(timeline, store, "sakura", cursor)

    assert timeline.cursors == [None, "1"]
    assert missing is not None
    assert (missing.history_entry_id, missing.segment_index, missing.text) == ("entry-old", 0, "更早的一句")


def test_device_peak_uses_cpu_load_and_gpu_busy_percent() -> None:
    assert device_below_peak(load_reader=lambda: 0.2, gpu_reader=lambda: 10, cpu_count=4)
    assert not device_below_peak(load_reader=lambda: 3.0, gpu_reader=lambda: 0, cpu_count=4)
    assert not device_below_peak(load_reader=lambda: 0.1, gpu_reader=lambda: 70, cpu_count=4)
    assert device_below_peak(load_reader=lambda: None, gpu_reader=lambda: None, cpu_count=4)


def test_cache_settings_round_trip_and_invalid_document_falls_back(tmp_path: Path) -> None:
    fallback = load_voice_cache_settings(tmp_path)
    assert fallback.directory is None
    assert fallback.idle_fill is False
    assert fallback.max_bytes == 512 * 1024 * 1024

    custom = tmp_path / "elsewhere"
    saved = save_voice_cache_settings(tmp_path, str(custom), 64 * 1024 * 1024, True)
    loaded = load_voice_cache_settings(tmp_path)
    assert saved == loaded
    assert loaded.directory == custom.resolve()
    assert loaded.idle_fill is True
    document = json.loads((tmp_path / "config" / "voice_cache.json").read_text(encoding="utf-8"))
    assert document["directory"] == str(custom.resolve())
    assert document["idleFill"] is True

    (tmp_path / "config" / "voice_cache.json").write_text("{", encoding="utf-8")
    broken = load_voice_cache_settings(tmp_path)
    assert broken.directory is None
    assert broken.idle_fill is False
