from __future__ import annotations

import json
import re
from pathlib import Path

from app.config.models import DEFAULT_THEME_SETTINGS, theme_colors_to_mapping


ROOT = Path(__file__).resolve().parents[2]
THEME = {
    "primary": "#4b9ac4",
    "primaryHover": "#3b83aa",
    "accent": "#e36c96",
    "text": "#27445a",
    "secondaryText": "#54768b",
    "mutedText": "#7d99a9",
    "pageBackground": "#f8fcfe",
    "panelBackground": "#eaf5fa",
    "inputBackground": "#ffffff",
    "bubbleBackground": "#e3f1f7",
    "border": "#accfde",
}
LEGACY_KEYS = {
    "primary": "primary_color",
    "primaryHover": "primary_hover_color",
    "accent": "accent_color",
    "text": "text_color",
    "secondaryText": "secondary_text_color",
    "mutedText": "muted_text_color",
    "pageBackground": "page_background_color",
    "panelBackground": "panel_background_color",
    "inputBackground": "input_background_color",
    "bubbleBackground": "bubble_background_color",
    "border": "border_color",
}


def test_default_theme_is_one_cross_language_product_contract() -> None:
    python_theme = theme_colors_to_mapping(DEFAULT_THEME_SETTINGS)
    assert python_theme == {LEGACY_KEYS[key]: value for key, value in THEME.items()}

    theme_js = (ROOT / "desktop/frontend/core/theme.js").read_text(encoding="utf-8")
    main_css = (ROOT / "desktop/frontend/styles.css").read_text(encoding="utf-8")
    settings_css = (ROOT / "desktop/frontend/settings/styles.css").read_text(encoding="utf-8")
    history_css = (ROOT / "desktop/frontend/history/styles.css").read_text(encoding="utf-8")
    rust = (ROOT / "desktop/src-tauri/src/character_presentation.rs").read_text(encoding="utf-8")

    for public_key, color in THEME.items():
        assert re.search(rf"\b{re.escape(public_key)}:\s*\"{color}\"", theme_js)
        assert f'("{public_key}", "{LEGACY_KEYS[public_key]}", "{color}")' in rust
        assert color in main_css
        assert color in settings_css
        assert color in history_css


def test_history_window_has_event_capability_for_refresh_and_bootstrap() -> None:
    capability = json.loads(
        (ROOT / "desktop/src-tauri/capabilities/default.json").read_text(encoding="utf-8")
    )

    assert "history" in capability["windows"]
    assert "core:event:allow-listen" in capability["permissions"]
