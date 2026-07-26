"""Qt-free public character presentation projection for Runtime v2."""

from __future__ import annotations

from typing import Final

from app.config.character_loader import CharacterProfile
from app.config.models import DEFAULT_THEME_SETTINGS, theme_colors_to_mapping


PRESENTATION_SCHEMA_VERSION: Final = 1
DEFAULT_PORTRAIT_KEY: Final = "__default__"

_THEME_TOKEN_NAMES: Final = {
    "primary_color": "primary",
    "primary_hover_color": "primaryHover",
    "accent_color": "accent",
    "text_color": "text",
    "secondary_text_color": "secondaryText",
    "muted_text_color": "mutedText",
    "page_background_color": "pageBackground",
    "panel_background_color": "panelBackground",
    "input_background_color": "inputBackground",
    "bubble_background_color": "bubbleBackground",
    "border_color": "border",
}


def portrait_resource_id(character_id: str, portrait_key: str) -> str:
    """Return a stable opaque ID; never include a path in the public DTO."""

    character_hex = character_id.encode("utf-8").hex()
    key_hex = portrait_key.encode("utf-8").hex()
    return f"character-v1-{character_hex}-portrait-{key_hex}"


def project_character_presentation(profile: CharacterProfile) -> dict[str, object]:
    """Project the current package into the path-free Runtime v2 UI contract."""

    expression_keys = sorted(profile.expression_portraits)
    portrait_keys = [DEFAULT_PORTRAIT_KEY, *expression_keys]
    theme = theme_colors_to_mapping(profile.theme_settings or DEFAULT_THEME_SETTINGS)
    return {
        "schemaVersion": PRESENTATION_SCHEMA_VERSION,
        "characterId": profile.id,
        "displayName": profile.display_name,
        "initialMessage": profile.initial_message,
        "themeTokens": {
            public_name: theme[source_name]
            for source_name, public_name in _THEME_TOKEN_NAMES.items()
        },
        "defaultPortraitKey": DEFAULT_PORTRAIT_KEY,
        "portraitKeys": portrait_keys,
        "portraitResourceIds": {
            key: portrait_resource_id(profile.id, key) for key in portrait_keys
        },
    }
