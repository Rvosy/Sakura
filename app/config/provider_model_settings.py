"""Qt-free Runtime v2 Provider/model settings domain.

This module deliberately does not use ``AppSettingsService.load_api_profiles``:
that legacy API may migrate while reading. Runtime v2 reads are side-effect free
and the only write is one whole-domain atomic replacement.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import yaml

from app.storage.atomic import atomic_write_text


SUPPORTED_CONFIG_VERSION = 4
MAX_PROVIDERS = 32
MAX_MODELS_PER_PROVIDER = 512
_PROFILE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_MODEL_NAME = re.compile(r"^[^\x00-\x1f\x7f]{1,256}$")
_HOST_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
CredentialAction = Literal["keep", "replace", "clear"]


class ProviderModelSettingsError(ValueError):
    def __init__(self, code: str, message: str, *, feature: str = "providers.manage", field: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.feature = feature
        self.field = field

    def public_error(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": self.message,
            "feature": self.feature,
            "field": self.field,
        }


@dataclass(frozen=True)
class ProviderCredential:
    action: CredentialAction
    value: str = ""


@dataclass(frozen=True)
class ProviderDraft:
    id: str
    alias: str
    base_url: str
    models: tuple[str, ...]
    credential: ProviderCredential


@dataclass(frozen=True)
class ModelSlotDraft:
    profile_id: str = ""
    model: str = ""

    @property
    def empty(self) -> bool:
        return not self.profile_id and not self.model


@dataclass(frozen=True)
class ProviderModelDraft:
    providers: tuple[ProviderDraft, ...]
    chat: ModelSlotDraft
    vision_chat: ModelSlotDraft
    timeout_seconds: int
    temperature: float | None
    top_p: float | None
    max_tokens: int | None


def _read_yaml(path: Path, *, missing_ok: bool) -> dict[str, Any]:
    if not path.exists():
        if missing_ok:
            return {}
        raise ProviderModelSettingsError("CONFIG_DATA_INVALID", "配置数据不可用。")
    try:
        text = path.read_text(encoding="utf-8")
        value = yaml.safe_load(text)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ProviderModelSettingsError("CONFIG_DATA_INVALID", "配置数据不可用。") from exc
    if value is None and missing_ok:
        return {}
    if not isinstance(value, Mapping):
        raise ProviderModelSettingsError("CONFIG_DATA_INVALID", "配置数据不可用。")
    return dict(value)


class ProviderModelSettingsRepository:
    def __init__(self, app_root: Path) -> None:
        self._config_dir = Path(app_root) / "data" / "config"
        self._system_path = self._config_dir / "system_config.yaml"
        self._api_path = self._config_dir / "api.yaml"

    def _assert_current_schema(self) -> None:
        system = _read_yaml(self._system_path, missing_ok=False)
        version = system.get("config_version")
        if isinstance(version, bool) or not isinstance(version, int):
            raise ProviderModelSettingsError("CONFIG_VERSION_UNSUPPORTED", "配置版本不受支持。")
        if version != SUPPORTED_CONFIG_VERSION:
            raise ProviderModelSettingsError("CONFIG_VERSION_UNSUPPORTED", "配置版本不受支持。")

    def snapshot(self) -> dict[str, Any]:
        self._assert_current_schema()
        data = _read_yaml(self._api_path, missing_ok=True)
        providers, slots, llm = self._validate_current_document(data)
        configured = {
            item["id"]
            for item in providers
            if item["configured"] and item["base_url"] and item["models"]
        }
        chat = slots["chat"]
        setup_complete = bool(chat["profile_id"] in configured and chat["model"])
        return {
            "schema_version": 1,
            "providers": providers,
            "model_slots": slots,
            "settings": {
                "timeout_seconds": _bounded_int(llm.get("timeout_seconds"), 60, 1, 300),
                "temperature": _optional_number(llm.get("temperature"), 0.0, 2.0),
                "top_p": _optional_number(llm.get("top_p"), 0.0, 1.0),
                "max_tokens": _optional_int(llm.get("max_tokens"), 1, 1_000_000),
            },
            "setup_complete": setup_complete,
            "change_plans": ["applied"],
        }

    def save(self, raw: object) -> dict[str, Any]:
        self._assert_current_schema()
        old = _read_yaml(self._api_path, missing_ok=True)
        self._validate_current_document(old)
        draft = parse_draft(raw)
        old_by_id = {
            str(item.get("id", "")): item
            for item in old.get("api_profiles", [])
            if isinstance(item, Mapping)
        }
        resolved_secrets: dict[str, str] = {}
        for provider in draft.providers:
            old_secret = ""
            old_provider = old_by_id.get(provider.id)
            if isinstance(old_provider, Mapping) and isinstance(old_provider.get("api_key"), str):
                old_secret = old_provider["api_key"]
            if provider.credential.action == "keep":
                resolved_secrets[provider.id] = old_secret
            elif provider.credential.action == "replace":
                resolved_secrets[provider.id] = provider.credential.value
            else:
                resolved_secrets[provider.id] = ""

        new_data = dict(old)
        new_data["api_profiles"] = [
            _merge_provider(old_by_id.get(provider.id), provider, resolved_secrets[provider.id])
            for provider in draft.providers
        ]
        old_slots = old.get("model_slots")
        slots: dict[str, Any] = dict(old_slots) if isinstance(old_slots, Mapping) else {}
        for name, selection in (("chat", draft.chat), ("vision_chat", draft.vision_chat)):
            existing = slots.get(name)
            merged_slot = dict(existing) if isinstance(existing, Mapping) else {}
            merged_slot.pop("profile_id", None)
            merged_slot.pop("model", None)
            if not selection.empty:
                merged_slot.update(_slot_mapping(selection))
            if merged_slot:
                slots[name] = merged_slot
            else:
                slots.pop(name, None)
        new_data["model_slots"] = slots

        llm = dict(old.get("llm")) if isinstance(old.get("llm"), Mapping) else {}
        selected_provider = next(
            (item for item in draft.providers if item.id == draft.chat.profile_id),
            None,
        )
        llm["base_url"] = selected_provider.base_url if selected_provider else ""
        llm["api_key"] = resolved_secrets.get(selected_provider.id, "") if selected_provider else ""
        llm["model"] = draft.chat.model if selected_provider else ""
        llm["timeout_seconds"] = draft.timeout_seconds
        _set_optional(llm, "temperature", draft.temperature)
        _set_optional(llm, "top_p", draft.top_p)
        _set_optional(llm, "max_tokens", draft.max_tokens)
        new_data["llm"] = llm

        serialized = yaml.safe_dump(new_data, allow_unicode=True, sort_keys=False)
        try:
            atomic_write_text(self._api_path, serialized, backup=False)
        except OSError as exc:
            raise ProviderModelSettingsError(
                "CONFIG_SAVE_FAILED",
                "配置保存失败，原文件保持不变。",
            ) from exc
        return {
            "saved": True,
            "change_plan": "applied",
            "setup_complete": self._setup_complete(draft, resolved_secrets),
        }

    def resolve_probe(self, raw: object, *, require_model: bool) -> tuple[str, str, str, int]:
        self._assert_current_schema()
        if not isinstance(raw, Mapping):
            raise ProviderModelSettingsError("INVALID_REQUEST", "请求格式无效。", feature="providers.test_connection")
        profile_id = _required_text(raw.get("profile_id"), "profile_id", 64)
        base_url = _validate_url(raw.get("base_url"), field="base_url")
        model = _text(raw.get("model"), "model", 256)
        if require_model and not model:
            raise ProviderModelSettingsError("FIELD_REQUIRED", "请选择模型。", feature="providers.test_connection", field="model")
        timeout = _bounded_int(raw.get("timeout_seconds"), 15, 1, 60, strict=True)
        credential = _parse_credential(raw.get("credential"), feature="providers.credentials")
        saved = _read_yaml(self._api_path, missing_ok=True)
        self._validate_current_document(saved)
        old_secret = ""
        for item in saved.get("api_profiles", []):
            if isinstance(item, Mapping) and item.get("id") == profile_id and isinstance(item.get("api_key"), str):
                old_secret = item["api_key"]
                break
        if credential.action == "keep":
            secret = old_secret
        elif credential.action == "replace":
            secret = credential.value
        else:
            secret = ""
        if not secret:
            raise ProviderModelSettingsError("CREDENTIAL_REQUIRED", "该供应商尚未配置凭据。", feature="providers.credentials", field="credential")
        return base_url, secret, model, timeout

    @classmethod
    def _validate_current_document(
        cls,
        data: Mapping[str, Any],
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]], Mapping[str, Any]]:
        providers = cls._public_providers(data)
        slots = cls._public_slots(data, providers)
        raw_llm = data.get("llm", {})
        if raw_llm is None:
            raw_llm = {}
        if not isinstance(raw_llm, Mapping):
            raise ProviderModelSettingsError("CONFIG_DATA_INVALID", "LLM 配置格式无效。")
        return providers, slots, raw_llm

    @staticmethod
    def _public_providers(data: Mapping[str, Any]) -> list[dict[str, Any]]:
        raw = data.get("api_profiles", [])
        if raw is None:
            raw = []
        if not isinstance(raw, list):
            raise ProviderModelSettingsError("CONFIG_DATA_INVALID", "Provider 配置格式无效。")
        providers: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, Mapping):
                raise ProviderModelSettingsError("CONFIG_DATA_INVALID", "Provider 配置格式无效。")
            profile_id = _required_text(item.get("id"), "id", 64)
            if not _PROFILE_ID.fullmatch(profile_id):
                raise ProviderModelSettingsError("CONFIG_DATA_INVALID", "Provider ID 格式无效。")
            if profile_id in seen:
                raise ProviderModelSettingsError("CONFIG_DATA_INVALID", "Provider ID 不能重复。")
            seen.add(profile_id)
            alias = _text(item.get("alias"), "alias", 120) or profile_id
            base_url = _text(item.get("base_url"), "base_url", 2048)
            if base_url:
                base_url = _validate_url(base_url, field="base_url")
            secret = item.get("api_key", "")
            if not isinstance(secret, str):
                raise ProviderModelSettingsError("CONFIG_DATA_INVALID", "Provider 凭据格式无效。")
            providers.append({
                "id": profile_id,
                "alias": alias,
                "base_url": base_url,
                "configured": bool(secret),
                "models": list(_parse_models(item.get("models", []), strict=False)),
            })
        return providers

    @staticmethod
    def _public_slots(data: Mapping[str, Any], providers: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
        raw = data.get("model_slots", {})
        if raw is None:
            raw = {}
        if not isinstance(raw, Mapping):
            raise ProviderModelSettingsError("CONFIG_DATA_INVALID", "模型槽配置格式无效。", feature="model.chat_slot")
        result: dict[str, dict[str, str]] = {}
        for slot in ("chat", "vision_chat"):
            value = raw.get(slot, {})
            if value is None:
                value = {}
            if not isinstance(value, Mapping):
                raise ProviderModelSettingsError("CONFIG_DATA_INVALID", "模型槽配置格式无效。", feature=f"model.{slot}_slot")
            result[slot] = {
                "profile_id": _text(value.get("profile_id"), "profile_id", 64),
                "model": _text(value.get("model"), "model", 256),
            }
        return result

    @staticmethod
    def _setup_complete(draft: ProviderModelDraft, secrets: Mapping[str, str]) -> bool:
        if draft.chat.empty:
            return False
        provider = next((item for item in draft.providers if item.id == draft.chat.profile_id), None)
        return bool(provider and secrets.get(provider.id) and draft.chat.model in provider.models)


def parse_draft(raw: object) -> ProviderModelDraft:
    if not isinstance(raw, Mapping) or set(raw) - {"providers", "model_slots", "settings"}:
        raise ProviderModelSettingsError("INVALID_REQUEST", "设置请求格式无效。")
    raw_providers = raw.get("providers")
    if not isinstance(raw_providers, list) or len(raw_providers) > MAX_PROVIDERS:
        raise ProviderModelSettingsError("PROVIDERS_INVALID", "Provider 列表无效。")
    providers: list[ProviderDraft] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_providers):
        if not isinstance(item, Mapping):
            raise ProviderModelSettingsError("PROVIDER_INVALID", "Provider 配置无效。", field=str(index))
        profile_id = _required_text(item.get("id"), "id", 64)
        if not _PROFILE_ID.fullmatch(profile_id):
            raise ProviderModelSettingsError("FIELD_INVALID", "Provider ID 格式无效。", field="id")
        if profile_id in seen:
            raise ProviderModelSettingsError("PROVIDER_ID_DUPLICATE", "Provider ID 不能重复。", field="id")
        seen.add(profile_id)
        providers.append(ProviderDraft(
            id=profile_id,
            alias=_required_text(item.get("alias"), "alias", 120),
            base_url=_validate_url(item.get("base_url"), field="base_url"),
            models=_parse_models(item.get("models"), strict=True),
            credential=_parse_credential(item.get("credential")),
        ))

    raw_slots = raw.get("model_slots", {})
    if not isinstance(raw_slots, Mapping) or set(raw_slots) - {"chat", "vision_chat"}:
        raise ProviderModelSettingsError("MODEL_SLOTS_INVALID", "模型槽配置无效。", feature="model.chat_slot")
    chat = _parse_slot(raw_slots.get("chat"), "chat")
    vision = _parse_slot(raw_slots.get("vision_chat"), "vision_chat")
    by_id = {item.id: item for item in providers}
    for slot_name, slot in (("chat", chat), ("vision_chat", vision)):
        if slot.empty:
            continue
        provider = by_id.get(slot.profile_id)
        if provider is None or slot.model not in provider.models:
            raise ProviderModelSettingsError("MODEL_REFERENCE_INVALID", "模型槽引用不存在的 Provider 或模型。", feature=f"model.{slot_name}_slot", field=slot_name)

    settings = raw.get("settings", {})
    if not isinstance(settings, Mapping) or set(settings) - {"timeout_seconds", "temperature", "top_p", "max_tokens"}:
        raise ProviderModelSettingsError("MODEL_SETTINGS_INVALID", "模型高级参数无效。", feature="model.chat_slot")
    return ProviderModelDraft(
        providers=tuple(providers),
        chat=chat,
        vision_chat=vision,
        timeout_seconds=_bounded_int(settings.get("timeout_seconds"), 60, 1, 300, strict=True),
        temperature=_optional_number(settings.get("temperature"), 0.0, 2.0, strict=True),
        top_p=_optional_number(settings.get("top_p"), 0.0, 1.0, strict=True),
        max_tokens=_optional_int(settings.get("max_tokens"), 1, 1_000_000, strict=True),
    )


def _merge_provider(old: object, draft: ProviderDraft, secret: str) -> dict[str, Any]:
    merged = dict(old) if isinstance(old, Mapping) else {}
    old_models = {
        str(item.get("name")): item
        for item in merged.get("models", [])
        if isinstance(item, Mapping) and isinstance(item.get("name"), str)
    }
    merged.update({
        "id": draft.id,
        "alias": draft.alias,
        "base_url": draft.base_url,
        "api_key": secret,
        "models": [dict(old_models.get(name, {}), name=name) for name in draft.models],
    })
    return merged


def _parse_credential(value: object, *, feature: str = "providers.credentials") -> ProviderCredential:
    if not isinstance(value, Mapping) or set(value) - {"action", "value"}:
        raise ProviderModelSettingsError("CREDENTIAL_ACTION_INVALID", "凭据动作无效。", feature=feature, field="credential")
    action = value.get("action")
    if action not in {"keep", "replace", "clear"}:
        raise ProviderModelSettingsError("CREDENTIAL_ACTION_INVALID", "凭据动作无效。", feature=feature, field="credential")
    raw_secret = value.get("value", "")
    if not isinstance(raw_secret, str) or len(raw_secret) > 16_384 or "\x00" in raw_secret:
        raise ProviderModelSettingsError("CREDENTIAL_INVALID", "凭据格式无效。", feature=feature, field="credential")
    secret = raw_secret.strip()
    if action == "replace" and not secret:
        raise ProviderModelSettingsError("CREDENTIAL_REQUIRED", "请输入新凭据。", feature=feature, field="credential")
    if action != "replace" and secret:
        raise ProviderModelSettingsError("CREDENTIAL_ACTION_INVALID", "凭据动作与内容不一致。", feature=feature, field="credential")
    return ProviderCredential(action=action, value=secret)


def _parse_models(value: object, *, strict: bool) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > MAX_MODELS_PER_PROVIDER:
        raise ProviderModelSettingsError("MODELS_INVALID", "模型列表无效。", field="models")
    result: list[str] = []
    for item in value:
        name = item.get("name") if isinstance(item, Mapping) else item
        if not isinstance(name, str):
            raise ProviderModelSettingsError("MODEL_INVALID", "模型 ID 无效。", field="models")
        name = name.strip()
        if not name or not _MODEL_NAME.fullmatch(name):
            raise ProviderModelSettingsError("MODEL_INVALID", "模型 ID 无效。", field="models")
        if name in result:
            if strict:
                raise ProviderModelSettingsError("MODEL_DUPLICATE", "模型 ID 不能重复。", field="models")
            continue
        result.append(name)
    return tuple(result)


def _parse_slot(value: object, name: str) -> ModelSlotDraft:
    if value is None:
        return ModelSlotDraft()
    if not isinstance(value, Mapping) or set(value) - {"profile_id", "model"}:
        raise ProviderModelSettingsError("MODEL_SLOT_INVALID", "模型槽配置无效。", feature=f"model.{name}_slot", field=name)
    slot = ModelSlotDraft(
        profile_id=_text(value.get("profile_id"), "profile_id", 64),
        model=_text(value.get("model"), "model", 256),
    )
    if bool(slot.profile_id) != bool(slot.model):
        raise ProviderModelSettingsError("MODEL_SLOT_INCOMPLETE", "模型槽必须同时选择 Provider 和模型。", feature=f"model.{name}_slot", field=name)
    return slot


def _validate_url(value: object, *, field: str) -> str:
    text = _required_text(value, field, 2048).rstrip("/")
    try:
        parsed = urlparse(text)
        parsed.port
    except ValueError as exc:
        raise ProviderModelSettingsError("BASE_URL_INVALID", "Base URL 格式无效。", field=field) from exc
    hostname = parsed.hostname
    if parsed.scheme.lower() not in {"http", "https"} or not hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ProviderModelSettingsError("BASE_URL_INVALID", "Base URL 格式无效。", field=field)
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii").rstrip(".")
    except UnicodeError as exc:
        raise ProviderModelSettingsError("BASE_URL_INVALID", "Base URL 格式无效。", field=field) from exc
    if (
        not ascii_hostname
        or len(ascii_hostname) > 253
        or (
            ":" not in ascii_hostname
            and any(_HOST_LABEL.fullmatch(label) is None for label in ascii_hostname.split("."))
        )
    ):
        raise ProviderModelSettingsError("BASE_URL_INVALID", "Base URL 格式无效。", field=field)
    return text


def _required_text(value: object, field: str, maximum: int) -> str:
    text = _text(value, field, maximum)
    if not text:
        raise ProviderModelSettingsError("FIELD_REQUIRED", f"{field} 不能为空。", field=field)
    return text


def _text(value: object, field: str, maximum: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        raise ProviderModelSettingsError("FIELD_INVALID", f"{field} 格式无效。", field=field)
    return value.strip()


def _bounded_int(value: object, default: int, minimum: int, maximum: int, *, strict: bool = False) -> int:
    if value is None and not strict:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        if strict:
            raise ProviderModelSettingsError("FIELD_INVALID", "数值字段超出允许范围。", feature="model.chat_slot")
        return default
    return value


def _optional_number(value: object, minimum: float, maximum: float, *, strict: bool = False) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or not minimum <= float(value) <= maximum:
        if strict:
            raise ProviderModelSettingsError("FIELD_INVALID", "数值字段超出允许范围。", feature="model.chat_slot")
        return None
    return float(value)


def _optional_int(value: object, minimum: int, maximum: int, *, strict: bool = False) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        if strict:
            raise ProviderModelSettingsError("FIELD_INVALID", "数值字段超出允许范围。", feature="model.chat_slot")
        return None
    return value


def _slot_mapping(slot: ModelSlotDraft) -> dict[str, str]:
    return {"profile_id": slot.profile_id, "model": slot.model}


def _set_optional(target: dict[str, Any], key: str, value: object | None) -> None:
    if value is None:
        target.pop(key, None)
    else:
        target[key] = value
