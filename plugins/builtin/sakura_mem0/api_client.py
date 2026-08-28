from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse, urlunparse

try:
    from .support import CancelChecker, check_cancelled
except ImportError:
    from support import CancelChecker, check_cancelled


@dataclass(frozen=True)
class ApiSettings:
    base_url: str
    api_key: str = field(repr=False)
    model: str
    timeout_seconds: int = 60


class OpenAICompatibleClient:
    """Narrow no-retry client used only by the plugin-owned curator."""

    def __init__(self, settings: ApiSettings, **_kwargs: object) -> None:
        self._settings = settings

    def complete_raw(
        self,
        system_prompt: str,
        messages: Sequence[Mapping[str, Any]],
        *,
        temperature: float = 0.2,
        response_format: Mapping[str, Any] | None = None,
        max_tokens: int | None = None,
        cancel_checker: CancelChecker | None = None,
        trace_metadata: object | None = None,
    ) -> str:
        del trace_metadata
        check_cancelled(cancel_checker)
        base = _normalize_openai_base_url(self._settings.base_url)
        url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
        payload: dict[str, Any] = {
            "model": self._settings.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                *[dict(message) for message in messages],
            ],
            "temperature": temperature,
        }
        if response_format is not None:
            payload["response_format"] = dict(response_format)
        if max_tokens is not None:
            payload["max_tokens"] = int(max_tokens)
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._settings.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(
            request,
            timeout=max(1, int(self._settings.timeout_seconds)),
        ) as response:
            raw = json.loads(response.read())
        check_cancelled(cancel_checker)
        try:
            content = raw["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise RuntimeError("CURATION_RESPONSE_INVALID") from error
        if not isinstance(content, str):
            raise RuntimeError("CURATION_RESPONSE_INVALID")
        return content

    def close(self) -> None:
        return None


def _normalize_openai_base_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.netloc.lower() != "generativelanguage.googleapis.com":
        return normalized
    parts = [part for part in parsed.path.split("/") if part]
    if parts and parts[0] in {"v1", "v1beta"} and "openai" not in parts:
        parts.append("openai")
        return urlunparse(parsed._replace(path="/" + "/".join(parts))).rstrip("/")
    return normalized


__all__ = ["ApiSettings", "OpenAICompatibleClient"]
