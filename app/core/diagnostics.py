"""Local exception diagnostics, without credentials, locals or absolute paths.

This module uses only the standard library so protocol error responses can use it
without importing the application or plugin runtime.
"""
from __future__ import annotations

import re
import json
from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterable
from urllib.parse import urlsplit, urlunsplit

DIAGNOSTIC_LIMIT = 4096
TRACE_LIMIT = 8192
DIAGNOSTIC_TEXT_KEYS = frozenset({"diagnostic", "exception_chain", "exception_stack", "recovery_diagnostic"})
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_SECRET = re.compile(r'''(?ix)
    \b(?:api[_-]?key|authorization|cookie|password|secret|(?:access[_-]?|refresh[_-]?)?token|credential)
    ["']?\s*[:=]\s*(?:bearer\s+)?(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;}]+)
    |\bbearer\s+[^\s,;}]+|\bsk-[\w.-]{6,}
''')
_URL = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s<>\"']+")
_QUOTED_PATH = re.compile(r'''(["'])((?:[A-Za-z]:[\\/]|/|\\\\)[^\r\n]*?)\1''')
_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|(?<![\w:>])/(?!/))[^\s\"'<>|,;]*")
_SECRETS: ContextVar[set[str] | None] = ContextVar("diagnostic_secrets", default=None)


@contextmanager
def diagnostic_secret_scope():
    """Keep known credentials local to one request, including nested logging."""
    token = _SECRETS.set(set(_SECRETS.get() or ()))
    try:
        yield
    finally:
        _SECRETS.reset(token)


def register_diagnostic_secret(value: str) -> None:
    current = _SECRETS.get()
    if current is not None and value:
        current.add(value)


def bounded_text(text: str, maximum: int) -> str:
    if len(text) <= maximum:
        return text
    marker = f"\n[truncated: {len(text)} characters]\n"
    if maximum <= len(marker):
        return marker[:max(0, maximum)]
    kept = max(0, maximum - len(marker))
    # The beginning identifies the operation; the tail often contains the root
    # cause. Keep both when a provider emits an unusually large error.
    return text[:kept // 2] + marker + text[-(kept - kept // 2):]


def safe_diagnostic_text(value: object, maximum: int = DIAGNOSTIC_LIMIT, *, secrets: Iterable[str] = ()) -> str:
    try:
        text = str(value)
    except Exception:
        text = f"{type(value).__name__}: exception message could not be formatted"
    for secret in (*secrets, *(_SECRETS.get() or ())):
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = _ANSI.sub("", text)
    text = _SECRET.sub("[REDACTED]", text)
    text = re.sub(r"\bPRIVATE_[A-Z0-9_]+\b", "[REDACTED]", text)
    urls: list[str] = []

    def url(match: re.Match[str]) -> str:
        try:
            parsed = urlsplit(match.group())
            host = parsed.hostname or ""
            port = f":{parsed.port}" if parsed.port else ""
            urls.append("<路径>/" + parsed.path.rsplit("/", 1)[-1] if parsed.scheme == "file" else urlunsplit((parsed.scheme, host + port, parsed.path, "", "")))
        except ValueError:
            urls.append("[URL]")
        return f"<url-{len(urls) - 1}>"

    def path(value: str) -> str:
        name = value.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        return f"<路径>/{name}" if name else "<路径>"

    text = _URL.sub(url, text)
    text = _QUOTED_PATH.sub(lambda m: m[1] + path(m[2]) + m[1], text)
    text = _PATH.sub(lambda m: path(m[0]), text)
    for index, value in enumerate(urls):
        text = text.replace(f"<url-{index}>", value)
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text).replace("\r\n", "\n")
    return bounded_text(text.strip(), maximum)


def exception_chain(error: BaseException) -> list[BaseException]:
    result: list[BaseException] = []
    seen: set[int] = set()
    pending = [error]
    while pending and len(result) < 16:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        result.append(current)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(reversed(current.exceptions))
        cause = current.__cause__ if current.__cause__ is not None else (None if current.__suppress_context__ else current.__context__)
        if cause is not None:
            pending.append(cause)
    return result


def _exception_message(error: object) -> str:
    text = safe_diagnostic_text(error, TRACE_LIMIT)
    if type(error).__name__ != "ApiRequestError":
        return safe_diagnostic_text(text)
    # Some clients put the complete HTTP response into str(error). Keep the
    # provider's error fields, never model output/messages from that response.
    try:
        raw = str(error)
    except Exception:
        return text
    body = raw.rsplit("\n原始响应：", 1)[-1] if "\n原始响应：" in raw else raw[raw.find("{"):] if "{" in raw else ""
    if body:
        prefix = raw.split("{", 1)[0].split("\n原始响应：", 1)[0].strip()
        try:
            payload = json.loads(body)
        except (ValueError, TypeError):
            payload = None
        if isinstance(payload, dict):
            source = payload.get("error", payload)
            if isinstance(source, str):
                return safe_diagnostic_text(prefix + " " + source)
            if isinstance(source, dict):
                fields = [f"{key}: {source[key]}" for key in ("message", "code", "type", "status") if isinstance(source.get(key), (str, int, float))]
                return safe_diagnostic_text(prefix + " " + "; ".join(fields))
    if "格式无法解析" in raw:
        return safe_diagnostic_text(raw.split("：", 1)[0] + "（响应正文未写入日志）")
    return safe_diagnostic_text(text)


def exception_frames(error: BaseException) -> list[str]:
    result = []
    tb = error.__traceback__
    while tb is not None:
        frame = tb.tb_frame
        module = re.sub(r"[^A-Za-z0-9_.-]", "_", str(frame.f_globals.get("__name__", "unknown")))
        function = re.sub(r"[^A-Za-z0-9_.<>-]", "_", frame.f_code.co_name)
        result.append(f"{module}:{function}:{tb.tb_lineno}")
        tb = tb.tb_next
    return (["[earlier frames omitted]"] if len(result) > 32 else []) + result[-32:]


def exception_diagnostics(error: BaseException | object, *, reason_code: str, stage: str) -> dict[str, object]:
    try:
        return _exception_diagnostics(error, reason_code=reason_code, stage=stage)
    except Exception:
        # Diagnostic extraction must not turn a recoverable failure into a
        # second failure (third-party exception attributes may be properties).
        return {"diagnostic": safe_diagnostic_text(error), "error_type": type(error).__name__,
                "reason_code": reason_code, "stage": stage, "exception_stack": "[diagnostic extraction incomplete]"}


def _exception_diagnostics(error: BaseException | object, *, reason_code: str, stage: str) -> dict[str, object]:
    chain = exception_chain(error) if isinstance(error, BaseException) else [error]
    root = chain[-1]
    attributes: dict[str, object] = {
        "diagnostic": _exception_message(root) or type(root).__name__,
        "error_type": type(error).__name__,
        "cause_type": type(root).__name__,
        "reason_code": reason_code,
        "stage": str(getattr(error, "stage", "") or stage),
    }
    if isinstance(error, BaseException):
        summaries = [f"{type(item).__name__}: {_exception_message(item)}" for item in chain]
        provider_error = next((item for item in chain if type(item).__name__ == "ApiRequestError"), None)
        if provider_error is not None:
            attributes["diagnostic"] = _exception_message(provider_error)
        separator = "\nException: " if any(isinstance(item, BaseExceptionGroup) for item in chain) else "\nCaused by: "
        attributes["exception_chain"] = bounded_text(separator.join(summaries), TRACE_LIMIT)
        if len(chain) == 16:
            attributes["exception_chain"] = bounded_text(str(attributes["exception_chain"]) + "\n[exception limit: 16]", TRACE_LIMIT)
        stacks = []
        for item, summary in zip(chain, summaries):
            frames = exception_frames(item)
            if frames:
                stacks.append(summary + "\n" + "\n".join(f"  at {frame}" for frame in frames))
        if stacks:
            attributes["exception_stack"] = bounded_text("\n\n".join(stacks), TRACE_LIMIT)
        frames = exception_frames(root) or exception_frames(error)
        if frames:
            attributes["exception_site"] = frames[-1]
        for key in ("errno", "winerror"):
            value = getattr(root, key, None)
            if isinstance(value, int):
                attributes[key] = value
        recovery = getattr(error, "recovery_error", None)
        if isinstance(recovery, BaseException):
            attributes["recovery_diagnostic"] = safe_diagnostic_text(
                f"{type(recovery).__name__}: {_exception_message(recovery)}\n" + "\n".join(f"  at {frame}" for frame in exception_frames(recovery)), TRACE_LIMIT)
        # Plugin RPC exceptions carry an already bounded remote diagnostic. Keep
        # the worker's original type and frames as well as the host propagation.
        remote = getattr(root, "diagnostics", None)
        if isinstance(remote, dict):
            for key in ("diagnostic", "cause_type"):
                if isinstance(remote.get(key), str):
                    attributes[key] = safe_diagnostic_text(remote[key])
            for key in ("exception_chain", "exception_stack"):
                if isinstance(remote.get(key), str):
                    attributes[key] = bounded_text(str(attributes.get(key, "")) + "\nRemote:\n" + safe_diagnostic_text(remote[key], TRACE_LIMIT), TRACE_LIMIT)
    return attributes
