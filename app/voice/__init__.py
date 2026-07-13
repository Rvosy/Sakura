"""语音包公共入口；Qt 播放控制器按需加载。"""

from __future__ import annotations

from typing import Any

__all__ = ["VoicePlaybackController"]


def __getattr__(name: str) -> Any:
    if name == "VoicePlaybackController":
        from app.voice.playback_controller import VoicePlaybackController

        return VoicePlaybackController
    raise AttributeError(name)
