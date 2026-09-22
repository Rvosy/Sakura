"""Neutral model Service client and consumer errors; contains no transport SDK."""
from __future__ import annotations

from dataclasses import dataclass, field

if __package__:
    from .sakura_model_client import ModelClient, ModelError
else:
    from sakura_model_client import ModelClient, ModelError


class ApiConfigError(RuntimeError):
    """消费者所需模型配置缺失。"""


class ApiRequestError(RuntimeError):
    """消费者语义处理或模型请求失败。"""


@dataclass(frozen=True)
class ApiSettings:
    """Legacy data type retained for imports; new consumers use ModelClient."""

    base_url: str
    api_key: str = field(repr=False)
    model: str
    timeout_seconds: int = 60
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    context_window_tokens: int = 32_768
    context_window_source: str = "fallback"


__all__ = ["ModelClient", "ModelError", "ApiConfigError", "ApiRequestError", "ApiSettings"]
