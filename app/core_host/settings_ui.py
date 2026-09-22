"""Declarative settings placement; configuration remains owned by its plugin."""
from __future__ import annotations

import math
import re


HOST_PAGES = frozenset({"character", "appearance", "providers", "model", "voice", "memory", "interaction", "tools", "plugins", "system", "about"})
GROUPS = frozenset({"character", "ai", "behavior", "system"})
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def identifier(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("SETTINGS_UI_INVALID")
    return value


def order(value=100):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError("SETTINGS_UI_INVALID")
    return value


def page(owner, raw):
    if not isinstance(raw, dict) or owner == "host":
        raise ValueError("SETTINGS_PAGE_INVALID")
    local_id = identifier(raw.get("pageId"))
    title = raw.get("title")
    if not isinstance(title, str) or not title.strip() or len(title) > 120 or raw.get("group") not in GROUPS:
        raise ValueError("SETTINGS_PAGE_INVALID")
    regions = raw.get("regions", [])
    if not isinstance(regions, list):
        raise ValueError("SETTINGS_PAGE_INVALID")
    return {"pageId": f"{owner}:{local_id}", "title": title, "group": raw["group"],
            "icon": identifier(raw.get("icon", "settings")), "order": order(raw.get("order", 100)),
            "regions": list(dict.fromkeys(identifier(item) for item in regions))}


def placement(raw):
    if not isinstance(raw, dict):
        raise ValueError("SETTINGS_PLACEMENT_INVALID")
    target = raw.get("pageId")
    if not isinstance(target, str) or ":" not in target:
        raise ValueError("SETTINGS_PLACEMENT_INVALID")
    owner, local = target.split(":", 1)
    identifier(owner)
    identifier(local)
    if owner == "host" and local not in HOST_PAGES:
        raise ValueError("SETTINGS_PLACEMENT_INVALID")
    return {"pageId": target, "region": identifier(raw.get("region", "content")),
            "order": order(raw.get("order", 100))}


def presentation(raw):
    if raw is None:
        return None
    if not isinstance(raw, dict) or raw.get("component", "form") not in {"form", "connection-editor"}:
        raise ValueError("SETTINGS_PRESENTATION_INVALID")
    result = {"component": raw.get("component", "form"), "collapsible": raw.get("collapsible", False)}
    if not isinstance(result["collapsible"], bool):
        raise ValueError("SETTINGS_PRESENTATION_INVALID")
    result["alignedUnits"] = raw.get("alignedUnits") is True
    # Bindings reference declared fields/actions, never executable markup.
    for key in ("timeoutSection", "timeoutField", "serviceKey", "group", "statusAction", "valueField", "requestField", "resultField", "probeAction", "cancelAction"):
        if key in raw:
            result[key] = identifier(raw[key])
    return result
