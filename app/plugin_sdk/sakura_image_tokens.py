from __future__ import annotations
from math import ceil, floor, sqrt
SCREEN_AWARENESS_TOKEN_PATCH_SIZE = 32
SCREEN_AWARENESS_HIGH_DETAIL_MAX_EDGE = 2048
SCREEN_AWARENESS_HIGH_DETAIL_SHORT_SIDE = 768
SCREEN_AWARENESS_TILE_SIZE = 512
SCREEN_AWARENESS_DEFAULT_TILE_BASE_TOKENS = 85
SCREEN_AWARENESS_DEFAULT_TILE_TOKENS = 170

def estimate_screen_context_image_tokens_for_size(
    width: int,
    height: int,
    *,
    model: str | None = None,
) -> int:
    """估算 high detail 图像 token；优先匹配 OpenAI 官方 patch/tile 规则。"""
    image_width = max(1, int(width))
    image_height = max(1, int(height))
    patch_profile = _patch_token_profile_for_model(model)
    if patch_profile is not None:
        patch_budget, multiplier = patch_profile
        return _estimate_patch_based_image_tokens(
            image_width,
            image_height,
            patch_budget=patch_budget,
            multiplier=multiplier,
        )

    base_tokens, tile_tokens = _tile_token_profile_for_model(model)
    return _estimate_tile_based_high_detail_tokens(
        image_width,
        image_height,
        base_tokens=base_tokens,
        tile_tokens=tile_tokens,
    )


def estimate_screen_context_batch_tokens_for_size(
    width: int,
    height: int,
    image_count: int,
    *,
    model: str | None = None,
) -> int:
    """按实际截图尺寸估算一批主动感知截图的图像 token。"""
    try:
        count = int(image_count)
    except (TypeError, ValueError):
        count = 0
    return estimate_screen_context_image_tokens_for_size(width, height, model=model) * max(
        0,
        count,
    )


def _patch_token_profile_for_model(model: str | None) -> tuple[int, float] | None:
    normalized = (model or "").strip().lower()
    if not normalized:
        return None
    if normalized.startswith("gpt-5.4-mini") or normalized.startswith("gpt-5-mini"):
        return 1536, 1.62
    if normalized.startswith("gpt-5.4-nano") or normalized.startswith("gpt-5-nano"):
        return 1536, 2.46
    if normalized.startswith("gpt-4.1-mini"):
        return 1536, 1.62
    if normalized.startswith("gpt-4.1-nano"):
        return 1536, 2.46
    if normalized.startswith("o4-mini"):
        return 1536, 1.72
    if normalized.startswith(("gpt-5.5", "gpt-5.4")):
        return 2500, 1.0
    if normalized.startswith(
        (
            "gpt-5.2",
            "gpt-5.3-codex",
            "gpt-5.2-codex",
            "gpt-5.1-codex",
            "gpt-5-codex-mini",
        )
    ):
        return 1536, 1.0
    return None


def _tile_token_profile_for_model(model: str | None) -> tuple[int, int]:
    normalized = (model or "").strip().lower()
    if "gpt-4o-mini" in normalized:
        return 2833, 5667
    if normalized.startswith("computer-use-preview"):
        return 65, 129
    if normalized.startswith(("o1", "o3")):
        return 75, 150
    if normalized in {"gpt-5", "gpt-5-chat-latest"}:
        return 70, 140
    return SCREEN_AWARENESS_DEFAULT_TILE_BASE_TOKENS, SCREEN_AWARENESS_DEFAULT_TILE_TOKENS


def _estimate_tile_based_high_detail_tokens(
    width: int,
    height: int,
    *,
    base_tokens: int,
    tile_tokens: int,
) -> int:
    resized_width, resized_height = _resize_for_tile_high_detail(width, height)
    tiles = ceil(resized_width / SCREEN_AWARENESS_TILE_SIZE) * ceil(
        resized_height / SCREEN_AWARENESS_TILE_SIZE
    )
    return base_tokens + tiles * tile_tokens


def _resize_for_tile_high_detail(width: int, height: int) -> tuple[int, int]:
    scale = min(1.0, SCREEN_AWARENESS_HIGH_DETAIL_MAX_EDGE / max(width, height))
    resized_width = max(1, int(round(width * scale)))
    resized_height = max(1, int(round(height * scale)))
    shortest_side = min(resized_width, resized_height)
    if shortest_side > SCREEN_AWARENESS_HIGH_DETAIL_SHORT_SIDE:
        detail_scale = SCREEN_AWARENESS_HIGH_DETAIL_SHORT_SIDE / shortest_side
        resized_width = max(1, int(round(resized_width * detail_scale)))
        resized_height = max(1, int(round(resized_height * detail_scale)))
    return resized_width, resized_height


def _estimate_patch_based_image_tokens(
    width: int,
    height: int,
    *,
    patch_budget: int,
    multiplier: float,
) -> int:
    resized_width, resized_height = _resize_to_max_edge(width, height)
    patch_count = ceil(resized_width / SCREEN_AWARENESS_TOKEN_PATCH_SIZE) * ceil(
        resized_height / SCREEN_AWARENESS_TOKEN_PATCH_SIZE
    )
    if patch_count > patch_budget:
        resized_width, resized_height = _resize_to_patch_budget(
            resized_width,
            resized_height,
            patch_budget,
        )
        patch_count = ceil(resized_width / SCREEN_AWARENESS_TOKEN_PATCH_SIZE) * ceil(
            resized_height / SCREEN_AWARENESS_TOKEN_PATCH_SIZE
        )
    return ceil(min(patch_budget, patch_count) * multiplier)


def _resize_to_max_edge(width: int, height: int) -> tuple[int, int]:
    scale = min(1.0, SCREEN_AWARENESS_HIGH_DETAIL_MAX_EDGE / max(width, height))
    return max(1, int(round(width * scale))), max(1, int(round(height * scale)))


def _resize_to_patch_budget(width: int, height: int, patch_budget: int) -> tuple[int, int]:
    shrink_factor = sqrt(
        (SCREEN_AWARENESS_TOKEN_PATCH_SIZE**2 * patch_budget) / (width * height)
    )
    scaled_width = width * shrink_factor
    scaled_height = height * shrink_factor
    width_ratio = _patch_floor_ratio(scaled_width)
    height_ratio = _patch_floor_ratio(scaled_height)
    adjusted = shrink_factor * min(width_ratio, height_ratio)
    return max(1, int(width * adjusted)), max(1, int(height * adjusted))


def _patch_floor_ratio(value: float) -> float:
    patches = value / SCREEN_AWARENESS_TOKEN_PATCH_SIZE
    if patches <= 0:
        return 1.0
    floored = floor(patches)
    if floored <= 0:
        return 1.0
    return floored / patches
