from __future__ import annotations

import inspect
import json
from pathlib import Path

from app.config.character_loader import CharacterRegistry
from app.core_host import character_presentation
from app.core_host.character_presentation import (
    DEFAULT_PORTRAIT_KEY,
    portrait_resource_id,
    project_character_presentation,
)


REPO_ROOT = Path(__file__).parents[2]


def test_real_sakura_and_navi_project_identity_theme_and_every_portrait_without_paths() -> None:
    registry = CharacterRegistry(REPO_ROOT)
    for character_id in ("Sakura", "N.A.V.I."):
        profile = registry.get(character_id)
        projected = project_character_presentation(profile)

        assert projected["characterId"] == profile.id
        assert projected["displayName"] == profile.display_name
        assert projected["initialMessage"] == profile.initial_message
        assert projected["defaultPortraitKey"] == DEFAULT_PORTRAIT_KEY
        assert projected["portraitKeys"] == [
            DEFAULT_PORTRAIT_KEY,
            *sorted(profile.expression_portraits),
        ]
        assert set(projected["portraitResourceIds"]) == set(projected["portraitKeys"])
        for key, resource_id in projected["portraitResourceIds"].items():
            assert resource_id == portrait_resource_id(profile.id, key)
            assert "/" not in resource_id
            assert "\\" not in resource_id

        serialized = json.dumps(projected, ensure_ascii=False)
        assert str(REPO_ROOT) not in serialized
        assert "characters/" not in serialized
        assert "characters\\" not in serialized
        assert set(projected["themeTokens"]) == {
            "primary",
            "primaryHover",
            "accent",
            "text",
            "secondaryText",
            "mutedText",
            "pageBackground",
            "panelBackground",
            "inputBackground",
            "bubbleBackground",
            "border",
        }


def test_projection_is_deterministic_and_resource_ids_are_utf8_opaque() -> None:
    profile = CharacterRegistry(REPO_ROOT).get("Sakura")
    assert project_character_presentation(profile) == project_character_presentation(profile)
    assert portrait_resource_id("Sakura", "开心") == (
        "character-v1-53616b757261-portrait-e5bc80e5bf83"
    )


def test_core_host_projection_module_is_qt_free() -> None:
    source = inspect.getsource(character_presentation)
    assert "PySide6" not in source
    assert "app.ui" not in source
    assert "default_portrait_path" not in source
    assert "expression_portraits" in source
