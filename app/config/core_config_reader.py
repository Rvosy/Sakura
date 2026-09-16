from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import yaml

from app.config.model_slots import resolve_model_slot
from app.config.models import (
    MODEL_SLOT_CHAT,
    ApiConfigProfile,
    ModelSelectionSettings,
    ModelSlotSelection,
)
from app.llm.api_client import ApiSettings as ClientApiSettings
from app.storage.paths import StoragePaths


SUPPORTED_CORE_CONFIG_VERSION = 1
_HOST_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


@dataclass(frozen=True)
class StableReadinessError:
    state: Literal["setup_required", "failed"]
    code: str
    message: str
    retryable: bool = False


@dataclass(frozen=True)
class ProviderSelection:
    api_settings: ClientApiSettings = field(repr=False)


@dataclass(frozen=True)
class CoreConfigReadResult:
    current_character_id: str | None
    provider_selection: ProviderSelection | None = field(repr=False)
    config_problem: StableReadinessError | None = None


_PROBLEM_DETAILS: dict[str, tuple[Literal["setup_required", "failed"], str]] = {
    "CORE_CONFIG_SETUP_REQUIRED": (
        "setup_required",
        "Core configuration setup is required.",
    ),
    "CONFIG_DATA_INVALID": (
        "failed",
        "Core configuration data is invalid.",
    ),
    "CONFIG_VERSION_UNSUPPORTED": (
        "failed",
        "Core configuration version is unsupported.",
    ),
    "PROVIDER_SETUP_REQUIRED": (
        "setup_required",
        "Provider configuration setup is required.",
    ),
}


def _stable_error(code: str) -> StableReadinessError:
    state, message = _PROBLEM_DETAILS[code]
    return StableReadinessError(state=state, code=code, message=message)


def _problem_result(
    code: str,
    *,
    current_character_id: str | None = None,
) -> CoreConfigReadResult:
    return CoreConfigReadResult(
        current_character_id=current_character_id,
        provider_selection=None,
        config_problem=_stable_error(code),
    )


def _read_required_system_mapping(
    path: Path,
) -> tuple[dict[str, object] | None, StableReadinessError | None]:
    if not path.exists():
        return {"config_version": SUPPORTED_CORE_CONFIG_VERSION}, None
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None, _stable_error("CONFIG_DATA_INVALID")
    if not content.strip():
        return None, _stable_error("CONFIG_DATA_INVALID")
    try:
        loaded = yaml.safe_load(content)
    except yaml.YAMLError:
        return None, _stable_error("CONFIG_DATA_INVALID")
    if not isinstance(loaded, Mapping):
        return None, _stable_error("CONFIG_DATA_INVALID")
    return dict(loaded), None


def _read_auxiliary_mapping(
    path: Path,
) -> tuple[dict[str, object] | None, StableReadinessError | None]:
    if not path.exists():
        return None, _stable_error("PROVIDER_SETUP_REQUIRED")
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None, _stable_error("CONFIG_DATA_INVALID")
    if not content.strip():
        return None, _stable_error("PROVIDER_SETUP_REQUIRED")
    try:
        loaded = yaml.safe_load(content)
    except yaml.YAMLError:
        return None, _stable_error("CONFIG_DATA_INVALID")
    if loaded is None:
        return None, _stable_error("PROVIDER_SETUP_REQUIRED")
    if not isinstance(loaded, Mapping):
        return None, _stable_error("CONFIG_DATA_INVALID")
    if not loaded:
        return None, _stable_error("PROVIDER_SETUP_REQUIRED")
    return dict(loaded), None


def _read_optional_characters_mapping(
    path: Path,
) -> tuple[dict[str, object] | None, StableReadinessError | None]:
    if not path.exists():
        return {}, None
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None, _stable_error("CONFIG_DATA_INVALID")
    if not content.strip():
        return {}, None
    try:
        loaded = yaml.safe_load(content)
    except yaml.YAMLError:
        return None, _stable_error("CONFIG_DATA_INVALID")
    if loaded is None:
        return {}, None
    if not isinstance(loaded, Mapping):
        return None, _stable_error("CONFIG_DATA_INVALID")
    return dict(loaded), None


def _read_current_character_id(
    config_dir: Path,
) -> tuple[str | None, StableReadinessError | None]:
    characters, problem = _read_optional_characters_mapping(
        config_dir / "characters.yaml"
    )
    if problem is not None:
        return None, problem
    assert characters is not None
    current_character_id = characters.get("current_character_id")
    if current_character_id is not None and not isinstance(current_character_id, str):
        return None, _stable_error("CONFIG_DATA_INVALID")
    current_character_id = (
        current_character_id.strip()
        if isinstance(current_character_id, str)
        else None
    )
    return current_character_id or None, None


def _problem_result_with_character(
    config_dir: Path,
    code: str,
) -> CoreConfigReadResult:
    if code != "PROVIDER_SETUP_REQUIRED":
        return _problem_result(code)
    current_character_id, problem = _read_current_character_id(config_dir)
    if problem is not None:
        return _problem_result(problem.code)
    return _problem_result(
        code,
        current_character_id=current_character_id,
    )


def _parse_profiles(
    data: Mapping[str, object],
) -> tuple[list[ApiConfigProfile] | None, StableReadinessError | None]:
    if "api_profiles" not in data:
        return None, _stable_error("PROVIDER_SETUP_REQUIRED")
    raw_profiles = data["api_profiles"]
    if not isinstance(raw_profiles, list):
        return None, _stable_error("CONFIG_DATA_INVALID")
    if not raw_profiles:
        return None, _stable_error("PROVIDER_SETUP_REQUIRED")

    profiles: list[ApiConfigProfile] = []
    for raw_profile in raw_profiles:
        if not isinstance(raw_profile, Mapping):
            return None, _stable_error("CONFIG_DATA_INVALID")

        text_fields: dict[str, str] = {}
        for name in ("id", "alias", "base_url", "api_key"):
            value = raw_profile.get(name, "")
            if not isinstance(value, str):
                return None, _stable_error("CONFIG_DATA_INVALID")
            text_fields[name] = value.strip()

        if "models" not in raw_profile:
            return None, _stable_error("PROVIDER_SETUP_REQUIRED")
        raw_models = raw_profile["models"]
        if not isinstance(raw_models, list):
            return None, _stable_error("CONFIG_DATA_INVALID")
        models: list[str] = []
        for raw_model in raw_models:
            if not isinstance(raw_model, Mapping):
                return None, _stable_error("CONFIG_DATA_INVALID")
            model_value = raw_model.get("name", "")
            if not isinstance(model_value, str):
                return None, _stable_error("CONFIG_DATA_INVALID")
            model_name = model_value.strip()
            if not model_name:
                return None, _stable_error("PROVIDER_SETUP_REQUIRED")
            if model_name not in models:
                models.append(model_name)

        profiles.append(
            ApiConfigProfile(
                id=text_fields["id"],
                alias=text_fields["alias"],
                base_url=text_fields["base_url"],
                api_key=text_fields["api_key"],
                models=tuple(models),
            )
        )
    return profiles, None


def _parse_model_selection(
    data: Mapping[str, object],
) -> tuple[ModelSelectionSettings | None, StableReadinessError | None]:
    if "model_slots" not in data:
        return ModelSelectionSettings(), None
    raw_slots = data["model_slots"]
    if not isinstance(raw_slots, Mapping):
        return None, _stable_error("CONFIG_DATA_INVALID")

    def parse_slot(
        slot_name: str,
        *,
        optional: bool,
    ) -> tuple[ModelSlotSelection | None, StableReadinessError | None]:
        if slot_name not in raw_slots:
            return None if optional else ModelSlotSelection(), None
        raw_slot = raw_slots[slot_name]
        if not isinstance(raw_slot, Mapping):
            return None, _stable_error("CONFIG_DATA_INVALID")
        profile_id = raw_slot.get("profile_id", "")
        model = raw_slot.get("model", "")
        if not isinstance(profile_id, str) or not isinstance(model, str):
            return None, _stable_error("CONFIG_DATA_INVALID")
        context_window = raw_slot.get("context_window_tokens")
        if context_window is not None and (
            isinstance(context_window, bool)
            or not isinstance(context_window, int)
            or not 4_096 <= context_window <= 2_000_000
        ):
            return None, _stable_error("CONFIG_DATA_INVALID")
        return ModelSlotSelection(
            profile_id=profile_id.strip(),
            model=model.strip(),
            context_window_tokens=context_window,
        ), None

    chat, problem = parse_slot(MODEL_SLOT_CHAT, optional=False)
    if problem is not None:
        return None, problem
    vision_chat, problem = parse_slot("vision_chat", optional=True)
    if problem is not None:
        return None, problem
    return (
        ModelSelectionSettings(
            chat=chat or ModelSlotSelection(),
            vision_chat=vision_chat,
        ),
        None,
    )


def _validate_provider_url(base_url: str) -> StableReadinessError | None:
    try:
        parsed = urlparse(base_url)
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        return _stable_error("PROVIDER_SETUP_REQUIRED")
    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        return _stable_error("PROVIDER_SETUP_REQUIRED")
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii").rstrip(".")
    except UnicodeError:
        return _stable_error("PROVIDER_SETUP_REQUIRED")
    if not ascii_hostname or len(ascii_hostname) > 253:
        return _stable_error("PROVIDER_SETUP_REQUIRED")
    if ":" not in ascii_hostname and any(
        _HOST_LABEL_PATTERN.fullmatch(label) is None
        for label in ascii_hostname.split(".")
    ):
        return _stable_error("PROVIDER_SETUP_REQUIRED")
    return None


class CoreConfigReader:
    def read(self, user_root: Path) -> CoreConfigReadResult:
        config_dir = StoragePaths(user_root).config_dir

        system, problem = _read_required_system_mapping(config_dir / "system_config.yaml")
        if problem is not None:
            return _problem_result(problem.code)
        assert system is not None
        version = system.get("config_version")
        if (
            isinstance(version, bool)
            or not isinstance(version, int)
            or version != SUPPORTED_CORE_CONFIG_VERSION
        ):
            return _problem_result("CONFIG_VERSION_UNSUPPORTED")

        api_data, problem = _read_auxiliary_mapping(config_dir / "api.yaml")
        if problem is not None:
            return _problem_result_with_character(config_dir, problem.code)
        assert api_data is not None

        selections, problem = _parse_model_selection(api_data)
        if problem is not None:
            return _problem_result_with_character(config_dir, problem.code)
        profiles, problem = _parse_profiles(api_data)
        if problem is not None:
            return _problem_result_with_character(config_dir, problem.code)
        assert profiles is not None and selections is not None

        raw_llm = api_data.get("llm", {})
        if raw_llm is None:
            raw_llm = {}
        if not isinstance(raw_llm, Mapping):
            return _problem_result_with_character(config_dir, "CONFIG_DATA_INVALID")
        timeout_seconds = raw_llm.get("timeout_seconds", 60)
        temperature = raw_llm.get("temperature")
        top_p = raw_llm.get("top_p")
        max_tokens = raw_llm.get("max_tokens")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or not 1 <= timeout_seconds <= 300
            or (
                max_tokens is not None
                and (
                    isinstance(max_tokens, bool)
                    or not isinstance(max_tokens, int)
                    or not 1 <= max_tokens <= 1_000_000
                )
            )
        ):
            return _problem_result_with_character(config_dir, "CONFIG_DATA_INVALID")
        for value, maximum in ((temperature, 2.0), (top_p, 1.0)):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not 0 <= value <= maximum
            ):
                return _problem_result_with_character(config_dir, "CONFIG_DATA_INVALID")
        base_settings = ClientApiSettings(
            base_url="",
            api_key="",
            model="",
            timeout_seconds=timeout_seconds,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
        )
        try:
            resolved = resolve_model_slot(
                profiles,
                selections,
                MODEL_SLOT_CHAT,
                base_settings,
            )
        except Exception:
            return _problem_result_with_character(config_dir, "CONFIG_DATA_INVALID")
        if resolved is None:
            return _problem_result_with_character(config_dir, "PROVIDER_SETUP_REQUIRED")
        settings = resolved.settings
        if not settings.base_url.strip() or not settings.api_key.strip() or not settings.model.strip():
            return _problem_result_with_character(config_dir, "PROVIDER_SETUP_REQUIRED")
        problem = _validate_provider_url(settings.base_url)
        if problem is not None:
            return _problem_result_with_character(config_dir, problem.code)

        current_character_id, problem = _read_current_character_id(config_dir)
        if problem is not None:
            return _problem_result(problem.code)

        return CoreConfigReadResult(
            current_character_id=current_character_id,
            provider_selection=ProviderSelection(api_settings=settings),
        )
