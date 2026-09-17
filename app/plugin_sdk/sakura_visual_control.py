"""Bound visual control values shared by persistence and public reply projection."""

from __future__ import annotations

import json
import re


def validate_visual_control(value: object) -> dict:
    if not isinstance(value, dict) or set(value) - {"version", "resourceId", "bindingId", "state", "actions"}:
        raise ValueError("VISUAL_CONTROL_INVALID")
    if (type(value.get("version")) is not int or value["version"] != 1
        or not isinstance(value.get("bindingId"), str) or not re.fullmatch(r"[0-9a-f]{32}", value["bindingId"])
        or not isinstance(value.get("resourceId"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value["resourceId"])
        or not ({"state", "actions"} & set(value))
        or not isinstance(value.get("actions", []), list) or len(value.get("actions", [])) > 32):
        raise ValueError("VISUAL_CONTROL_INVALID")
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > 65536:
            raise ValueError("VISUAL_CONTROL_INVALID")
        return json.loads(encoded)
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise ValueError("VISUAL_CONTROL_INVALID") from error


def raw_visual_control(value: object) -> object:
    """Keep invalid input distinguishable from absence without losing valid text."""
    if value is None:
        return None
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) <= 65536:
            return json.loads(encoded)
    except (TypeError, ValueError, OverflowError, RecursionError):
        pass
    return {}
