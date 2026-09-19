"""Model selections and the one-time handoff of legacy API configuration."""
from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
import threading

import yaml

from app.storage.atomic import atomic_write_text
from app.storage.paths import StoragePaths


OPENAI_SERVICE = "sakura.model.openai_compatible"
_HANDOFF_LOCK = threading.RLock()
EMPTY_REFERENCE = {"serviceKey": "", "profileId": "", "modelId": ""}


def model_reference(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) - set(EMPTY_REFERENCE):
        raise ValueError("MODEL_SLOT_SELECTION_INVALID")
    result = {key: value.get(key, "") for key in EMPTY_REFERENCE}
    if any(not isinstance(item, str) or "\x00" in item or len(item) > (256 if key == "modelId" else 200)
           for key, item in result.items()):
        raise ValueError("MODEL_SLOT_SELECTION_INVALID")
    result = {key: item.strip() for key, item in result.items()}
    if len({bool(item) for item in result.values()}) != 1:
        raise ValueError("MODEL_SLOT_SELECTION_INVALID")
    return result


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("CONFIG_DATA_INVALID")
    return value


def _write(path: Path, value: Mapping) -> None:
    atomic_write_text(path, json.dumps(dict(value), ensure_ascii=False, indent=2) + "\n")


def migrate_legacy_model_configuration(user_root: Path) -> None:
    """Finish the handoff before plugin startup; retain the original YAML untouched.

    Each destination is written atomically. Existing destination fields win when a
    process stopped between writes; the reference file is the final commit point.
    """
    paths = StoragePaths(user_root)
    target = paths.config_dir / "model_slots.json"
    with _HANDOFF_LOCK:
        if target.exists():
            # Earlier plugin builds placed the chat budget on model metadata.
            # Adopt it once; an explicit null in Assistant means the user cleared it.
            assistant_path = paths.plugin_data_for("sakura.assistant.default") / "config.json"
            assistant = _read(assistant_path)
            if "contextWindowTokens" not in assistant:
                reference = ModelReferenceRepository(user_root).load()["chat"]
                budget = None
                if reference.get("serviceKey") == OPENAI_SERVICE:
                    provider = _read(paths.plugin_data_for(OPENAI_SERVICE) / "config.json")
                    profiles = provider.get("profiles", [])
                    if not isinstance(profiles, list):
                        raise ValueError("CONFIG_DATA_INVALID")
                    for profile in profiles:
                        if not isinstance(profile, Mapping):
                            raise ValueError("CONFIG_DATA_INVALID")
                        if profile.get("profileId") == reference.get("profileId"):
                            models = profile.get("models", [])
                            if not isinstance(models, list) or any(not isinstance(model, Mapping) for model in models):
                                raise ValueError("CONFIG_DATA_INVALID")
                            budget = next((model.get("contextWindowTokens") for model in models
                                           if model.get("modelId") == reference.get("modelId")), None)
                _write(assistant_path, {**assistant, "contextWindowTokens": budget})
            return
        legacy_path = paths.api_config()
        try:
            raw = yaml.safe_load(legacy_path.read_text(encoding="utf-8")) if legacy_path.exists() else {}
        except yaml.YAMLError as error:
            raise ValueError("CONFIG_DATA_INVALID") from error
        if raw is None:
            raw = {}
        if not isinstance(raw, Mapping):
            raise ValueError("CONFIG_DATA_INVALID")
        legacy_profiles = raw.get("api_profiles", []) or []
        slots = raw.get("model_slots", {}) or {}
        llm = raw.get("llm", {}) or {}
        if not isinstance(legacy_profiles, list) or not isinstance(slots, Mapping) or not isinstance(llm, Mapping):
            raise ValueError("CONFIG_DATA_INVALID")
        profiles = []
        for old in legacy_profiles:
            if not isinstance(old, Mapping):
                raise ValueError("CONFIG_DATA_INVALID")
            if not isinstance(old.get("models", []), list):
                raise ValueError("CONFIG_DATA_INVALID")
            models = []
            for old_model in old.get("models", []):
                name = old_model.get("name", "") if isinstance(old_model, Mapping) else old_model
                if not isinstance(name, str) or not name:
                    raise ValueError("CONFIG_DATA_INVALID")
                model = {"modelId": name, "label": name}
                for slot in slots.values():
                    if not isinstance(slot, Mapping) or slot.get("profile_id") != old.get("id") or slot.get("model") != name:
                        continue
                    if slot.get("context_window_tokens") is not None:
                        model["contextWindowTokens"] = slot["context_window_tokens"]
                models.append(model)
            profiles.append({"profileId": old.get("id", ""), "label": old.get("alias", old.get("id", "")),
                             "base_url": old.get("base_url", ""), "api_key": old.get("api_key", ""),
                             "timeout_seconds": llm.get("timeout_seconds", 60), "models": models})
        if not profiles and llm.get("base_url") and llm.get("model"):
            profiles = [{"profileId": "legacy", "label": "模型服务", "base_url": llm["base_url"],
                         "api_key": llm.get("api_key", ""), "timeout_seconds": llm.get("timeout_seconds", 60),
                         "models": [{"modelId": llm["model"], "label": llm["model"]}]}]
            slots = {**slots, "chat": {"profile_id": "legacy", "model": llm["model"]}}
        provider_path = paths.plugin_data_for(OPENAI_SERVICE) / "config.json"
        provider = _read(provider_path)
        assistant_path = paths.plugin_data_for("sakura.assistant.default") / "config.json"
        assistant = _read(assistant_path)
        references = {}
        for name in ("chat", "vision_chat"):
            old = slots.get(name) or {}
            if not isinstance(old, Mapping):
                raise ValueError("CONFIG_DATA_INVALID")
            references[name] = model_reference({"serviceKey": OPENAI_SERVICE if old.get("profile_id") else "",
                                                "profileId": old.get("profile_id", ""), "modelId": old.get("model", "")})
        if "profiles" not in provider:
            _write(provider_path, {**provider, "profiles": profiles})
        original_assistant = dict(assistant)
        if "generation" not in assistant:
            generation = {key: llm[key] for key in ("temperature", "top_p", "max_tokens") if llm.get(key) is not None}
            assistant["generation"] = generation
        assistant.setdefault("contextWindowTokens", (slots.get("chat") or {}).get("context_window_tokens"))
        if assistant != original_assistant:
            _write(assistant_path, assistant)
        _write(target, {"schemaVersion": 1, "slots": references})


class ModelReferenceRepository:
    def __init__(self, user_root: Path) -> None:
        self.path = StoragePaths(user_root).config_dir / "model_slots.json"

    def load(self) -> dict[str, dict[str, str]]:
        raw = _read(self.path)
        if not raw:
            return {name: dict(EMPTY_REFERENCE) for name in ("chat", "vision_chat")}
        if raw.get("schemaVersion") != 1 or not isinstance(raw.get("slots"), Mapping):
            raise ValueError("CONFIG_DATA_INVALID")
        return {name: model_reference(raw["slots"].get(name, {})) for name in ("chat", "vision_chat")}

    def save(self, selections: Mapping, *, replace_invalid: bool = False) -> None:
        if set(selections) != {"chat", "vision_chat"}:
            raise ValueError("MODEL_SLOTS_INVALID")
        try:
            self.load()
            raw = _read(self.path)
        except ValueError:
            if not replace_invalid:
                raise
            raw = {}
        _write(self.path, {**raw, "schemaVersion": 1, "slots": {name: model_reference(value) for name, value in selections.items()}})

    def active(self) -> dict[str, dict[str, str] | None]:
        selections = self.load()
        chat = selections["chat"] if selections["chat"]["serviceKey"] else None
        vision = selections["vision_chat"] if selections["vision_chat"]["serviceKey"] else chat
        return {"chat": chat, "vision_chat": vision}
