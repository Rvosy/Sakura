from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config.appearance_settings import (
    AppearanceSettingsError,
    THEME_FIELDS,
    load_runtime_v2_appearance,
    parse_appearance_document,
    runtime_v2_ui_config_path,
)
from app.config.settings_service import AppSettingsService


def _theme(seed: str = "11") -> dict[str, str]:
    return {field: f"#{seed}{index:04x}"[-7:] for index, field in enumerate(THEME_FIELDS)}


def _document(**settings: object) -> dict[str, object]:
    return {"schema_version": 1, "domain": "ui", "settings": settings}


def test_parse_narrow_appearance_document_normalizes_theme() -> None:
    theme = {field: "#A1B2C3" for field in THEME_FIELDS}
    parsed = parse_appearance_document(
        _document(
            portrait_scale_percent=125,
            speech_font_size=20,
            name_font_size=14,
            input_font_size=16,
            button_font_size=16,
            character_theme_overrides={"N.A.V.I.": theme},
        )
    )
    assert parsed.portrait_scale_percent == 125
    assert parsed.character_theme_overrides["N.A.V.I."]["primary_color"] == "#a1b2c3"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("portrait_scale_percent", 49),
        ("portrait_scale_percent", True),
        ("speech_font_size", 25),
        ("name_font_size", "13"),
        ("input_font_size", 11),
        ("button_font_size", 21),
    ],
)
def test_rejects_invalid_scalar_fields(field: str, value: object) -> None:
    with pytest.raises(AppearanceSettingsError, match=field):
        parse_appearance_document(_document(**{field: value}))


def test_rejects_future_schema_and_partial_or_invalid_theme() -> None:
    with pytest.raises(AppearanceSettingsError, match="SCHEMA_UNSUPPORTED"):
        parse_appearance_document({"schema_version": 2, "domain": "ui", "settings": {}})
    with pytest.raises(AppearanceSettingsError, match="THEME_FIELDS_INVALID"):
        parse_appearance_document(
            _document(character_theme_overrides={"Sakura": {"primary_color": "#112233"}})
        )
    invalid = _theme()
    invalid["accent_color"] = "url(secret)"
    with pytest.raises(AppearanceSettingsError, match="accent_color"):
        parse_appearance_document(_document(character_theme_overrides={"Sakura": invalid}))


def test_legacy_settings_service_reads_only_approved_runtime_v2_overlay(tmp_path: Path) -> None:
    config = tmp_path / "data" / "config"
    config.mkdir(parents=True)
    (config / "system_config.yaml").write_text(
        "ui:\n  portrait_scale_percent: 80\n  control_panel_width: 520\n",
        encoding="utf-8",
    )
    runtime_path = runtime_v2_ui_config_path(tmp_path)
    runtime_path.parent.mkdir(parents=True)
    runtime_path.write_text(
        json.dumps(
            _document(
                portrait_scale_percent=130,
                speech_font_size=21,
                character_theme_overrides={"Sakura": _theme("22")},
                credential="must-not-be-projected",
            )
        ),
        encoding="utf-8",
    )

    ui = AppSettingsService(tmp_path).load_system_values("ui")
    assert ui["portrait_scale_percent"] == 130
    assert ui["speech_font_size"] == 21
    assert ui["control_panel_width"] == 520
    assert "credential" not in ui
    assert AppSettingsService(tmp_path).load_character_theme_override("Sakura") is not None


def test_legacy_reader_ignores_missing_corrupt_or_future_document(tmp_path: Path) -> None:
    assert load_runtime_v2_appearance(tmp_path) is None
    path = runtime_v2_ui_config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{", encoding="utf-8")
    assert load_runtime_v2_appearance(tmp_path) is None
    path.write_text(json.dumps({"schema_version": 99, "domain": "ui", "settings": {}}))
    assert load_runtime_v2_appearance(tmp_path) is None


def test_unrelated_runtime_v2_ui_fields_do_not_override_legacy_values(tmp_path: Path) -> None:
    config = tmp_path / "data" / "config"
    config.mkdir(parents=True)
    (config / "system_config.yaml").write_text(
        "ui:\n  portrait_scale_percent: 88\n  speech_font_size: 18\n",
        encoding="utf-8",
    )
    path = runtime_v2_ui_config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(_document(theme="fixture", font_scale=1.0, typewriter_cps=30)),
        encoding="utf-8",
    )
    ui = AppSettingsService(tmp_path).load_system_values("ui")
    assert ui["portrait_scale_percent"] == 88
    assert ui["speech_font_size"] == 18
