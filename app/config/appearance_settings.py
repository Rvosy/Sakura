"""Runtime v2 character appearance DTO and reader."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any, Final


APPEARANCE_SCHEMA_VERSION: Final = 1
APPEARANCE_DOMAIN: Final = "ui"
PORTRAIT_SCALE_MIN_PERCENT: Final = 50
PORTRAIT_SCALE_MAX_PERCENT: Final = 150
PORTRAIT_SCALE_DEFAULT_PERCENT: Final = 100

FONT_LIMITS: Final = {
    "speech_font_size": (10, 24, 19),
    "name_font_size": (10, 20, 13),
    "input_font_size": (12, 20, 15),
    "button_font_size": (12, 20, 15),
}

THEME_FIELDS: Final = (
    "primary_color",
    "primary_hover_color",
    "accent_color",
    "text_color",
    "secondary_text_color",
    "muted_text_color",
    "page_background_color",
    "panel_background_color",
    "input_background_color",
    "bubble_background_color",
    "border_color",
)

_CHARACTER_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


class AppearanceSettingsError(ValueError):
    """Raised when the Runtime v2 UI document violates the frozen narrow schema."""


@dataclass(frozen=True)
class AppearanceSettings:
    portrait_scale_percent: int = PORTRAIT_SCALE_DEFAULT_PERCENT
    speech_font_size: int = FONT_LIMITS["speech_font_size"][2]
    name_font_size: int = FONT_LIMITS["name_font_size"][2]
    input_font_size: int = FONT_LIMITS["input_font_size"][2]
    button_font_size: int = FONT_LIMITS["button_font_size"][2]
    character_theme_overrides: dict[str, dict[str, str]] = field(default_factory=dict)
    present_fields: frozenset[str] = field(default_factory=frozenset, repr=False, compare=False)

def parse_appearance_document(document: object) -> AppearanceSettings:
    root = _mapping(document, "document")
    if root.get("schema_version") != APPEARANCE_SCHEMA_VERSION:
        raise AppearanceSettingsError("APPEARANCE_SCHEMA_UNSUPPORTED")
    if root.get("domain") != APPEARANCE_DOMAIN:
        raise AppearanceSettingsError("APPEARANCE_DOMAIN_INVALID")
    settings = _mapping(root.get("settings"), "settings")

    portrait_scale = _bounded_int(
        settings.get("portrait_scale_percent", PORTRAIT_SCALE_DEFAULT_PERCENT),
        PORTRAIT_SCALE_MIN_PERCENT,
        PORTRAIT_SCALE_MAX_PERCENT,
        "portrait_scale_percent",
    )
    fonts = {
        name: _bounded_int(settings.get(name, default), minimum, maximum, name)
        for name, (minimum, maximum, default) in FONT_LIMITS.items()
    }
    overrides = _theme_overrides(settings.get("character_theme_overrides", {}))
    return AppearanceSettings(
        portrait_scale_percent=portrait_scale,
        character_theme_overrides=overrides,
        present_fields=frozenset(settings).intersection(
            {"portrait_scale_percent", *FONT_LIMITS, "character_theme_overrides"}
        ),
        **fonts,
    )


def load_appearance_settings(path: Path) -> AppearanceSettings:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AppearanceSettingsError("APPEARANCE_DOCUMENT_INVALID") from exc
    return parse_appearance_document(document)


def _mapping(value: object, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AppearanceSettingsError(f"APPEARANCE_FIELD_INVALID:{field_name}")
    return dict(value)


def _bounded_int(value: object, minimum: int, maximum: int, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise AppearanceSettingsError(f"APPEARANCE_FIELD_INVALID:{field_name}")
    return value


def _theme_overrides(value: object) -> dict[str, dict[str, str]]:
    raw = _mapping(value, "character_theme_overrides")
    result: dict[str, dict[str, str]] = {}
    for character_id, theme_value in raw.items():
        if not isinstance(character_id, str) or not _CHARACTER_ID.fullmatch(character_id):
            raise AppearanceSettingsError("APPEARANCE_CHARACTER_ID_INVALID")
        theme = _mapping(theme_value, f"character_theme_overrides.{character_id}")
        if set(theme) != set(THEME_FIELDS):
            raise AppearanceSettingsError("APPEARANCE_THEME_FIELDS_INVALID")
        normalized: dict[str, str] = {}
        for field_name in THEME_FIELDS:
            color = theme.get(field_name)
            if not isinstance(color, str) or not _HEX_COLOR.fullmatch(color):
                raise AppearanceSettingsError(f"APPEARANCE_FIELD_INVALID:{field_name}")
            normalized[field_name] = color.lower()
        result[character_id] = normalized
    return result
