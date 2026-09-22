from __future__ import annotations

from dataclasses import dataclass
from math import ceil, floor, sqrt

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
SCREEN_AWARENESS_SCREEN_CONTEXT_RESOLUTION_BOUNDS = {
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "2160p": (3840, 2160),
}
SCREEN_AWARENESS_IMAGE_DETAIL = "high"
SCREEN_AWARENESS_TOKEN_PATCH_SIZE = 32
SCREEN_AWARENESS_HIGH_DETAIL_MAX_EDGE = 2048
SCREEN_AWARENESS_HIGH_DETAIL_SHORT_SIDE = 768
SCREEN_AWARENESS_TILE_SIZE = 512
SCREEN_AWARENESS_DEFAULT_TILE_BASE_TOKENS = 85
SCREEN_AWARENESS_DEFAULT_TILE_TOKENS = 170
SCREEN_AWARENESS_MIN_CHECK_INTERVAL_MINUTES = 1
SCREEN_AWARENESS_MAX_CHECK_INTERVAL_MINUTES = 120
SCREEN_AWARENESS_MIN_COOLDOWN_MINUTES = 1
SCREEN_AWARENESS_MAX_COOLDOWN_MINUTES = 120
SCREEN_AWARENESS_MIN_SCREEN_CONTEXT_BATCH_LIMIT = 1
SCREEN_AWARENESS_MAX_SCREEN_CONTEXT_BATCH_LIMIT = 20
SCREEN_AWARENESS_TIMER_POLL_INTERVAL_MS = 10_000
SCREEN_AWARENESS_TIMER_DUE_GRACE_SECONDS = 1.0
SCREEN_AWARENESS_CONTEXT_HISTORY_MARKER = "[已抓取屏幕上下文]"



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
            check_interval_minutes=_clamp_interval_minutes(
                self.check_interval_minutes,
                min_value=SCREEN_AWARENESS_MIN_CHECK_INTERVAL_MINUTES,
                max_value=SCREEN_AWARENESS_MAX_CHECK_INTERVAL_MINUTES,
            ),
            cooldown_minutes=_clamp_interval_minutes(
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


def _clamp_interval_minutes(value: int, *, min_value: int, max_value: int) -> int:
    return _clamp_bounded_int(value, min_value=min_value, max_value=max_value)


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


def screen_context_resolution_size(
    width: int,
    height: int,
    resolution: object,
) -> tuple[int, int]:
    """返回所选档位下的等比截图尺寸；固定档位不会放大较小的屏幕。"""
    image_width = max(1, int(width))
    image_height = max(1, int(height))
    normalized = normalize_screen_context_resolution(resolution)
    bounds = SCREEN_AWARENESS_SCREEN_CONTEXT_RESOLUTION_BOUNDS.get(normalized)
    if bounds is None:
        return image_width, image_height

    max_width, max_height = bounds
    if image_height > image_width:
        max_width, max_height = max_height, max_width
    scale = min(1.0, max_width / image_width, max_height / image_height)
    return (
        max(1, int(round(image_width * scale))),
        max(1, int(round(image_height * scale))),
    )


from app.plugin_sdk.sakura_image_tokens import (estimate_screen_context_image_tokens_for_size, estimate_screen_context_batch_tokens_for_size)
