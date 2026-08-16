"""Voice package with lazy Legacy Qt exports.

Headless Runtime v2 code imports storage and synthesis modules from this
package.  Importing :mod:`app.voice` must therefore not initialize PySide.
The Legacy controller remains source compatible through a lazy attribute.
"""

from __future__ import annotations

from typing import Any

__all__ = ["VoicePlaybackController"]


def __getattr__(name: str) -> Any:
    if name == "VoicePlaybackController":
        from app.voice.playback_controller import VoicePlaybackController

        return VoicePlaybackController
    raise AttributeError(name)
