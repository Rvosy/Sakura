from __future__ import annotations

from dataclasses import dataclass

SCREEN_AWARENESS_DEFAULT_CHECK_INTERVAL_MINUTES = 20
SCREEN_AWARENESS_DEFAULT_COOLDOWN_MINUTES = 10
SCREEN_AWARENESS_DEFAULT_SCREEN_CONTEXT_BATCH_LIMIT = 6
SCREEN_AWARENESS_DEFAULT_SCREEN_CONTEXT_RESOLUTION = "fullscreen"
SCREEN_AWARENESS_SCREEN_CONTEXT_RESOLUTIONS = (
    "fullscreen",
    "720p",
    "1080p",
    "2160p",
)
SCREEN_AWARENESS_MIN_CHECK_INTERVAL_MINUTES = 1
SCREEN_AWARENESS_MAX_CHECK_INTERVAL_MINUTES = 120
SCREEN_AWARENESS_MIN_COOLDOWN_MINUTES = 1
SCREEN_AWARENESS_MAX_COOLDOWN_MINUTES = 120
SCREEN_AWARENESS_MIN_SCREEN_CONTEXT_BATCH_LIMIT = 1
SCREEN_AWARENESS_MAX_SCREEN_CONTEXT_BATCH_LIMIT = 20


@dataclass(frozen=True)
class ScreenAwarenessSettings:
    """主动屏幕感知配置；启用后会定期截图并让模型基于屏幕找话题。"""

    enabled: bool = True
    screen_context_enabled: bool = True
    check_interval_minutes: int = SCREEN_AWARENESS_DEFAULT_CHECK_INTERVAL_MINUTES
    cooldown_minutes: int = SCREEN_AWARENESS_DEFAULT_COOLDOWN_MINUTES
    screen_context_batch_limit: int = SCREEN_AWARENESS_DEFAULT_SCREEN_CONTEXT_BATCH_LIMIT
    screen_context_resolution: str = SCREEN_AWARENESS_DEFAULT_SCREEN_CONTEXT_RESOLUTION

    def normalized(self) -> "ScreenAwarenessSettings":
        enabled = bool(self.enabled)
        screen_context_enabled = enabled and bool(self.screen_context_enabled)
        return ScreenAwarenessSettings(
            enabled=enabled,
            screen_context_enabled=screen_context_enabled,
            check_interval_minutes=_clamp_bounded_int(
                self.check_interval_minutes,
                min_value=SCREEN_AWARENESS_MIN_CHECK_INTERVAL_MINUTES,
                max_value=SCREEN_AWARENESS_MAX_CHECK_INTERVAL_MINUTES,
            ),
            cooldown_minutes=_clamp_bounded_int(
                self.cooldown_minutes,
                min_value=SCREEN_AWARENESS_MIN_COOLDOWN_MINUTES,
                max_value=SCREEN_AWARENESS_MAX_COOLDOWN_MINUTES,
            ),
            screen_context_batch_limit=_clamp_bounded_int(
                self.screen_context_batch_limit,
                min_value=SCREEN_AWARENESS_MIN_SCREEN_CONTEXT_BATCH_LIMIT,
                max_value=SCREEN_AWARENESS_MAX_SCREEN_CONTEXT_BATCH_LIMIT,
            ),
            screen_context_resolution=normalize_screen_context_resolution(
                self.screen_context_resolution
            ),
        )

    def allows_screen_context(self) -> bool:
        """主动屏幕感知依赖截图；关闭屏幕上下文时整个功能停止。"""
        normalized = self.normalized()
        return normalized.enabled and normalized.screen_context_enabled


def _clamp_bounded_int(value: int, *, min_value: int, max_value: int) -> int:
    return max(
        min_value,
        min(max_value, value),
    )


def normalize_screen_context_resolution(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in SCREEN_AWARENESS_SCREEN_CONTEXT_RESOLUTIONS:
        return normalized
    return SCREEN_AWARENESS_DEFAULT_SCREEN_CONTEXT_RESOLUTION
