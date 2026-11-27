"""Default Assistant diagnostics use the public plugin logger and explicit operation IDs."""
from contextvars import ContextVar
import re
import traceback

_operation = ContextVar("assistant_operation", default="")
_logger = None

def configure(logger):
    global _logger
    _logger = logger

def get_interaction_id():
    return _operation.get()

_SECRET_RE = re.compile(
    r"""(?ix)
    (\b(?:api[_-]?key|authorization|cookie|password|secret|(?:access[_-]?|refresh[_-]?)?token|credential)
    ["']?\s*[:=]\s*(?:(?:bearer|basic)\s+)?)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;}&]+)
    |(\bbearer\s+)[^\s,;}&]+|\bsk-[\w.-]{6,}
    """
)

def safe_diagnostic_text(value, max_chars=8000, *, secrets=(), **kwargs):
    text = str(value)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = _SECRET_RE.sub(lambda m: (m[1] or m[2] or "") + "[REDACTED]", text)
    text = re.sub(r"([a-zA-Z][a-zA-Z0-9+.-]*://)[^/\s@]+@", r"\1[REDACTED]@", text)
    return text[:max_chars]

def diagnostic_attributes(error, **attributes):
    return {**attributes, "error_type": type(error).__name__, "diagnostic": safe_diagnostic_text(error),
            "exception_stack": safe_diagnostic_text("".join(traceback.format_exception(error))),
            "exception_chain": safe_diagnostic_text(error.__cause__) if error.__cause__ else ""}

def log_event(channel, message, attributes=None, *, severity="info", event=None, **kwargs):
    if _logger is not None:
        fields = {"channel": channel, "operation_id": get_interaction_id(), **(attributes or {})}
        if event:
            fields["event"] = event
        getattr(_logger, severity if severity in {"debug", "info", "warning", "error"} else "info")(message, fields=fields)

def summarize_messages(messages, **kwargs):
    return [{"role": str(item.get("role", "")), "content_chars": len(str(item.get("content", "")))} for item in messages]

def submit_telemetry_model_call(candidate):
    if _logger is not None:
        return _logger.model_call(candidate)
    return False
