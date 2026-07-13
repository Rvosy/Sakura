from __future__ import annotations

from app.ui.theme import DEFAULT_THEME_SETTINGS, ThemeSettings, build_pet_window_stylesheet
from app.config.defaults import (
    DEFAULT_SPEECH_FONT_SIZE,
    DEFAULT_NAME_FONT_SIZE,
    DEFAULT_INPUT_FONT_SIZE,
    DEFAULT_BUTTON_FONT_SIZE,
)


def pet_window_stylesheet(
    settings: ThemeSettings = DEFAULT_THEME_SETTINGS,
    *,
    speech_font_size: int = DEFAULT_SPEECH_FONT_SIZE,
    name_font_size: int = DEFAULT_NAME_FONT_SIZE,
    input_font_size: int = DEFAULT_INPUT_FONT_SIZE,
    button_font_size: int = DEFAULT_BUTTON_FONT_SIZE,
) -> str:
    return build_pet_window_stylesheet(
        settings,
        speech_font_size=speech_font_size,
        name_font_size=name_font_size,
        input_font_size=input_font_size,
        button_font_size=button_font_size,
    )


PET_WINDOW_STYLEHEET = pet_window_stylesheet()