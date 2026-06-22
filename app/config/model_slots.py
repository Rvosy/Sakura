from __future__ import annotations

from dataclasses import dataclass

from app.config.models import (
    MODEL_SLOT_CHAT,
    MODEL_SLOT_FALLBACKS,
    ApiConfigProfile,
    ModelSelectionSettings,
    ModelSlotSelection,
)
from app.llm.api_client import ApiSettings


@dataclass(frozen=True)
class ResolvedModelSlot:
    slot: str
    source_slot: str
    selection: ModelSlotSelection
    settings: ApiSettings


def normalize_provider_models(models: object) -> tuple[str, ...]:
    names: list[str] = []
    if isinstance(models, list | tuple):
        for item in models:
            if isinstance(item, str):
                name = item.strip()
            elif isinstance(item, dict):
                name = str(item.get("name", "")).strip()
            else:
                name = ""
            if name and name not in names:
                names.append(name)
    return tuple(names)


def resolve_model_slot(
    profiles: list[ApiConfigProfile],
    selections: ModelSelectionSettings,
    slot: str,
    base_settings: ApiSettings,
) -> ResolvedModelSlot | None:
    for candidate in (slot, *MODEL_SLOT_FALLBACKS.get(slot, ())):
        selection = selections.get(candidate)
        if selection is None or not selection.configured:
            continue
        profile = find_profile(profiles, selection.profile_id)
        if profile is None:
            continue
        if selection.model.strip() not in profile.models:
            continue
        return ResolvedModelSlot(
            slot=slot,
            source_slot=candidate,
            selection=selection,
            settings=api_settings_from_selection(
                profile,
                selection.model,
                base_settings,
                include_dialogue_params=candidate == MODEL_SLOT_CHAT,
            ),
        )
    return None


def api_settings_from_selection(
    profile: ApiConfigProfile,
    model: str,
    base_settings: ApiSettings,
    *,
    include_dialogue_params: bool = False,
) -> ApiSettings:
    return ApiSettings(
        base_url=profile.base_url.strip().rstrip("/"),
        api_key=profile.api_key.strip(),
        model=model.strip(),
        timeout_seconds=base_settings.timeout_seconds,
        temperature=base_settings.temperature if include_dialogue_params else None,
        top_p=base_settings.top_p if include_dialogue_params else None,
        max_tokens=base_settings.max_tokens if include_dialogue_params else None,
    )


def find_profile(
    profiles: list[ApiConfigProfile],
    profile_id: str,
) -> ApiConfigProfile | None:
    for profile in profiles:
        if profile.id == profile_id:
            return profile
    return None
