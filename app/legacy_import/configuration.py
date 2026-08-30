from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

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
                if _matching_onnx_dir(legacy_onnx_root, character_id) is not None:
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
    slots = api.get("model_slots")
    if isinstance(profiles, list) and isinstance(slots, Mapping):
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


def _merge_allowed_env(source: Path, api: dict[str, Any]) -> None:
    env_path = source / ".env"
    if not env_path.is_file():
        return
    allowed: dict[str, str] = {}
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            name = name.strip()
            if name in {"OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"}:
                allowed[name] = value.strip().strip('"').strip("'")
    except (OSError, UnicodeError):
        return
    llm = api.setdefault("llm", {})
    if not isinstance(llm, dict):
        return
    llm.setdefault("api_key", allowed.get("OPENAI_API_KEY", ""))
    llm.setdefault("base_url", allowed.get("OPENAI_BASE_URL", ""))
    llm.setdefault("model", allowed.get("OPENAI_MODEL", ""))


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
    source_text = str(source).replace("\\", "/").casefold()
    for name, raw in servers.items():
        if not isinstance(name, str) or not isinstance(raw, Mapping):
            dropped += 1
            continue
        server = _strip_deprecated_mcp_fields(raw)
        encoded = json.dumps(server, ensure_ascii=False).replace("\\", "/")
        if source_text in encoded.casefold():
            dropped += 1
            continue
        args = server.get("args")
        if isinstance(args, list):
            server["args"] = [
                "{core_root}/app/agent/mcp/web_search_server.py"
                if isinstance(item, str) and item.endswith("/app/agent/mcp/web_search_server.py")
                else item
                for item in args
            ]
        if server.get("command") in {"{base_dir}/runtime/python.exe", "{base_dir}\\runtime\\python.exe"}:
            server["command"] = "{python}"
        kept[name] = server
    return {
        "enabled": bool(value.get("enabled", True)),
        "default_call_timeout": value.get("default_call_timeout", 20),
        "servers": kept,
    }, dropped


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
    config: dict[str, object] = {
        "endpointMode": "managed" if local else "custom",
        "ttsPath": endpoint.path or "/tts" if endpoint else "/tts",
        "timeoutSeconds": _bounded_timeout(provider.get("timeout_seconds")),
        "workDir": (
            user_facing_path(
                _rewritten_tts_path(provider.get("work_dir"), new_tts_root, "g50")
            )
            if local
            else ""
        ),
        "pythonPath": "",
        "ttsConfigPath": "",
    }
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
        if parts[index].casefold() in {"cpu", "g50", "gpt"}:
            return root.joinpath(*parts[index:])
    return root / default_child


def _bounded_timeout(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return min(300, max(1, value))
    return 60
