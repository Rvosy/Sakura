"""Neutral model Service client and consumer errors; contains no transport SDK."""
from __future__ import annotations

if __package__:
    from .sakura_model_client import ModelClient, ModelError
else:
    from sakura_model_client import ModelClient, ModelError


class ApiConfigError(RuntimeError):
    """消费者所需模型配置缺失。"""


class ApiRequestError(RuntimeError):
    """消费者语义处理或模型请求失败。"""


__all__ = ["ModelClient", "ModelError", "ApiConfigError", "ApiRequestError"]
