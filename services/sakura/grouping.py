"""Stable grouping projections; original reports remain unchanged."""

import json
import re


_CHARACTER_SCOPE = re.compile(
    r"((?:sakura-character://localhost|https?://sakura-character\.localhost)/(?:module|v1)/)"
    r"[a-f0-9]+/[a-f0-9]{32}(?=[/-])", re.IGNORECASE,
)
_HOME_USER = re.compile(r"([A-Z]:/Users/|/Users/|/home/)[^/\s\"']+", re.IGNORECASE)
_PYTHON_SOURCE = re.compile(r'(File ")[^"\n]*?/(app/|plugins/|desktop/frontend/)([^"\n]+")')
_PYTHON_LINE = re.compile(r'(File "[^"\n]+", line )\d+')
_SCRIPT_LINE = re.compile(r"((?:https?|tauri|sakura-character)://[^\s)]+?):\d+(?::\d+)?(?=\)?(?:\n|$))")


def _text(value, *, stack=False):
    if not isinstance(value, str):
        return value
    value = value.replace("\\", "/")
    value = _CHARACTER_SCOPE.sub(r"\1<generation>/<binding>", value)
    value = _HOME_USER.sub(r"\1<user>", value)
    if stack:
        value = _PYTHON_SOURCE.sub(r"\1\2\3", value)
        value = _PYTHON_LINE.sub(r"\1<line>", value)
        value = _SCRIPT_LINE.sub(r"\1:<line>", value)
    return value


def error_group_key(report):
    error, details, evidence = report["error"], report["details"], report["evidence"]
    context = [evidence.get(field) for field in (
        "plugin_id", "section_id", "validation_field", "detail_stage", "command", "service_key",
        "error_type", "cause_type", "cause_code", "slot_id", "provider_id", "backend",
    )]
    frames = [{key: _text(value) for key, value in frame.items() if key not in {"line", "column"}}
              for frame in report.get("stack", [])]
    return json.dumps([
        error["component"], error["event"], error["code"], error.get("exceptionType"),
        details.get("reasonCode"), details.get("stage"),
        _text(evidence.get("diagnostic")), _text(evidence.get("exception_stack"), stack=True),
        _text(evidence.get("exception_chain")), frames, context,
    ], ensure_ascii=False, separators=(",", ":"))
