from __future__ import annotations

import json
import re
import urllib.error
from collections.abc import Mapping
from typing import Any


_PROVIDER_PUBLIC_FIELDS = ("message", "code", "type", "status")
_PROVIDER_DIAGNOSTIC_LIMIT = 360
_PROVIDER_HTTP_PREFIX = re.compile(r"(?:^|\n)API HTTP (?P<status>[1-5][0-9]{2}):")
_PROVIDER_SENSITIVE_PATTERNS = (
    re.compile(r"\bPRIVATE_[A-Z0-9_]+\b"),
    re.compile(r"\b(?:sk|pk)-[A-Za-z0-9_-]{6,}\b", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{6,}", re.IGNORECASE),
    re.compile(
        r"\b(?:api[_ -]?key|authorization|token|secret|password|credential)\b"
        r"\s*[:=]\s*[^\s,;]+",
        re.IGNORECASE,
    ),
    re.compile(r"https?://[^\s\])}>,;]+", re.IGNORECASE),
    re.compile(r"\b[A-Za-z]:\\[^\s\])}>,;]+"),
    re.compile(r"(?<![\w:])/(?:[^/\s]+/)+[^/\s\])}>,;]+"),
)


def provider_http_status(error: BaseException) -> int | None:
    """Return a real HTTP status, or an explicitly formatted API HTTP status."""

    cause: BaseException | None = error
    seen: set[int] = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, urllib.error.HTTPError):
            return int(cause.code)
        cause = cause.__cause__
    matched = _PROVIDER_HTTP_PREFIX.search(str(error))
    return int(matched.group("status")) if matched is not None else None


def public_provider_http_message(
    error: BaseException,
    status_code: int | None = None,
) -> str:
    """Keep useful Provider HTTP details while removing credentials and private paths."""

    resolved_status = status_code if status_code is not None else provider_http_status(error)
    if resolved_status is None:
        return "供应商请求失败。"
    body = _provider_error_body(str(error), resolved_status)
    payload = _provider_error_payload(body)
    if payload is not None:
        raw_error = payload.get("error")
        if isinstance(raw_error, Mapping):
            public_source = raw_error
        elif isinstance(raw_error, str):
            public_source = {"message": raw_error}
        else:
            public_source = payload
        public_values: dict[str, str] = {}
        for field in _PROVIDER_PUBLIC_FIELDS:
            value = public_source.get(field)
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                continue
            sanitized = sanitize_provider_diagnostic(str(value))
            if sanitized:
                public_values[field] = sanitized

        message = public_values.pop("message", "")
        metadata = "; ".join(
            f"{field}: {public_values[field]}"
            for field in _PROVIDER_PUBLIC_FIELDS[1:]
            if field in public_values
        )
        if message and metadata:
            return f"API HTTP {resolved_status}: {message} ({metadata})"
        if message:
            return f"API HTTP {resolved_status}: {message}"
        if metadata:
            return f"API HTTP {resolved_status}: {metadata}"

    diagnostic = sanitize_provider_diagnostic(body)
    if diagnostic:
        return f"API HTTP {resolved_status}: {diagnostic}"
    return f"API HTTP {resolved_status}: 供应商请求失败。"


def _provider_error_body(error_text: str, status_code: int) -> str:
    raw_marker = "\n原始响应："
    if raw_marker in error_text:
        return error_text.rsplit(raw_marker, 1)[1].strip()
    prefix = f"API HTTP {status_code}:"
    return error_text.split(prefix, 1)[1].strip() if prefix in error_text else ""


def _provider_error_payload(body: str) -> Mapping[str, Any] | None:
    if not body.startswith("{"):
        return None
    try:
        decoded = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return None
    return decoded if isinstance(decoded, Mapping) else None


def sanitize_provider_diagnostic(value: str) -> str:
    sanitized = " ".join(value.split())
    for pattern in _PROVIDER_SENSITIVE_PATTERNS:
        sanitized = pattern.sub("[REDACTED]", sanitized)
    if len(sanitized) > _PROVIDER_DIAGNOSTIC_LIMIT:
        sanitized = sanitized[: _PROVIDER_DIAGNOSTIC_LIMIT - 1].rstrip() + "…"
    return sanitized


__all__ = [
    "provider_http_status",
    "public_provider_http_message",
    "sanitize_provider_diagnostic",
]
