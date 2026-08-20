"""Copy legacy Runtime v2 TTS state into Plugin Kernel v3-owned stores.

The legacy files remain untouched as rollback material.  Every destination is
independently merge-only and atomically written so an interrupted run can be
retried without a global migration marker.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from app.config.yaml_config import load_yaml_mapping
from app.storage.atomic import atomic_write_text
from app.storage.paths import StoragePaths


GPT_PROVIDER_ID = "sakura.tts.gpt-sovits"
GENIE_PROVIDER_ID = "sakura.tts.genie"
HUB_EXTENSION_ID = "sakura.tts"


@dataclass(frozen=True)
class TTSPluginCutoverReport:
    changed_files: int = 0
    skipped_files: int = 0
    failed_files: int = 0


def migrate_legacy_tts_to_plugins(app_root: Path) -> TTSPluginCutoverReport:
    """Best-effort, copy-only projection performed before a Core generation loads plugins."""

    root = Path(app_root).resolve()
    api_path = StoragePaths(root).api_config()
    try:
        api = load_yaml_mapping(api_path)
    except (OSError, ValueError):
        return TTSPluginCutoverReport(skipped_files=1)
    legacy = api.get("tts")
    if not isinstance(legacy, Mapping):
        return TTSPluginCutoverReport()

    selected_provider, enabled = _legacy_selection(legacy)
    changed = skipped = failed = 0

    provider_patches = {
        GPT_PROVIDER_ID: _gpt_config_patch(root, legacy),
        GENIE_PROVIDER_ID: _genie_config_patch(root, legacy),
    }
    for plugin_id, patch in provider_patches.items():
        if not patch:
            continue
        target = StoragePaths(root).plugin_data_for(plugin_id) / "config.json"
        result = _merge_json_file(target, patch)
        changed += result == "changed"
        skipped += result == "skipped"
        failed += result == "failed"

    characters_dir = root / "characters"
    for manifest_path in sorted(characters_dir.glob("*/character.json")):
        result = _migrate_character(
            manifest_path,
            selected_provider=selected_provider,
            enabled=enabled,
            genie_mode=str(provider_patches[GENIE_PROVIDER_ID].get("endpointMode", "custom")),
        )
        changed += result == "changed"
        skipped += result == "skipped"
        failed += result == "failed"

    return TTSPluginCutoverReport(changed, skipped, failed)


def _legacy_selection(legacy: Mapping[str, Any]) -> tuple[str | None, bool | None]:
    raw_provider = str(legacy.get("provider") or "").strip().lower().replace("_", "-")
    enabled = legacy.get("enabled") if isinstance(legacy.get("enabled"), bool) else None
    if raw_provider in {
        "gpt-sovits",
        "gptsovits",
        "custom-gpt-sovits",
        "external-gpt-sovits",
        "custom-sovits",
        "external-sovits",
    }:
        return GPT_PROVIDER_ID, enabled
    if raw_provider in {"genie", "genie-tts", "genietts"}:
        return GENIE_PROVIDER_ID, enabled
    if raw_provider in {"none", "off", "disabled", "不使用"}:
        return GPT_PROVIDER_ID, False
    return None, enabled


def _gpt_config_patch(root: Path, legacy: Mapping[str, Any]) -> dict[str, Any]:
    section = legacy.get("gpt_sovits")
    if not isinstance(section, Mapping):
        section = {}
    runtime = section.get("managed_runtime")
    if not isinstance(runtime, Mapping):
        runtime = {}
    raw_provider = str(legacy.get("provider") or "").strip().lower().replace("_", "-")
    custom = raw_provider in {
        "custom-gpt-sovits",
        "external-gpt-sovits",
        "custom-sovits",
        "external-sovits",
    }
    custom_base = _text(section.get("custom_base_url"))
    tts_path = _text(section.get("tts_path")) or "/tts"
    if custom and custom_base is None:
        custom_base, tts_path = _split_endpoint(_text(section.get("api_url")), tts_path)

    patch: dict[str, Any] = {
        "customBaseUrl": custom_base or "",
        "ttsPath": tts_path if tts_path.startswith("/") else f"/{tts_path}",
    }
    _copy_text(section, "remote_reference_root", patch, "remoteReferenceRoot")
    _copy_timeout(section, patch)
    if not custom:
        work_dir = _absolute_path(root, runtime.get("work_dir", section.get("work_dir")))
        if work_dir is None:
            work_dir = _installed_bundle(root, "gpt-sovits")
        if work_dir is not None:
            patch["workDir"] = str(work_dir)
        for old_key, new_key in (
            ("python_path", "pythonPath"),
            ("tts_config_path", "ttsConfigPath"),
        ):
            value = _absolute_path(root, runtime.get(old_key, section.get(old_key)))
            if value is not None:
                patch[new_key] = str(value)
    return patch


def _genie_config_patch(root: Path, legacy: Mapping[str, Any]) -> dict[str, Any]:
    section = legacy.get("genie_tts")
    if not isinstance(section, Mapping):
        section = {}
    work_dir = _absolute_path(root, section.get("work_dir"))
    if work_dir is None:
        work_dir = _installed_bundle(root, "genie-tts")
    mode = "managed" if work_dir is not None else "custom"
    patch: dict[str, Any] = {
        "endpointMode": mode,
        "apiUrl": _text(section.get("api_url")) or "http://127.0.0.1:9881/",
    }
    _copy_timeout(section, patch)
    if work_dir is not None:
        patch["workDir"] = str(work_dir)
    return patch


def _installed_bundle(root: Path, provider: str) -> Path | None:
    try:
        from app.voice.tts_bundle import TTS_BUNDLES, default_bundle_work_dir

        for entry in TTS_BUNDLES:
            if (entry.provider or "gpt-sovits") != provider:
                continue
            candidate = default_bundle_work_dir(entry, root)
            if candidate.is_dir():
                return candidate.resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    return None


def _migrate_character(
    manifest_path: Path,
    *,
    selected_provider: str | None,
    enabled: bool | None,
    genie_mode: str,
) -> str:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "skipped"
    if not isinstance(manifest, dict) or not isinstance(manifest.get("voice"), Mapping):
        return "skipped"
    extensions = manifest.get("extensions", {})
    if not isinstance(extensions, Mapping):
        return "skipped"
    extensions = _json_clone(extensions)
    hub = extensions.get(HUB_EXTENSION_ID, {})
    if not isinstance(hub, Mapping):
        return "skipped"
    hub = dict(hub)
    provider_id = hub.get("provider")
    if not isinstance(provider_id, str) or not provider_id:
        provider_id = selected_provider
    voice = manifest["voice"]
    provider_resources_valid = not (
        (
            provider_id == GPT_PROVIDER_ID
            and not _voice_resources_valid(manifest_path.parent, voice)
        )
        or (
            provider_id == GENIE_PROVIDER_ID
            and genie_mode != "custom"
            and not _voice_resources_valid(
                manifest_path.parent,
                voice,
                require_models=True,
            )
        )
    )
    if "provider" not in hub and provider_id is not None:
        hub["provider"] = provider_id
    if "enabled" not in hub and enabled is not None:
        hub["enabled"] = enabled
    if not hub:
        return "skipped"
    extensions[HUB_EXTENSION_ID] = hub

    if provider_id == GPT_PROVIDER_ID and provider_resources_valid:
        patch = _voice_patch(voice, include_text_lang=True)
        _merge_extension(extensions, GPT_PROVIDER_ID, patch)
    elif provider_id == GENIE_PROVIDER_ID:
        if genie_mode == "custom":
            display_name = manifest.get("display_name")
            patch = {"remoteCharacterName": display_name.strip()} if isinstance(display_name, str) and display_name.strip() else {}
        elif provider_resources_valid:
            patch = _voice_patch(voice, include_text_lang=False)
        else:
            patch = {}
        if patch:
            _merge_extension(extensions, GENIE_PROVIDER_ID, patch)

    updated = dict(manifest)
    updated["extensions"] = extensions
    if updated == manifest:
        return "skipped"
    try:
        atomic_write_text(
            manifest_path,
            json.dumps(updated, ensure_ascii=False, indent=2, allow_nan=False),
            backup=True,
        )
    except (OSError, TypeError, ValueError):
        return "failed"
    return "changed"


def _voice_patch(voice: Mapping[str, Any], *, include_text_lang: bool) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    for old_key, new_key in (
        ("tone_refs", "toneRefs"),
        ("gpt_model", "gptModel"),
        ("sovits_model", "sovitsModel"),
        ("ref_lang", "refLang"),
    ):
        _copy_text(voice, old_key, patch, new_key)
    if include_text_lang:
        _copy_text(voice, "text_lang", patch, "textLang")
    return patch


def _voice_resources_valid(
    package_root: Path,
    voice: Mapping[str, Any],
    *,
    require_models: bool = False,
) -> bool:
    tone_path = _safe_character_resource(package_root, voice.get("tone_refs"), require_file=True)
    if tone_path is None:
        return False
    for key in ("gpt_model", "sovits_model"):
        raw = _text(voice.get(key))
        if (require_models or raw is not None) and _safe_character_resource(
            package_root,
            raw,
            require_file=True,
        ) is None:
            return False
    try:
        lines = tone_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    references = 0
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 4 or not all(parts):
            return False
        if _safe_character_resource(package_root, parts[0], require_file=True) is None:
            return False
        references += 1
    return references > 0


def _safe_character_resource(
    package_root: Path,
    value: object,
    *,
    require_file: bool,
) -> Path | None:
    text = _text(value)
    if text is None:
        return None
    lexical = Path(text)
    if lexical.is_absolute() or lexical.drive or ".." in lexical.parts or text.startswith(("\\", "//")):
        return None
    try:
        root = package_root.resolve(strict=True)
        target = (package_root / lexical).resolve(strict=True)
        target.relative_to(root)
        cursor = package_root
        for part in lexical.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                return None
    except (OSError, ValueError):
        return None
    if require_file and not target.is_file():
        return None
    return target


def _merge_extension(extensions: dict[str, Any], plugin_id: str, patch: Mapping[str, Any]) -> None:
    current = extensions.get(plugin_id, {})
    if not isinstance(current, Mapping):
        return
    merged = dict(current)
    for key, value in patch.items():
        merged.setdefault(key, value)
    if merged:
        extensions[plugin_id] = merged


def _merge_json_file(path: Path, patch: Mapping[str, Any]) -> str:
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return "skipped"
        if not isinstance(current, dict):
            return "skipped"
    else:
        current = {}
    updated = dict(current)
    for key, value in patch.items():
        updated.setdefault(key, value)
    if updated == current:
        return "skipped"
    try:
        atomic_write_text(
            path,
            json.dumps(updated, ensure_ascii=False, indent=2, allow_nan=False),
            backup=True,
        )
    except (OSError, TypeError, ValueError):
        return "failed"
    return "changed"


def _copy_text(source: Mapping[str, Any], old_key: str, target: dict[str, Any], new_key: str) -> None:
    value = _text(source.get(old_key))
    if value is not None:
        target[new_key] = value


def _copy_timeout(source: Mapping[str, Any], target: dict[str, Any]) -> None:
    value = source.get("timeout_seconds")
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 300:
        target["timeoutSeconds"] = value


def _absolute_path(root: Path, value: object) -> Path | None:
    text = _text(value)
    if text is None:
        return None
    path = Path(text)
    return (path if path.is_absolute() else root / path).resolve()


def _split_endpoint(raw_url: str | None, fallback_path: str) -> tuple[str | None, str]:
    if raw_url is None:
        return None, fallback_path
    try:
        parsed = urlsplit(raw_url)
    except ValueError:
        return None, fallback_path
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None, fallback_path
    path = parsed.path or fallback_path
    base = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    return base.rstrip("/"), path if path.startswith("/") else f"/{path}"


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _json_clone(value: Mapping[str, Any]) -> dict[str, Any]:
    cloned = json.loads(json.dumps(dict(value), ensure_ascii=False, allow_nan=False))
    return cloned if isinstance(cloned, dict) else {}


__all__ = ["TTSPluginCutoverReport", "migrate_legacy_tts_to_plugins"]
