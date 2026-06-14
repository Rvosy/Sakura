"""app/config/migrations.py — 配置迁移工具。

提供从旧格式 (.env) 到新格式 (YAML) 的迁移逻辑。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config.yaml_config import load_yaml_mapping, save_yaml_mapping


def migrate_env_to_yaml(env_path: Path, api_yaml_path: Path, system_yaml_path: Path) -> dict[str, Any]:
    """从旧 .env 文件迁移配置到 data/config/*.yaml。

    返回迁移结果：{"migrated": [...], "skipped": [...], "errors": [...]}
    """
    result: dict[str, list[str]] = {"migrated": [], "skipped": [], "errors": []}

    env_vars = _parse_dotenv(env_path)
    if not env_vars:
        result["errors"].append(f"无法解析 .env 文件: {env_path}")
        return result

    api_data = load_yaml_mapping(api_yaml_path)
    system_data = load_yaml_mapping(system_yaml_path)

    api_changed = False
    system_changed = False

    # API 配置迁移
    api_mappings = {
        "BASE_URL": ("llm", "base_url"),
        "API_KEY": ("llm", "api_key"),
        "MODEL": ("llm", "model"),
        "API_TIMEOUT_SECONDS": ("llm", "timeout_seconds"),
        "TTS_ENABLED": ("tts", "enabled"),
        "GPT_SOVITS_API_URL": ("tts", "gpt_sovits", "api_url"),
        "GPT_SOVITS_REF_LANG": ("tts", "gpt_sovits", "ref_lang"),
        "GPT_SOVITS_TEXT_LANG": ("tts", "gpt_sovits", "text_lang"),
        "GPT_SOVITS_TIMEOUT_SECONDS": ("tts", "gpt_sovits", "timeout_seconds"),
    }

    for env_key, yaml_path_parts in api_mappings.items():
        if env_key in env_vars:
            _set_nested(api_data, list(yaml_path_parts), env_vars[env_key])
            api_changed = True
            result["migrated"].append(env_key)

    # 系统配置迁移
    system_mappings = {
        "SUBTITLE_LANGUAGE": ("ui", "subtitle_language"),
        "SCREEN_OBSERVATION_ENABLED": ("screen", "enabled"),
        "AUTONOMOUS_SCREEN_OBSERVATION_ENABLED": ("screen", "autonomous_enabled"),
        "PROACTIVE_CARE_ENABLED": ("screen_awareness", "enabled"),
        "PROACTIVE_SCREEN_CONTEXT_ENABLED": ("screen_awareness", "screen_context_enabled"),
        "PROACTIVE_CHECK_INTERVAL_MINUTES": ("screen_awareness", "check_interval_minutes"),
        "PROACTIVE_COOLDOWN_MINUTES": ("screen_awareness", "cooldown_minutes"),
        "AUTO_MEMORY_ENABLED": ("memory_curation", "enabled"),
        "AUTO_MEMORY_TRIGGER_TURNS": ("memory_curation", "trigger_turns"),
        "AUTO_MEMORY_BACKFILL_LIMIT": ("memory_curation", "backfill_limit"),
        "WINDOWS_MCP_ENABLED": ("mcp", "windows_enabled"),
        "SAKURA_DEBUG": ("debug", "enabled"),
        "SAKURA_DEBUG_BODY": ("debug", "body_enabled"),
        "SAKURA_DEBUG_FILE": ("debug", "file_enabled"),
    }

    for env_key, yaml_path_parts in system_mappings.items():
        if env_key in env_vars:
            _set_nested(system_data, list(yaml_path_parts), env_vars[env_key])
            system_changed = True
            result["migrated"].append(env_key)

    # CURRENT_CHARACTER_ID 单独处理
    if "CURRENT_CHARACTER_ID" in env_vars:
        from app.config.yaml_config import load_yaml_mapping as lym
        chars_path = api_yaml_path.parent / "characters.yaml"
        chars_data = lym(chars_path)
        chars_data["current_character_id"] = str(env_vars["CURRENT_CHARACTER_ID"]).strip()
        from app.config.yaml_config import save_yaml_mapping as sym
        sym(chars_path, chars_data)
        result["migrated"].append("CURRENT_CHARACTER_ID")

    if api_changed:
        save_yaml_mapping(api_yaml_path, api_data)
    if system_changed:
        save_yaml_mapping(system_yaml_path, system_data)

    return result


def _parse_dotenv(path: Path) -> dict[str, str]:
    """解析 .env 文件为键值对字典。"""
    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("").strip("'")
        if key:
            result[key] = value
    return result


def _set_nested(data: dict[str, Any], path: list[str], value: str) -> None:
    """将值设置到嵌套字典路径中。"""
    current = data
    for part in path[:-1]:
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]
    # 类型转换
    final_key = path[-1]
    current[final_key] = _coerce_type(value)


def _coerce_type(value: str) -> Any:
    """将字符串值转换为适当的类型。"""
    normalized = value.strip().lower()
    if normalized in {"true", "yes", "on", "1"}:
        return True
    if normalized in {"false", "no", "off", "0"}:
        return False
    try:
        return int(normalized)
    except ValueError:
        pass
    return value
