from __future__ import annotations

import sys


class VisualEffectMode:
    SOLID = "solid"
    GAUSSIAN_BLUR = "gaussian_blur"
    WINDOWS_ACRYLIC = "windows_acrylic"
    MACOS_VISUAL_EFFECT = "macos_visual_effect"
    _ALL = (SOLID, GAUSSIAN_BLUR, WINDOWS_ACRYLIC, MACOS_VISUAL_EFFECT)
    DEFAULT = GAUSSIAN_BLUR

    @classmethod
    def available_modes(cls) -> list[str]:
        modes = [cls.SOLID, cls.GAUSSIAN_BLUR]
        if sys.platform == "darwin":
            modes.append(cls.MACOS_VISUAL_EFFECT)
        return modes

    @classmethod
    def validate(cls, value: str) -> str:
        return value if value in cls._ALL else cls.DEFAULT
