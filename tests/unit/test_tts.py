from __future__ import annotations

from app.voice.text_language_guard import should_skip_tts_text


def test_japanese_language_guard_rejects_obvious_chinese_only() -> None:
    assert not should_skip_tts_text("うん。大丈夫。", "ja")
    assert not should_skip_tts_text("大丈夫", "ja")
    assert should_skip_tts_text("这是中文，不能进 TTS。", "ja")
    assert not should_skip_tts_text("这是中文，不能进 TTS。", "zh")
