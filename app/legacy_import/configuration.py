from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlparse, urlsplit

import yaml

from app.storage.paths import user_facing_path

from .errors import LegacyImportError


_UI_KEYS = {
    "subtitle_language",
    "portrait_scale_percent",
    "subtitle_typing_interval_ms",
    "reply_segment_pause_ms",
    "control_panel_width",
    "bubble_height",
    "control_panel_vertical_offset",
    "input_bar_offset",
    "speech_font_size",
    "name_font_size",
    "input_font_size",
    "visual_effect_mode",
}
_BUILTIN_PLUGIN_ALIASES = {
    "sakura_mobile": "sakura_mobile",
    "sakura.tts": "sakura.tts",
    "sakura.tts.gpt-sovits": "sakura.tts.gpt-sovits",
    "sakura_gpt_sovits": "sakura.tts.gpt-sovits",
    "gpt_sovits": "sakura.tts.gpt-sovits",
    "sakura.tts.genie": "sakura.tts.genie",
    "sakura_genie": "sakura.tts.genie",
    "sakura.memory.mem0": "sakura.memory.mem0",
    "sakura_mem0": "sakura.memory.mem0",
}
_PR110_DEFAULT_TEXT_MODEL = "gpt-4.1-mini"
_PR110_DEFAULT_VISION_MODEL = "gpt-4o"
_PR110_SELECTION_FIELDS = (
    "text_enabled",
    "text_profile_id",
    "text_model",
    "vision_profile_id",
    "vision_model",
)
_RETIRED_API_FIELDS = ("model_names", *_PR110_SELECTION_FIELDS)
_LEGACY_ENV_TO_LLM_FIELD = {
    "BASE_URL": "base_url",
    "API_KEY": "api_key",
    "MODEL": "model",
}


def migrate_configuration(source: Path, staged: Path, *, new_tts_root: Path) -> dict[str, int]:
    legacy_config = source / "data" / "config"
    target_config = staged / "config"
    target_config.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}

    api = _load_yaml(legacy_config / "api.yaml", required=True)
    tts = api.pop("tts", None)
    _merge_allowed_env(source, api)
    _normalize_api(api)
    _write_yaml(target_config / "api.yaml", api)
    counts["config"] = counts.get("config", 0) + 1

    system = _load_yaml(legacy_config / "system_config.yaml", required=False)
    current_system: dict[str, object] = {"config_version": 1}
    for key in ("tool_loop", "screen_awareness", "memory_curation"):
        if isinstance(system.get(key), Mapping):
            current_system[key] = dict(system[key])
    screen_awareness = current_system.get("screen_awareness")
    if isinstance(screen_awareness, dict):
        legacy_context_enabled = screen_awareness.pop("screen_context_enabled", None)
        if isinstance(legacy_context_enabled, bool):
            current_enabled = screen_awareness.get("enabled", True)
            screen_awareness["enabled"] = bool(current_enabled) and legacy_context_enabled
    _write_yaml(target_config / "system_config.yaml", current_system)

    characters = _load_yaml(legacy_config / "characters.yaml", required=False)
    current_character = characters.get("current_character_id", "")
    if not isinstance(current_character, str):
        raise LegacyImportError("LEGACY_CHARACTER_SELECTION_INVALID", "staging", "data/config/characters.yaml")
    _write_yaml(
        target_config / "characters.yaml",
        {"current_character_id": current_character.strip()},
    )

    mcp, dropped_servers = _migrate_mcp(source, legacy_config / "mcp.yaml")
    _write_yaml(target_config / "mcp.yaml", mcp)
    counts["mcpServersQuarantined"] = dropped_servers

    tts_provider = _legacy_tts_provider(tts)
    plugins = _migrate_plugins(legacy_config / "plugins.yaml", tts_provider=tts_provider)
    _write_yaml(target_config / "plugins.yaml", plugins)

    ui = _migrate_ui(system)
    (target_config / "ui.json").write_text(
        json.dumps(ui, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    counts["config"] += 5

    if isinstance(tts, Mapping):
        _write_tts_plugin_config(staged, tts, new_tts_root, tts_provider=tts_provider)
        counts["ttsConfig"] = 1
    return counts


def add_character_extensions(
    staged: Path,
    *,
    legacy_onnx_root: Path | None = None,
) -> tuple[str, ...]:
    ids: list[str] = []
    root = staged / "characters"
    if not root.is_dir():
        return ()
    for manifest in sorted(root.glob("*/character.json")):
        relative = manifest.relative_to(staged).as_posix()
        try:
            value = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise LegacyImportError("LEGACY_CHARACTER_MANIFEST_INVALID", "staging", relative) from exc
        if not isinstance(value, dict) or not isinstance(value.get("id"), str):
            raise LegacyImportError("LEGACY_CHARACTER_MANIFEST_INVALID", "staging", relative)
        character_id = value["id"].strip()
        if not character_id:
            raise LegacyImportError("LEGACY_CHARACTER_MANIFEST_INVALID", "staging", relative)
        ids.append(character_id)
        theme = value.get("theme")
        if isinstance(theme, dict) and theme.get("source") not in {None, "package"}:
            # 0.9.x used internal provenance labels such as ``compat_default``.
            # Runtime v2 only accepts package-owned themes; the actual color
            # values remain valid and should migrate unchanged.
            theme["source"] = "package"
        voice = value.get("voice")
        if isinstance(voice, Mapping):
            tone_refs = voice.get("tone_refs")
            if isinstance(tone_refs, str) and tone_refs.strip():
                extensions = value.setdefault("extensions", {})
                if not isinstance(extensions, dict):
                    raise LegacyImportError("LEGACY_CHARACTER_EXTENSION_INVALID", "staging", relative)
                hub = extensions.setdefault("sakura.tts", {})
                gpt_provider = extensions.setdefault("sakura.tts.gpt-sovits", {})
                genie_provider = extensions.setdefault("sakura.tts.genie", {})
                if not all(
                    isinstance(item, dict)
                    for item in (hub, gpt_provider, genie_provider)
                ):
                    raise LegacyImportError("LEGACY_CHARACTER_EXTENSION_INVALID", "staging", relative)
                selected = (
                    "sakura.tts.genie"
                    if (staged / "data/plugins/sakura.tts.genie/config.json").is_file()
                    else "sakura.tts.gpt-sovits"
                )
                hub.update({"enabled": True, "provider": selected})
                common = {
                    "toneRefs": tone_refs,
                    "refLang": str(voice.get("ref_lang") or "ja"),
                }
                gpt_provider.update(
                    {**common, "textLang": str(voice.get("text_lang") or "ja")}
                )
                genie_provider.update(common)
                if isinstance(voice.get("gpt_model"), str) and voice["gpt_model"].strip():
                    gpt_provider["gptModel"] = voice["gpt_model"]
                    genie_provider["gptModel"] = voice["gpt_model"]
                if isinstance(voice.get("sovits_model"), str) and voice["sovits_model"].strip():
                    gpt_provider["sovitsModel"] = voice["sovits_model"]
                    genie_provider["sovitsModel"] = voice["sovits_model"]
                if (
                    staged / "characters" / manifest.parent.name / "voice" / "onnx"
                ).is_dir() or _matching_onnx_dir(legacy_onnx_root, character_id) is not None:
                    genie_provider["onnxModelDir"] = "voice/onnx"
        manifest.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if len({value.casefold() for value in ids}) != len(ids):
        raise LegacyImportError("LEGACY_CHARACTER_ID_CONFLICT", "validating")
    _normalize_character_selection(staged, tuple(ids))
    return tuple(ids)


def _normalize_character_selection(staged: Path, ids: tuple[str, ...]) -> None:
    path = staged / "config" / "characters.yaml"
    value = _load_yaml(path, required=False)
    current = value.get("current_character_id")
    if not isinstance(current, str) or not current.strip():
        return
    matches = [character_id for character_id in ids if character_id.casefold() == current.strip().casefold()]
    if len(matches) == 1 and matches[0] != current.strip():
        value["current_character_id"] = matches[0]
        _write_yaml(path, value)


def _load_yaml(path: Path, *, required: bool) -> dict[str, Any]:
    if not path.is_file():
        if required:
            raise LegacyImportError("LEGACY_CONFIG_MISSING", "staging", path.name)
        return {}
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise LegacyImportError("LEGACY_CONFIG_INVALID", "staging", path.name) from exc
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise LegacyImportError("LEGACY_CONFIG_INVALID", "staging", path.name)
    return dict(value)


def _write_yaml(path: Path, value: object) -> None:
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _normalize_api(api: dict[str, Any]) -> None:
    profiles = api.get("api_profiles")
    legacy_model_names = _legacy_model_names(api)
    if isinstance(profiles, list):
        _normalize_legacy_provider_models(profiles, legacy_model_names)

    slots = api.get("model_slots")
    if isinstance(slots, Mapping):
        _drop_retired_api_fields(api)
        return

    if any(key in api for key in _PR110_SELECTION_FIELDS):
        text_enabled = _legacy_bool_value(api.get("text_enabled"), True)
        if text_enabled:
            text_profile_id = str(api.get("text_profile_id", "")).strip()
            text_model = str(api.get("text_model", _PR110_DEFAULT_TEXT_MODEL)).strip()
            vision_profile_id = str(api.get("vision_profile_id", "")).strip()
            vision_model = str(api.get("vision_model", _PR110_DEFAULT_VISION_MODEL)).strip()
            migrated_slots: dict[str, dict[str, str]] = {
                "chat": {"profile_id": text_profile_id, "model": text_model},
            }
            if vision_profile_id and vision_model:
                migrated_slots["vision_chat"] = {
                    "profile_id": vision_profile_id,
                    "model": vision_model,
                }
            api["model_slots"] = migrated_slots
        else:
            api["model_slots"] = {
                "chat": {
                    "profile_id": str(api.get("vision_profile_id", "")).strip(),
                    "model": str(
                        api.get("vision_model", _PR110_DEFAULT_VISION_MODEL)
                    ).strip(),
                }
            }
        _drop_retired_api_fields(api)
        return

    llm = api.get("llm")
    if not isinstance(llm, Mapping):
        llm = {}
    base_url = str(llm.get("base_url") or "").strip()
    api_key = str(llm.get("api_key") or "").strip()
    model = str(llm.get("model") or "").strip()
    if base_url or api_key or model:
        api["api_profiles"] = [
            {
                "id": "legacy",
                "alias": "旧版本配置",
                "base_url": base_url,
                "api_key": api_key,
                "models": ([{"name": model}] if model else []),
            }
        ]
        api["model_slots"] = {
            "chat": {"profile_id": "legacy" if model else "", "model": model}
        }
    else:
        api.setdefault("api_profiles", [])
        api.setdefault("model_slots", {"chat": {"profile_id": "", "model": ""}})
    _drop_retired_api_fields(api)


def _normalize_legacy_provider_models(
    profiles: list[object],
    fallback_names: list[str],
) -> None:
    """Convert model lists accepted by 0.9.10 to the current ``models[].name`` shape."""

    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        raw_models = profile.get("models")
        current_shape = isinstance(raw_models, list) and all(
            isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and bool(item["name"].strip())
            for item in raw_models
        )
        if current_shape and (raw_models or not fallback_names):
            continue

        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        if isinstance(raw_models, (list, tuple)):
            for item in raw_models:
                if isinstance(item, str):
                    name = item.strip()
                    entry: dict[str, Any] = {"name": name}
                elif isinstance(item, Mapping):
                    name = str(item.get("name", "")).strip()
                    entry = dict(item)
                    entry["name"] = name
                else:
                    continue
                if not name or name in seen:
                    continue
                normalized.append(entry)
                seen.add(name)
        if not normalized:
            normalized = [{"name": name} for name in fallback_names]
        profile["models"] = normalized


def _legacy_model_names(api: Mapping[str, Any]) -> list[str]:
    names: list[str] = []
    raw_names = api.get("model_names")
    if isinstance(raw_names, list):
        names.extend(str(item).strip() for item in raw_names if isinstance(item, str))
    for key in ("vision_model", "text_model"):
        names.append(str(api.get(key, "")).strip())
    llm = api.get("llm")
    if not isinstance(llm, Mapping):
        llm = {}
    names.append(str(llm.get("model", "")).strip())
    if any(key in api for key in ("text_enabled", "text_profile_id", "vision_profile_id")):
        if _legacy_bool_value(api.get("text_enabled"), True) and not str(
            api.get("text_model", "")
        ).strip():
            names.append(_PR110_DEFAULT_TEXT_MODEL)
        if not str(api.get("vision_model", "")).strip():
            names.append(_PR110_DEFAULT_VISION_MODEL)
    elif llm.get("base_url") and not str(llm.get("model", "")).strip():
        names.append(_PR110_DEFAULT_TEXT_MODEL)
    return _dedupe_strings(names)


def _dedupe_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _legacy_bool_value(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _drop_retired_api_fields(api: dict[str, Any]) -> None:
    for key in _RETIRED_API_FIELDS:
        api.pop(key, None)


def _merge_allowed_env(source: Path, api: dict[str, Any]) -> None:
    env_path = source / ".env"
    if not env_path.is_file():
        return
    allowed = _parse_allowed_legacy_env(env_path)
    if not allowed:
        return
    raw_llm = api.get("llm")
    if raw_llm is None:
        llm: dict[str, Any] = {}
        api["llm"] = llm
    elif isinstance(raw_llm, dict):
        llm = raw_llm
    else:
        return
    for env_key, llm_field in _LEGACY_ENV_TO_LLM_FIELD.items():
        if env_key in allowed and _missing_legacy_llm_value(llm.get(llm_field)):
            llm[llm_field] = allowed[env_key]


def _parse_allowed_legacy_env(env_path: Path) -> dict[str, str]:
    allowed: dict[str, str] = {}
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            if "=" not in line:
                continue
            name, _, value = line.partition("=")
            name = name.strip()
            if name not in _LEGACY_ENV_TO_LLM_FIELD:
                continue
            value = value.strip()
            if value[:1] in {'"', "'"}:
                quote = value[0]
                if len(value) < 2 or value[-1] != quote:
                    raise LegacyImportError("LEGACY_CONFIG_INVALID", "staging", ".env")
                value = value[1:-1]
                if quote == '"':
                    value = (
                        value.replace(r"\n", "\n")
                        .replace(r"\r", "\r")
                        .replace(r"\t", "\t")
                        .replace(r'\"', '"')
                        .replace(r"\\", "\\")
                    )
            allowed[name] = value
    except (OSError, UnicodeError):
        return {}
    return allowed


def _missing_legacy_llm_value(value: object) -> bool:
    return value is None or isinstance(value, str) and not value.strip()


def _migrate_ui(system: Mapping[str, Any]) -> dict[str, object]:
    legacy = system.get("ui")
    settings: dict[str, object] = {"first_run_guide_completed": False}
    if isinstance(legacy, Mapping):
        for key in _UI_KEYS:
            value = legacy.get(key)
            if isinstance(value, (str, int, float, bool)) and not isinstance(value, float):
                settings[key] = value
        if isinstance(legacy.get("always_on_top_enabled"), bool):
            settings["always_on_top"] = legacy["always_on_top_enabled"]
        theme = legacy.get("theme")
        if isinstance(theme, Mapping):
            settings["character_theme_overrides"] = {}
    return {"schema_version": 1, "domain": "ui", "settings": settings}


def _migrate_mcp(source: Path, path: Path) -> tuple[dict[str, object], int]:
    value = _load_yaml(path, required=False)
    servers = value.get("servers")
    if not isinstance(servers, Mapping):
        return {"enabled": False, "default_call_timeout": 20, "servers": {}}, 0
    kept: dict[str, object] = {}
    dropped = 0
    for name, raw in servers.items():
        if not isinstance(name, str) or not isinstance(raw, Mapping):
            dropped += 1
            continue
        server = _strip_deprecated_mcp_fields(raw)
        if not isinstance(server, Mapping) or _mcp_server_references_source(server, source):
            dropped += 1
            continue
        args = server.get("args")
        if isinstance(args, list):
            server["args"] = [
                "{core_root}/app/agent/mcp/web_search_server.py"
                if isinstance(item, str) and _is_legacy_web_search_path(item)
                else item
                for item in args
            ]
        command = server.get("command")
        if (
            isinstance(command, str)
            and command.replace("\\", "/").casefold() == "{base_dir}/runtime/python.exe"
        ):
            server["command"] = "{python}"
        kept[name] = server
    return {
        "enabled": bool(value.get("enabled", True)),
        "default_call_timeout": value.get("default_call_timeout", 20),
        "servers": kept,
    }, dropped


def _mcp_server_references_source(server: Mapping[str, object], source: Path) -> bool:
    source_root = _normalize_mcp_path(str(source)).rstrip("/")
    if not source_root:
        source_root = "/"

    values: list[str] = []
    command = server.get("command")
    if isinstance(command, str):
        values.append(command)
    args = server.get("args")
    if isinstance(args, list):
        values.extend(item for item in args if isinstance(item, str))
    env = server.get("env")
    if isinstance(env, Mapping):
        values.extend(item for item in env.values() if isinstance(item, str))
    return any(_mcp_value_references_source(value, source_root) for value in values)


def _mcp_value_references_source(value: str, source_root: str) -> bool:
    if any(
        _mcp_path_is_source_or_child(path, source_root)
        for path in _mcp_local_file_uri_paths(value)
    ):
        return True

    normalized = _normalize_mcp_path(value)
    start = 0
    while (index := normalized.find(source_root, start)) >= 0:
        before = normalized[index - 1] if index else ""
        end = index + len(source_root)
        after = normalized[end : end + 1]
        # Values may wrap a path in quotes, shell syntax, ``--key=...``, or a
        # Windows/POSIX path list. A slash before the match is deliberately not
        # a boundary: an HTTP URL containing the same text remains a URL.
        prefix_boundary = (
            not before or before.isspace() or before in "\"'=;:&|(<[{,@"
        )
        component_boundary = (
            source_root == "/" or not after or after == "/" or after in "\"';:&|)>]}"
        )
        if (
            prefix_boundary
            and component_boundary
            and not _mcp_match_is_http_authority(normalized, index)
        ):
            return True
        start = index + 1
    return False


def _normalize_mcp_path(value: str) -> str:
    normalized = value.strip().replace("\\", "/").casefold()
    normalized = normalized.replace("//?/unc/", "//").replace("//?/", "")
    return normalized


def _mcp_path_is_source_or_child(value: str, source_root: str) -> bool:
    candidate = _normalize_mcp_path(value)
    if candidate != "/":
        candidate = candidate.rstrip("/")
    if source_root == "/":
        return candidate.startswith("/")
    return candidate == source_root or candidate.startswith(f"{source_root}/")


def _mcp_local_file_uri_paths(value: str) -> list[str]:
    paths: list[str] = []
    folded = value.casefold()
    start = 0
    while (index := folded.find("file:", start)) >= 0:
        before = value[index - 1] if index else ""
        if before and not (before.isspace() or before in "\"'=;:&|(<[{,@"):
            start = index + len("file:")
            continue
        try:
            # Parse from each occurrence through the remaining value. URL
            # paths may legally contain sub-delims, and legacy configs also
            # contain unescaped spaces in Windows paths. A later ``file:`` is
            # visited independently by the loop.
            parsed = urlsplit(value[index:])
        except ValueError:
            start = index + len("file:")
            continue
        if parsed.scheme.casefold() != "file":
            start = index + len("file:")
            continue
        authority = unquote(parsed.netloc)
        path = unquote(parsed.path)
        if len(authority) == 2 and authority[0].isalpha() and authority[1] == ":":
            path = f"{authority}{path}"
        elif authority and authority.casefold() != "localhost":
            path = f"//{authority}{path}"
        elif (
            len(path) >= 3
            and path[0] == "/"
            and path[1].isalpha()
            and path[2] in {":", "|"}
        ):
            path = f"{path[1]}:{path[3:]}"
        paths.append(path)
        start = index + len("file:")
    return paths


def _mcp_match_is_http_authority(value: str, index: int) -> bool:
    for scheme in ("http:", "https:"):
        scheme_start = index - len(scheme)
        if scheme_start < 0 or value[scheme_start:index] != scheme:
            continue
        before = value[scheme_start - 1] if scheme_start else ""
        if not before or before.isspace() or before in "\"'=;:&|(<[{,@":
            return True
    return False


def _is_legacy_web_search_path(value: str) -> bool:
    return _normalize_mcp_path(value).endswith("/app/agent/mcp/web_search_server.py")


def _strip_deprecated_mcp_fields(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            key: _strip_deprecated_mcp_fields(child)
            for key, child in value.items()
            if key != "requires_confirmation"
        }
    if isinstance(value, list):
        return [_strip_deprecated_mcp_fields(child) for child in value]
    return value


def _migrate_plugins(
    path: Path, *, tts_provider: str | None
) -> list[dict[str, object]]:
    raw: object
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else []
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise LegacyImportError("LEGACY_PLUGIN_CONFIG_INVALID", "staging", "data/config/plugins.yaml") from exc
    enabled: dict[str, bool] = {}
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
                continue
            current = _BUILTIN_PLUGIN_ALIASES.get(item["id"])
            if current:
                enabled[current] = bool(item.get("enabled", True))
    if tts_provider:
        enabled.setdefault("sakura.tts", True)
        enabled.setdefault(tts_provider, True)
    enabled.setdefault("sakura.memory.mem0", True)
    return [{"id": key, "enabled": value} for key, value in sorted(enabled.items())]


def _legacy_tts_provider(raw: object) -> str | None:
    if not isinstance(raw, Mapping) or raw.get("enabled", True) is False:
        return None
    name = str(raw.get("provider") or "").strip().casefold().replace("-", "_")
    if name in {"genie", "genie_tts", "sakura_genie", "sakura.tts.genie"} or isinstance(
        raw.get("genie_tts"), Mapping
    ):
        return "sakura.tts.genie"
    if name in {
        "gpt_sovits",
        "sakura_gpt_sovits",
        "sakura.tts.gpt_sovits",
        "sakura.tts.gpt-sovits",
    } or isinstance(raw.get("gpt_sovits"), Mapping):
        return "sakura.tts.gpt-sovits"
    return None


def _matching_onnx_dir(root: Path | None, character_id: str) -> Path | None:
    if root is None or not root.is_dir():
        return None
    exact = root / character_id
    if exact.is_dir():
        return exact
    matches = [
        child
        for child in root.iterdir()
        if child.is_dir() and child.name.casefold() == character_id.casefold()
    ]
    return matches[0] if len(matches) == 1 else None


def _write_tts_plugin_config(
    staged: Path,
    raw: Mapping[str, Any],
    new_tts_root: Path,
    *,
    tts_provider: str | None,
) -> None:
    if tts_provider == "sakura.tts.genie":
        provider = raw.get("genie_tts")
        if not isinstance(provider, Mapping):
            return
        api_url = str(provider.get("api_url") or "").strip()
        endpoint = urlparse(api_url) if api_url else None
        local = endpoint is None or endpoint.hostname in {None, "127.0.0.1", "localhost", "::1"}
        config: dict[str, object] = {
            "endpointMode": "managed" if local else "custom",
            "apiUrl": api_url or "http://127.0.0.1:9881/",
            "timeoutSeconds": _bounded_timeout(provider.get("timeout_seconds")),
            "workDir": (
                user_facing_path(
                    _rewritten_tts_path(provider.get("work_dir"), new_tts_root, "cpu")
                )
                if local
                else ""
            ),
        }
        target = staged / "data" / "plugins" / "sakura.tts.genie" / "config.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return
    provider = raw.get("gpt_sovits")
    if not isinstance(provider, Mapping):
        return
    api_url = str(provider.get("api_url") or "").strip()
    endpoint = urlparse(api_url) if api_url else None
    local = endpoint is None or endpoint.hostname in {None, "127.0.0.1", "localhost", "::1"}
    work_dir = _rewritten_tts_path(provider.get("work_dir"), new_tts_root, "g50")
    config: dict[str, object] = {
        "endpointMode": "managed" if local else "custom",
        "ttsPath": endpoint.path or "/tts" if endpoint else "/tts",
        "timeoutSeconds": _bounded_timeout(provider.get("timeout_seconds")),
        "workDir": (
            user_facing_path(work_dir)
            if local
            else ""
        ),
        "pythonPath": "",
        "ttsConfigPath": "",
    }
    if local and "gpt_sovits_macos" in {part.casefold() for part in work_dir.parts}:
        bundle_root = new_tts_root / "gpt_sovits_macos"
        raw_python = str(provider.get("python_path") or "").strip()
        raw_config = str(provider.get("tts_config_path") or "").strip()
        python_path = (
            _rewritten_tts_path(raw_python, new_tts_root, "gpt_sovits_macos")
            if raw_python
            else bundle_root / "miniforge3/envs/gpt-sovits310/bin/python"
        )
        tts_config_path = (
            _rewritten_tts_path(raw_config, new_tts_root, "gpt_sovits_macos")
            if raw_config
            else bundle_root
            / "GPT-SoVITS/GPT_SoVITS/configs/tts_infer_sakura_macos.yaml"
        )
        config["pythonPath"] = user_facing_path(python_path)
        config["ttsConfigPath"] = user_facing_path(tts_config_path)
    if endpoint and not local:
        config["customBaseUrl"] = f"{endpoint.scheme}://{endpoint.netloc}"
    # Managed mode deliberately lets the v2 bundle resolver find the copied TTS root.
    # Persisting old absolute work/python/config paths would keep the old install alive.
    target = staged / "data" / "plugins" / "sakura.tts.gpt-sovits" / "config.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _rewritten_tts_path(value: object, root: Path, default_child: str) -> Path:
    text = str(value or "").strip().replace("\\", "/")
    parts = [part for part in text.split("/") if part]
    for index in range(len(parts) - 1, -1, -1):
        if parts[index].casefold() in {"cpu", "g50", "gpt", "gpt_sovits_macos"}:
            return root.joinpath(*parts[index:])
    return root / default_child


def _bounded_timeout(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return min(300, max(1, value))
    return 60
