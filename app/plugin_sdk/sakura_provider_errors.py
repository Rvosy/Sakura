from __future__ import annotations

import json
import re
import traceback
from collections.abc import Iterable, Mapping
from typing import Any


_PROVIDER_PUBLIC_FIELDS = ("message", "detail", "Exception", "code", "type", "status", "param", "request_id")
_PROVIDER_DIAGNOSTIC_LIMIT = 4096
_PROVIDER_HTTP_PREFIX = re.compile(r"(?:^|\n)API HTTP (?P<status>[1-5][0-9]{2}):")
_SECRET = re.compile(r'''(?ix)
    (\b(?:api[_ -]?key|authorization|cookie|password|secret|(?:access[_-]?|refresh[_-]?)?token|credential)
    ["']?\s*[:=]\s*(?:(?:bearer|basic)\s+)?)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;}&]+)
    |(\bbearer\s+)[^\s,;}&]+|\b(?:sk|pk)-[\w.-]{6,}
''')


def provider_http_status(error: BaseException) -> int | None:
    """Return a real HTTP status, or an explicitly formatted API HTTP status."""
    cause: BaseException | None = error
    seen: set[int] = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        status = getattr(cause, "status_code", None)
        if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
            return status
        cause = cause.__cause__
    matched = _PROVIDER_HTTP_PREFIX.search(str(error))
    return int(matched.group("status")) if matched is not None else None


def public_provider_http_message(
    error: BaseException,
    status_code: int | None = None,
    *,
    secrets: Iterable[str] = (),
) -> str:
    """Keep Provider error fields, including paths and URLs, without response content."""

    resolved_status = status_code if status_code is not None else provider_http_status(error)
    if resolved_status is None:
        return sanitize_provider_diagnostic(str(error), secrets=secrets)
    secrets = tuple(secrets)
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
            sanitized = sanitize_provider_diagnostic(str(value), secrets=secrets)
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

    # A JSON response without error fields may contain conversation content.
    diagnostic = sanitize_provider_diagnostic(body, secrets=secrets) if payload is None else ""
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


def sanitize_provider_diagnostic(value: str, *, secrets: Iterable[str] = ()) -> str:
    # Remove known credentials before bounding the text. Preserve newlines and
    # diagnostic paths/URLs; the SDK handles credentials embedded in URLs.
    for secret in secrets:
        if secret:
            for encoded in (secret, json.dumps(secret)[1:-1], json.dumps(secret, ensure_ascii=False)[1:-1], repr(secret)[1:-1], ascii(secret)[1:-1]):
                value = value.replace(encoded, "[REDACTED]")
    value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
    value = _SECRET.sub(lambda m: (m[1] or m[2] or "") + "[REDACTED]", value)
    value = re.sub(r"([a-zA-Z][a-zA-Z0-9+.-]*://)[^/\s@]+@", r"\1[REDACTED]@", value)
    value = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", value).strip()
    if len(value) > _PROVIDER_DIAGNOSTIC_LIMIT:
        marker = f"\n[truncated: {len(value)} characters]\n"
        kept = _PROVIDER_DIAGNOSTIC_LIMIT - len(marker)
        value = value[:kept // 2] + marker + value[-(kept - kept // 2):]
    return value


def provider_exception_diagnostics(error: BaseException, *, secrets: Iterable[str] = ()) -> dict[str, str]:
    """Capture a provider failure before its asynchronous worker releases it."""
    secrets = (*secrets, *getattr(error, "diagnostic_secrets", ()))
    chain, stacks, seen, current = [], [], set(), error
    root = error
    while current is not None and id(current) not in seen and len(chain) < 16:
        seen.add(id(current))
        root = current
        status = getattr(current, "status_code", None)
        body = getattr(current, "body", None)
        if isinstance(status, int) and isinstance(body, Mapping):
            # SDK exception strings embed the entire response, including normal
            # output fields. Keep the provider's error fields, not that payload.
            message = public_provider_http_message(
                RuntimeError(f"API HTTP {status}: {json.dumps(body, ensure_ascii=False)}"),
                status, secrets=secrets,
            )
        else:
            message = sanitize_provider_diagnostic(str(current), secrets=secrets)
        summary = f"{type(current).__name__}: {message}"
        chain.append(summary)
        stacks.append(summary + "\n" + "".join(traceback.format_tb(current.__traceback__, limit=-32)))
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
    diagnostics = {
        "diagnostic": message,
        "error_type": type(error).__name__,
        "cause_type": type(root).__name__,
        "exception_chain": sanitize_provider_diagnostic("\nCaused by: ".join(chain), secrets=secrets),
        "exception_stack": sanitize_provider_diagnostic("\nCaused by:\n".join(stacks), secrets=secrets),
    }
    frames = traceback.extract_tb(root.__traceback__)
    if frames:
        frame = frames[-1]
        diagnostics["exception_site"] = sanitize_provider_diagnostic(f"{frame.filename}:{frame.name}:{frame.lineno}", secrets=secrets)
    remote = getattr(root, "diagnostics", None)
    if isinstance(remote, Mapping):
        for key in ("diagnostic", "error_type", "cause_type", "cause_code", "exception_site"):
            if isinstance(remote.get(key), str):
                diagnostics[key] = sanitize_provider_diagnostic(remote[key], secrets=secrets)
        for key in ("exception_chain", "exception_stack"):
            if isinstance(remote.get(key), str):
                diagnostics[key] = sanitize_provider_diagnostic(diagnostics[key] + "\nRemote:\n" + remote[key], secrets=secrets)
    return diagnostics


def provider_failure(error_code: str, error: object) -> dict[str, Any]:
    """Keep the failure captured at the worker boundary alongside its stable code."""
    diagnostics = (provider_exception_diagnostics(error) if isinstance(error, BaseException)
                   else {"diagnostic": sanitize_provider_diagnostic(str(error))})
    diagnostics.setdefault("cause_code", error_code)
    return {"errorCode": error_code, "diagnostics": diagnostics}



__all__ = [
    "provider_exception_diagnostics",
    "provider_failure",
    "provider_http_status",
    "public_provider_http_message",
    "sanitize_provider_diagnostic",
]
