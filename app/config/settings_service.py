from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from app.agent.runtime_limits import RuntimeLoopSettings, normalize_runtime_loop_settings
from app.config.character_loader import CharacterRegistry
from app.config.yaml_config import load_yaml_mapping, save_yaml_mapping
from app.config.defaults import DEFAULT_BASE_URL, DEFAULT_TEXT_MODEL
from app.config.models import (
    DEFAULT_THEME_SETTINGS,
    MODEL_SLOT_CHAT,
    MODEL_SLOT_VISION_CHAT,
    ApiConfigProfile,
    ModelSelectionSettings,
    ModelSlotSelection,
    ThemeSettings,
    theme_colors_to_mapping,
    theme_from_mapping,
)
from app.llm.api_client import ApiSettings
from app.storage.paths import StoragePaths
from app.agent.screen_awareness import (
    SCREEN_AWARENESS_DEFAULT_CHECK_INTERVAL_MINUTES,
    SCREEN_AWARENESS_DEFAULT_COOLDOWN_MINUTES,
    SCREEN_AWARENESS_DEFAULT_SCREEN_CONTEXT_BATCH_LIMIT,
    SCREEN_AWARENESS_DEFAULT_SCREEN_CONTEXT_RESOLUTION,
    ScreenAwarenessSettings,
)


API_CONFIG_FILE = "api.yaml"
CHARACTERS_CONFIG_FILE = "characters.yaml"
SYSTEM_CONFIG_FILE = "system_config.yaml"
SYSTEM_CONFIG_VERSION = 1


@dataclass(frozen=True)
class DebugLogSettings:
    """运行日志配置。"""

    enabled: bool = True
    body_enabled: bool = False
    file_enabled: bool = True
    profile: str = "info"
    # 开发者选项:舞台调试框(画窗口/布局/实际立绘三框 + DPR 数值,排查布局/HiDPI)。
    stage_debug_overlay: bool = False
    # 舞台碰撞遮罩(默认开):setMask 到内容矩形并集,立绘四周空白点击穿透,避免误拖/挡点击。
    stage_collision_mask: bool = True


@dataclass(frozen=True)
class StartupSettings:
    """启动行为配置。"""

    launch_at_login: bool = False


BUBBLE_AUTO_HIDE_DEFAULT_DELAY_SECONDS = 5


@dataclass(frozen=True)
class BubbleSettings:
    """对话气泡无操作自动隐藏配置。"""

    auto_hide_enabled: bool = True
    auto_hide_delay_seconds: int = BUBBLE_AUTO_HIDE_DEFAULT_DELAY_SECONDS


BACKCHANNEL_MIN_DELAY_MS = 100
BACKCHANNEL_MAX_DELAY_MS = 5000
BACKCHANNEL_DEFAULT_DELAY_MS = 600
BACKCHANNEL_MODES = ("off", "rules", "hybrid")
BACKCHANNEL_DEFAULT_MODE = "rules"
# hybrid 后台分类超时(安全网):超时按无标签落兜底,不阻塞迟到的接话。
# 仅对 hybrid 生效;规则分类同步不触发。0 表示不设超时。
BACKCHANNEL_MIN_TIMEOUT_MS = 0
BACKCHANNEL_MAX_TIMEOUT_MS = 2000
BACKCHANNEL_DEFAULT_TIMEOUT_MS = 400


@dataclass(frozen=True)
class BackchannelSettings:
    """本地快速接话层配置。

    默认关闭;rules 为纯规则模式,hybrid 为 rules-first + 本地 embedding 意图泛化。
    """

    enabled: bool = False
    mode: str = BACKCHANNEL_DEFAULT_MODE
    delay_ms: int = BACKCHANNEL_DEFAULT_DELAY_MS
    probability: float = 1.0
    tts_enabled: bool = False
    timeout_ms: int = BACKCHANNEL_DEFAULT_TIMEOUT_MS

    @property
    def active(self) -> bool:
        return self.enabled and self.mode != "off"

    def normalized(self) -> "BackchannelSettings":
        mode = self.mode if self.mode in BACKCHANNEL_MODES else BACKCHANNEL_DEFAULT_MODE
        delay = max(
            BACKCHANNEL_MIN_DELAY_MS,
            min(BACKCHANNEL_MAX_DELAY_MS, int(self.delay_ms)),
        )
        probability = max(0.0, min(1.0, float(self.probability)))
        timeout = max(
            BACKCHANNEL_MIN_TIMEOUT_MS,
            min(BACKCHANNEL_MAX_TIMEOUT_MS, int(self.timeout_ms)),
        )
        return BackchannelSettings(
            enabled=bool(self.enabled),
            mode=mode,
            delay_ms=delay,
            probability=probability,
            tts_enabled=bool(self.tts_enabled),
            timeout_ms=timeout,
        )


@dataclass(frozen=True)
class AppSettingsService:
    """读取共享 YAML 配置，只写入当前角色选择和屏幕感知设置。"""

    base_dir: Path

    @property
    def config_dir(self) -> Path:
        return StoragePaths(self.base_dir).config_dir

    @property
    def api_config_path(self) -> Path:
        return self.config_dir / API_CONFIG_FILE

    @property
    def characters_config_path(self) -> Path:
        return self.config_dir / CHARACTERS_CONFIG_FILE

    @property
    def system_config_path(self) -> Path:
        return self.config_dir / SYSTEM_CONFIG_FILE

    def load_api_settings(self) -> ApiSettings:
        data = self._api_section("llm")
        timeout_seconds = _int_value(
            data.get("timeout_seconds"),
            60,
        )
        return ApiSettings(
            base_url=str(data.get("base_url", DEFAULT_BASE_URL)).strip().rstrip("/"),
            api_key=str(data.get("api_key", "")).strip(),
            model=str(data.get("model", DEFAULT_TEXT_MODEL)).strip(),
            timeout_seconds=timeout_seconds,
            temperature=_optional_float(data.get("temperature"), minimum=0.0, maximum=2.0),
            top_p=_optional_float(data.get("top_p"), minimum=0.0, maximum=1.0),
            max_tokens=_optional_positive_int(data.get("max_tokens")),
        )

    def load_api_profiles(self) -> list[ApiConfigProfile]:
        """从 api.yaml 读取当前 api_profiles 列表。"""
        data = load_yaml_mapping(self.api_config_path)
        _assert_no_retired_api_fields(data)
        raw_profiles = data.get("api_profiles")
        if raw_profiles is None:
            return []
        if not isinstance(raw_profiles, list):
            raise ValueError("Provider 配置格式无效。")
        profiles: list[ApiConfigProfile] = []
        for raw in raw_profiles:
            if not isinstance(raw, dict):
                raise ValueError("Provider 配置格式无效。")
            profiles.append(
                ApiConfigProfile(
                    id=_required_string(raw.get("id"), "Provider ID"),
                    alias=_required_string(raw.get("alias"), "Provider 别名"),
                    base_url=_required_string(raw.get("base_url"), "Provider Base URL").rstrip("/"),
                    api_key=_string(raw.get("api_key"), "Provider API Key"),
                    models=_current_provider_models(raw.get("models")),
                )
            )
        return profiles

    def load_model_selection(self) -> ModelSelectionSettings:
        data = load_yaml_mapping(self.api_config_path)
        _assert_no_retired_api_fields(data)
        raw_slots = data.get("model_slots")
        if raw_slots is None:
            return ModelSelectionSettings()
        if not isinstance(raw_slots, dict):
            raise ValueError("模型槽配置格式无效。")
        return ModelSelectionSettings(
            chat=_slot_selection(raw_slots.get(MODEL_SLOT_CHAT)),
            vision_chat=_optional_slot_selection(raw_slots.get(MODEL_SLOT_VISION_CHAT)),
        )

    def load_runtime_loop_settings(self) -> RuntimeLoopSettings:
        tool_loop = self._system_section("tool_loop")
        defaults = RuntimeLoopSettings()
        return normalize_runtime_loop_settings(
            RuntimeLoopSettings(
                max_agent_steps_per_turn=_int_value(
                    tool_loop.get("max_agent_steps_per_turn"),
                    defaults.max_agent_steps_per_turn,
                ),
                max_tool_calls_per_step=_int_value(
                    tool_loop.get("max_tool_calls_per_step"),
                    defaults.max_tool_calls_per_step,
                ),
                max_tool_calls_per_turn=_int_value(
                    tool_loop.get("max_tool_calls_per_turn"),
                    defaults.max_tool_calls_per_turn,
                ),
            )
        )

    def load_debug_log_settings(self) -> DebugLogSettings:
        debug = self._system_section("debug")
        if "raw_tts_service_enabled" in debug:
            raise ValueError("调试配置使用了已废止的字段。")
        return DebugLogSettings(
            enabled=_bool_value(debug.get("enabled"), True),
            body_enabled=_bool_value(debug.get("body_enabled"), False),
            file_enabled=_bool_value(debug.get("file_enabled"), True),
            profile=_log_level_value(debug.get("profile"), "info"),
            stage_debug_overlay=_bool_value(debug.get("stage_debug_overlay"), False),
            stage_collision_mask=_bool_value(debug.get("stage_collision_mask"), True),
        )

    def load_startup_settings(self) -> StartupSettings:
        startup = self._system_section("startup")
        return StartupSettings(
            launch_at_login=_bool_value(startup.get("launch_at_login"), False),
        )

    def load_theme_settings(self) -> ThemeSettings:
        ui = self._system_section("ui")
        saved = theme_from_mapping(ui.get("theme"))
        return replace(
            DEFAULT_THEME_SETTINGS,
            ai_enabled=saved.ai_enabled,
            visual_effect_mode=saved.visual_effect_mode,
        )

    def load_character_theme_overrides(self) -> dict[str, ThemeSettings]:
        ui = self._system_section("ui")
        raw = ui.get("character_theme_overrides")
        if not isinstance(raw, dict):
            return {}
        overrides: dict[str, ThemeSettings] = {}
        for character_id, value in raw.items():
            key = str(character_id).strip()
            if not key or not isinstance(value, dict):
                continue
            theme = theme_from_mapping(value).normalized()
            overrides[key] = ThemeSettings(**theme_colors_to_mapping(theme))
        return overrides

    def load_screen_awareness_settings(self) -> ScreenAwarenessSettings:
        screen_awareness = self._system_section("screen_awareness")
        enabled = bool(
            _bool_value(screen_awareness.get("enabled"), True)
            and _bool_value(screen_awareness.get("screen_context_enabled"), True)
        )
        return ScreenAwarenessSettings(
            enabled=enabled,
            screen_context_enabled=True,
            check_interval_minutes=_int_value(
                screen_awareness.get("check_interval_minutes"),
                SCREEN_AWARENESS_DEFAULT_CHECK_INTERVAL_MINUTES,
            ),
            cooldown_minutes=_int_value(
                screen_awareness.get("cooldown_minutes"),
                SCREEN_AWARENESS_DEFAULT_COOLDOWN_MINUTES,
            ),
            screen_context_batch_limit=_int_value(
                screen_awareness.get("screen_context_batch_limit"),
                SCREEN_AWARENESS_DEFAULT_SCREEN_CONTEXT_BATCH_LIMIT,
            ),
            screen_context_resolution=str(
                screen_awareness.get(
                    "screen_context_resolution",
                    SCREEN_AWARENESS_DEFAULT_SCREEN_CONTEXT_RESOLUTION,
                )
            ),
        )

    def save_screen_awareness_settings(self, settings: ScreenAwarenessSettings) -> None:
        normalized = settings.normalized()
        data = self._system_document()
        section = data.get("screen_awareness")
        preserved = dict(section) if isinstance(section, dict) else {}
        preserved.pop("screen_context_enabled", None)
        enabled = bool(normalized.enabled and normalized.screen_context_enabled)
        preserved.update(
            {
                "enabled": enabled,
                "check_interval_minutes": int(normalized.check_interval_minutes),
                "cooldown_minutes": int(normalized.cooldown_minutes),
                "screen_context_batch_limit": int(normalized.screen_context_batch_limit),
                "screen_context_resolution": normalized.screen_context_resolution,
            }
        )
        data["screen_awareness"] = preserved
        save_yaml_mapping(self.system_config_path, data)

    def load_bubble_settings(self) -> BubbleSettings:
        ui = self._system_section("ui")
        return BubbleSettings(
            auto_hide_enabled=_bool_value(ui.get("bubble_auto_hide_enabled"), True),
            auto_hide_delay_seconds=_int_value(
                ui.get("bubble_auto_hide_delay_seconds"),
                BUBBLE_AUTO_HIDE_DEFAULT_DELAY_SECONDS,
            ),
        )

    def load_backchannel_settings(self) -> BackchannelSettings:
        section = self._system_section("backchannel")
        return BackchannelSettings(
            enabled=_bool_value(section.get("enabled"), False),
            mode=str(section.get("mode", BACKCHANNEL_DEFAULT_MODE) or BACKCHANNEL_DEFAULT_MODE),
            delay_ms=_int_value(section.get("delay_ms"), BACKCHANNEL_DEFAULT_DELAY_MS),
            probability=_float_value(section.get("probability"), 1.0),
            tts_enabled=_bool_value(section.get("tts_enabled"), False),
            timeout_ms=_int_value(section.get("timeout_ms"), BACKCHANNEL_DEFAULT_TIMEOUT_MS),
        ).normalized()

    def load_current_character_id(
        self,
        character_registry: CharacterRegistry,
    ) -> str | None:
        data = load_yaml_mapping(self.characters_config_path)
        configured = str(data.get("current_character_id", "")).strip()
        if configured in character_registry.profiles:
            return configured
        return None

    def save_current_character_id(
        self,
        character_registry: CharacterRegistry,
        character_id: str,
    ) -> None:
        character_registry.get(character_id)
        data = load_yaml_mapping(self.characters_config_path)
        data["current_character_id"] = character_id
        save_yaml_mapping(self.characters_config_path, data)

    def _api_section(self, name: str) -> dict[str, Any]:
        return _mapping(load_yaml_mapping(self.api_config_path).get(name))

    def _system_section(self, name: str) -> dict[str, Any]:
        return _mapping(self._system_document().get(name))

    def _system_document(self) -> dict[str, Any]:
        if not self.system_config_path.exists():
            return {"config_version": SYSTEM_CONFIG_VERSION}
        data = load_yaml_mapping(self.system_config_path)
        version = data.get("config_version")
        if isinstance(version, bool) or not isinstance(version, int) or version != SYSTEM_CONFIG_VERSION:
            raise ValueError("系统配置版本不受支持。")
        return data


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _assert_no_retired_api_fields(data: dict[str, Any]) -> None:
    retired = {
        "model_names",
        "text_enabled",
        "text_profile_id",
        "text_model",
        "vision_profile_id",
        "vision_model",
    }
    if retired.intersection(data):
        raise ValueError("API 配置使用了已废止的字段。")


def _slot_selection(raw: object) -> ModelSlotSelection:
    if raw is None:
        return ModelSlotSelection()
    if not isinstance(raw, dict):
        raise ValueError("模型槽配置格式无效。")
    return ModelSlotSelection(
        profile_id=_string(raw.get("profile_id"), "Provider ID"),
        model=_string(raw.get("model"), "模型 ID"),
        context_window_tokens=_optional_context_window(raw.get("context_window_tokens")),
    )


def _optional_context_window(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 4_096 <= value <= 2_000_000 else None


def _optional_slot_selection(raw: object) -> ModelSlotSelection | None:
    if raw is None:
        return None
    selection = _slot_selection(raw)
    return selection if selection.configured else None


def _current_provider_models(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise ValueError("Provider 模型列表格式无效。")
    names: list[str] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Provider 模型列表格式无效。")
        name = _required_string(item.get("name"), "模型 ID")
        if name not in names:
            names.append(name)
    return tuple(names)


def _string(value: object, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field} 格式无效。")
    return value.strip()


def _required_string(value: object, field: str) -> str:
    text = _string(value, field)
    if not text:
        raise ValueError(f"{field} 不能为空。")
    return text


def _int_value(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _float_value(value: Any, default: float) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _optional_float(value: Any, *, minimum: float, maximum: float) -> float | None:
    """解析可选浮点参数；缺省或非法返回 None，合法值 clamp 到 [minimum, maximum]。"""
    if value is None:
        return None
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return max(minimum, min(maximum, parsed))


def _optional_positive_int(value: Any) -> int | None:
    """解析可选正整数；缺省、非法或非正返回 None。"""
    if value is None:
        return None
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _bool_value(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return default


def _log_level_value(value: Any, default: str) -> str:
    """验证并规范化当前日志级别值。"""
    raw = str(value or default).strip().lower()
    if raw in {"error", "warn", "info", "debug", "trace"}:
        return raw
    return default
