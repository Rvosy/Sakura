"""视觉观察标识与 Timeline 摘要投影。"""

from __future__ import annotations

import re
import uuid
from typing import Any


TIMELINE_VISUAL_SUMMARY_CHARS = 4000

_SENSITIVE_PATTERNS = (
    re.compile(r"\b(api[_-]?key|secret|token|password|passwd|pwd)\b\s*[:=：]\s*\S+", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"),
    re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
    re.compile(r"\b\d{17}[\dXx]\b"),
    re.compile(r"(密码|口令|密钥|令牌|银行卡|信用卡|身份证)\s*[:：]\s*\S+"),
)


def generate_visual_observation_id() -> str:
    return f"vis_{uuid.uuid4().hex[:10]}"


def sanitize_timeline_visual_summary(summary: dict[str, Any]) -> dict[str, Any] | None:
    """Return a bounded, redacted semantic observation suitable for Timeline.

    Raw screenshots and resource paths never enter this projection.  The text is
    still treated as untrusted observation evidence by downstream consumers.
    """

    if not _summary_has_content(summary):
        return None
    redacted, sensitive_redacted = _redact_record_dict(summary)
    lines: list[str] = []
    summary_text = _text_value(redacted.get("summary"))
    if summary_text:
        lines.append(f"画面摘要：{summary_text}")
    visible_texts = _string_list(redacted.get("visible_texts"))
    if visible_texts:
        lines.append(f"可见文字：{_format_list(visible_texts)}")
    uncertain_texts = _string_list(redacted.get("uncertain_texts"))
    if uncertain_texts:
        lines.append(f"不确定文字：{_format_list(uncertain_texts)}")
    notable_elements = _string_list(redacted.get("notable_elements"))
    if notable_elements:
        lines.append(f"关键元素：{_format_list(notable_elements)}")
    text = "\n".join(lines).strip()[:TIMELINE_VISUAL_SUMMARY_CHARS].rstrip()
    if not text:
        return None
    return {
        "text": text,
        "confidence": _confidence_value(redacted.get("confidence")),
        "sensitive_redacted": bool(redacted.get("sensitive_redacted"))
        or sensitive_redacted,
    }


def _summary_has_content(summary: dict[str, Any]) -> bool:
    return bool(
        _text_value(summary.get("summary"))
        or _string_list(summary.get("visible_texts"))
        or _string_list(summary.get("uncertain_texts"))
        or _string_list(summary.get("notable_elements"))
    )


def _redact_record_dict(data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    redacted = dict(data)
    sensitive_redacted = False
    for key in ("user_text", "summary"):
        redacted[key], changed = _redact_text(_text_value(redacted.get(key)))
        sensitive_redacted = sensitive_redacted or changed
    for key in ("visible_texts", "uncertain_texts", "notable_elements"):
        redacted[key], changed = _redact_text_list(redacted.get(key))
        sensitive_redacted = sensitive_redacted or changed
    if sensitive_redacted:
        redacted["sensitive_redacted"] = True
    redacted.pop("data_url", None)
    redacted.pop("image_url", None)
    return redacted, sensitive_redacted


def _redact_text_list(value: Any) -> tuple[list[str], bool]:
    result: list[str] = []
    changed = False
    for item in _string_list(value):
        redacted, item_changed = _redact_text(item)
        if redacted:
            result.append(redacted)
        changed = changed or item_changed
    return result, changed


def _redact_text(text: str) -> tuple[str, bool]:
    redacted = text
    for pattern in _SENSITIVE_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted, redacted != text


def _format_list(items: list[str]) -> str:
    return "；".join(item for item in items if item.strip()) or "无"


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _text_value(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _confidence_value(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return max(0.0, min(1.0, number))
