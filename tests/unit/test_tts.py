from __future__ import annotations

from pathlib import Path

import pytest


pytest.importorskip("PySide6")

from app.voice.text_language_guard import should_skip_tts_text  # noqa: E402
from app.voice.tts import (  # noqa: E402
    _is_restartable_local_tts_service_failure,
    _is_soft_synth_failure,
    _is_voiceable_text,
    _resolve_request_text_lang,
)
from app.voice.tts_settings import _load_tone_references  # noqa: E402


def test_japanese_language_guard_rejects_obvious_chinese_only() -> None:
    assert not should_skip_tts_text("うん。大丈夫。", "ja")
    assert not should_skip_tts_text("大丈夫", "ja")
    assert should_skip_tts_text("这是中文，不能进 TTS。", "ja")
    assert not should_skip_tts_text("这是中文，不能进 TTS。", "zh")


def test_request_language_resolution_handles_mixed_text() -> None:
    assert _resolve_request_text_lang("Steamを開いているんだね。", "ja") == "auto"
    assert _resolve_request_text_lang("でも私、大丈夫だよ。", "ja") == "ja"
    assert _resolve_request_text_lang("Steam is open.", "en") == "en"
    assert _resolve_request_text_lang("Steam 打开咗。", "all_yue") == "auto_yue"


def test_tts_text_and_service_failures_are_classified_conservatively() -> None:
    for text in ("こんにちは", "你好", "Hello", "123"):
        assert _is_voiceable_text(text)
    for text in ("！？…、。", "🎉🥳✨", "♪♪♪", "   "):
        assert not _is_voiceable_text(text)

    soft_failure = '{"message":"tts failed","Exception":"[Errno 22] Invalid argument"}'
    broken_pipe = '{"message":"tts failed","Exception":"[Errno 32] Broken pipe"}'
    assert _is_soft_synth_failure(400, soft_failure)
    assert not _is_soft_synth_failure(400, broken_pipe)
    assert _is_restartable_local_tts_service_failure(400, broken_pipe)


def test_tone_reference_manifest_resolves_existing_audio(tmp_path: Path) -> None:
    audio_path = tmp_path / "voice" / "refs" / "tone_refs" / "neutral.wav"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"wav")
    manifest = tmp_path / "voice" / "refs" / "ref.txt"
    manifest.write_text(
        "voice/refs/tone_refs/neutral.wav|JA|テスト|中性\n",
        encoding="utf-8",
    )

    references = _load_tone_references(manifest, tmp_path)

    assert list(references) == ["中性"]
    assert references["中性"][0].ref_audio_path == audio_path
