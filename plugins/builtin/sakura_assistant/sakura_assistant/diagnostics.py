"""Default Assistant diagnostics use the public plugin logger and explicit operation IDs."""
from contextvars import ContextVar
import re
import traceback

_operation = ContextVar("assistant_operation", default="")
_logger = None
_secrets = []

def configure(logger):
    global _logger
    _logger = logger

def add_secret(value):
    if value and value not in _secrets:
        _secrets.append(value)

def get_interaction_id():
    return _operation.get()

def safe_diagnostic_text(value, max_chars=8000, *, secrets=(), **kwargs):
    text = str(value)
    for secret in [*_secrets, *secrets]:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return re.sub(r"(?i)(authorization|api[_-]?key|token|password)([=: ]+)[^\s,;]+", r"\1\2[REDACTED]", text)[:max_chars]

def diagnostic_attributes(error, **attributes):
    return {**attributes, "error_type": type(error).__name__, "error_message": safe_diagnostic_text(error),
            "traceback": safe_diagnostic_text("".join(traceback.format_exception(error))),
            "cause": safe_diagnostic_text(error.__cause__) if error.__cause__ else None}

def log_event(channel, message, attributes=None, *, severity="info", event=None, **kwargs):
    if _logger is not None:
        fields = {"channel": channel, "operation_id": get_interaction_id(), **(attributes or {})}
        if event:
            fields["event"] = event
        getattr(_logger, severity if severity in {"debug", "info", "warning", "error"} else "info")(message, fields=fields)

def log_body_enabled():
    return False

def summarize_messages(messages, **kwargs):
    return [{"role": str(item.get("role", "")), "content_chars": len(str(item.get("content", "")))} for item in messages]

def submit_telemetry_model_call(candidate):
    if _logger is not None:
        return _logger.model_call(candidate)
    return False
