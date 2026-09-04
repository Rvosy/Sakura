from __future__ import annotations

from pathlib import Path

from app.voice.text_language_guard import should_skip_tts_text
from app.voice.tts_settings import _load_tone_references


def test_japanese_language_guard_rejects_obvious_chinese_only() -> None:
    assert not should_skip_tts_text("うん。大丈夫。", "ja")
    assert not should_skip_tts_text("大丈夫", "ja")
    assert should_skip_tts_text("这是中文，不能进 TTS。", "ja")
    assert not should_skip_tts_text("这是中文，不能进 TTS。", "zh")


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
