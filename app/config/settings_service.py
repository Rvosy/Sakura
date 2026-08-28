from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from app.agent.runtime_limits import RuntimeLoopSettings, normalize_runtime_loop_settings
from app.config.character_loader import CharacterProfile, CharacterRegistry
from app.config.yaml_config import load_yaml_mapping, save_yaml_mapping
from app.config.defaults import DEFAULT_BASE_URL, DEFAULT_TEXT_MODEL, DEFAULT_VISION_MODEL
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
    theme_to_mapping,
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
from app.voice.tts_settings import (
    DEFAULT_GENIE_TTS_API_URL,
    DEFAULT_GPT_SOVITS_BASE_URL,
    DEFAULT_GPT_SOVITS_API_URL,
    DEFAULT_GPT_SOVITS_TTS_PATH,
    TTS_PROVIDER_GENIE,
    TTS_PROVIDER_GPT_SOVITS,
    GPTSoVITSTTSSettings,
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


BUBBLE_AUTO_HIDE_MIN_DELAY_SECONDS = 1
BUBBLE_AUTO_HIDE_MAX_DELAY_SECONDS = 120
BUBBLE_AUTO_HIDE_DEFAULT_DELAY_SECONDS = 5


@dataclass(frozen=True)
class BubbleSettings:
    """对话气泡无操作自动隐藏配置。"""

    auto_hide_enabled: bool = True
    auto_hide_delay_seconds: int = BUBBLE_AUTO_HIDE_DEFAULT_DELAY_SECONDS

    def normalized(self) -> "BubbleSettings":
        delay = max(
            BUBBLE_AUTO_HIDE_MIN_DELAY_SECONDS,
            min(BUBBLE_AUTO_HIDE_MAX_DELAY_SECONDS, int(self.auto_hide_delay_seconds)),
        )
        return BubbleSettings(
            auto_hide_enabled=bool(self.auto_hide_enabled),
            auto_hide_delay_seconds=delay,
        )


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
    """集中管理 Runtime v2 使用的 YAML 配置。"""

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

    def save_api_settings(self, settings: ApiSettings) -> None:
        data = load_yaml_mapping(self.api_config_path)
        llm_data: dict[str, Any] = {
            "base_url": settings.base_url.strip().rstrip("/"),
            "api_key": settings.api_key.strip(),
            "model": settings.model.strip(),
            "timeout_seconds": int(settings.timeout_seconds),
        }
        # 仅写入用户显式配置的高级参数。
        if settings.temperature is not None:
            llm_data["temperature"] = float(settings.temperature)
        if settings.top_p is not None:
            llm_data["top_p"] = float(settings.top_p)
        if settings.max_tokens is not None:
            llm_data["max_tokens"] = int(settings.max_tokens)
        data["llm"] = llm_data
        save_yaml_mapping(self.api_config_path, data)

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

    def save_api_profiles(self, profiles: list[ApiConfigProfile]) -> None:
        data = load_yaml_mapping(self.api_config_path)
        data["api_profiles"] = [
            {
                "id": p.id,
                "alias": p.alias,
                "base_url": p.base_url.strip().rstrip("/"),
                "api_key": p.api_key.strip(),
                "models": [{"name": name} for name in _dedupe(p.models)],
            }
            for p in profiles
        ]
        save_yaml_mapping(self.api_config_path, data)

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

    def save_model_selection(self, settings: ModelSelectionSettings) -> None:
        data = load_yaml_mapping(self.api_config_path)
        slots: dict[str, dict[str, Any]] = {}
        for slot in (
            MODEL_SLOT_CHAT,
            MODEL_SLOT_VISION_CHAT,
        ):
            selection = settings.get(slot)
            if selection is None:
                continue
            if slot != MODEL_SLOT_CHAT and not selection.configured:
                continue
            slots[slot] = {
                "profile_id": selection.profile_id.strip(),
                "model": selection.model.strip(),
            }
            if selection.context_window_tokens is not None:
                slots[slot]["context_window_tokens"] = selection.context_window_tokens
        data["model_slots"] = slots
        save_yaml_mapping(self.api_config_path, data)

    def load_tts_settings(
        self,
        *,
        validate_enabled: bool = True,
        character_profile: CharacterProfile | None = None,
    ) -> GPTSoVITSTTSSettings:
        data = self._api_section("tts")
        gpt_sovits = _mapping(data.get("gpt_sovits"))
        genie_tts = _mapping(data.get("genie_tts"))
        raw_provider = str(data.get("provider", "")).strip().lower()
        provider = raw_provider or TTS_PROVIDER_GPT_SOVITS
        if provider not in {TTS_PROVIDER_GPT_SOVITS, TTS_PROVIDER_GENIE}:
            raise ValueError("不支持的 TTS Provider 配置。")
        enabled = _bool_value(data.get("enabled"), False)

        # 无语音角色不能启用 TTS，启动和设置页加载时直接降级为关闭。
        if enabled and character_profile is not None and character_profile.voice is None:
            enabled = False

        provider_data = genie_tts if provider == TTS_PROVIDER_GENIE else gpt_sovits
        default_api_url = DEFAULT_GENIE_TTS_API_URL if provider == TTS_PROVIDER_GENIE else DEFAULT_GPT_SOVITS_API_URL
        if provider == TTS_PROVIDER_GPT_SOVITS and any(
            key in provider_data for key in ("api_url", "work_dir", "python_path", "tts_config_path")
        ):
            raise ValueError("GPT-SoVITS 配置使用了已废止的字段。")
        runtime_data = _mapping(provider_data.get("managed_runtime"))
        custom_base_url = str(provider_data.get("custom_base_url") or "").strip().rstrip("/") or None
        tts_path = str(provider_data.get("tts_path") or DEFAULT_GPT_SOVITS_TTS_PATH).strip()
        if not tts_path.startswith("/"):
            tts_path = f"/{tts_path}"
        api_url = (
            _join_gpt_sovits_url(custom_base_url or DEFAULT_GPT_SOVITS_BASE_URL, tts_path)
            if provider == TTS_PROVIDER_GPT_SOVITS
            else str(provider_data.get("api_url", default_api_url)).strip()
        )
        runtime_source = provider_data if provider == TTS_PROVIDER_GENIE else runtime_data
        work_dir = _optional_path(runtime_source.get("work_dir"), self.base_dir)
        python_path = _optional_path(runtime_source.get("python_path"), self.base_dir)
        tts_config_path = _optional_path(runtime_source.get("tts_config_path"), self.base_dir)
        remote_reference_root = str(provider_data.get("remote_reference_root") or "").strip() or None
        ref_lang = "ja"
        text_lang = "ja"
        timeout_seconds = _int_value(provider_data.get("timeout_seconds"), 60)
        onnx_model_dir = _optional_path(genie_tts.get("onnx_model_dir"), self.base_dir)
        if character_profile is not None:
            if provider == TTS_PROVIDER_GENIE and onnx_model_dir is None:
                onnx_model_dir = StoragePaths(self.base_dir).tts_bundle_onnx_for(character_profile.id)
            settings = GPTSoVITSTTSSettings.from_character_profile(
                character_profile=character_profile,
                enabled=enabled,
                api_url=api_url,
                ref_lang=ref_lang,
                text_lang=text_lang,
                timeout_seconds=timeout_seconds,
                provider=provider,
                work_dir=work_dir,
                python_path=python_path,
                tts_config_path=tts_config_path,
                onnx_model_dir=onnx_model_dir,
                custom_base_url=custom_base_url,
                tts_path=tts_path,
                remote_reference_root=remote_reference_root,
                validate_enabled=validate_enabled,
            )
        else:
            if provider == TTS_PROVIDER_GENIE and onnx_model_dir is None:
                onnx_model_dir = StoragePaths(self.base_dir).tts_bundle_onnx_for("default")
            settings = GPTSoVITSTTSSettings(
                enabled=enabled,
                api_url=api_url,
                ref_audio_path=self.base_dir / "ref" / "VO01_2210.ogg",
                ref_text_path=self.base_dir / "ref" / "text.txt",
                ref_text="",
                provider=provider,
                work_dir=work_dir,
                python_path=python_path,
                tts_config_path=tts_config_path,
                character_name="sakura",
                onnx_model_dir=onnx_model_dir,
                ref_lang=ref_lang,
                text_lang=text_lang,
                timeout_seconds=timeout_seconds,
                custom_base_url=custom_base_url,
                tts_path=tts_path,
                remote_reference_root=remote_reference_root,
            )
        if settings.enabled and validate_enabled:
            settings.validate()
        return settings

    def save_tts_settings(self, settings: GPTSoVITSTTSSettings) -> None:
        if settings.provider not in {TTS_PROVIDER_GPT_SOVITS, TTS_PROVIDER_GENIE}:
            raise ValueError("不支持的 TTS Provider 配置。")
        data = load_yaml_mapping(self.api_config_path)
        existing_tts = data.get("tts")
        tts_data: dict[str, object] = (
            dict(existing_tts) if isinstance(existing_tts, dict) else {}
        )
        # Runtime v2 keeps the selected provider while TTS is disabled so the
        # user can configure/install/test it before enabling chat playback.
        saved_provider = (
            TTS_PROVIDER_GENIE
            if settings.provider == TTS_PROVIDER_GENIE
            else TTS_PROVIDER_GPT_SOVITS
        )
        section_provider = TTS_PROVIDER_GENIE if settings.provider == TTS_PROVIDER_GENIE else TTS_PROVIDER_GPT_SOVITS
        tts_data["provider"] = saved_provider
        tts_data["enabled"] = bool(settings.enabled)
        if section_provider == TTS_PROVIDER_GENIE:
            genie_data = _mapping(tts_data.get("genie_tts"))
            genie_data.update({
                "api_url": settings.api_url.strip() or DEFAULT_GENIE_TTS_API_URL,
                "work_dir": _path_for_config(settings.work_dir, self.base_dir),
                "onnx_model_dir": _path_for_config(settings.onnx_model_dir, self.base_dir),
                "ref_lang": settings.ref_lang.strip(),
                "text_lang": settings.text_lang.strip(),
                "timeout_seconds": int(settings.timeout_seconds),
            })
            tts_data["genie_tts"] = genie_data
        elif section_provider == TTS_PROVIDER_GPT_SOVITS:
            gpt_data = _mapping(tts_data.get("gpt_sovits"))
            custom_base_url = str(settings.custom_base_url or "").strip().rstrip("/") or None
            tts_path = str(settings.tts_path or DEFAULT_GPT_SOVITS_TTS_PATH).strip()
            if not tts_path.startswith("/"):
                tts_path = f"/{tts_path}"
            if any(key in gpt_data for key in ("api_url", "work_dir", "python_path", "tts_config_path")):
                raise ValueError("GPT-SoVITS 配置使用了已废止的字段。")
            gpt_data.update({
                "custom_base_url": custom_base_url,
                "tts_path": tts_path,
                "remote_reference_root": settings.remote_reference_root,
                "managed_runtime": {
                    "work_dir": _path_for_config(settings.work_dir, self.base_dir),
                    "python_path": _path_for_config(settings.python_path, self.base_dir),
                    "tts_config_path": _path_for_config(settings.tts_config_path, self.base_dir),
                },
                "ref_lang": settings.ref_lang.strip(),
                "text_lang": settings.text_lang.strip(),
                "timeout_seconds": int(settings.timeout_seconds),
            })
            tts_data["gpt_sovits"] = gpt_data
        data["tts"] = tts_data
        save_yaml_mapping(self.api_config_path, data)

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

    def save_runtime_loop_settings(self, settings: RuntimeLoopSettings) -> None:
        normalized = normalize_runtime_loop_settings(settings)
        self.save_system_values(
            "tool_loop",
            {
                "max_agent_steps_per_turn": int(normalized.max_agent_steps_per_turn),
                "max_tool_calls_per_step": int(normalized.max_tool_calls_per_step),
                "max_tool_calls_per_turn": int(normalized.max_tool_calls_per_turn),
            },
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

    def save_debug_log_settings(self, settings: DebugLogSettings) -> None:
        data = self._system_document()
        debug = _mapping(data.get("debug"))
        if "raw_tts_service_enabled" in debug:
            raise ValueError("调试配置使用了已废止的字段。")
        debug.update(
            {
                "enabled": bool(settings.enabled),
                "body_enabled": bool(settings.body_enabled),
                "file_enabled": bool(settings.file_enabled),
                "profile": _log_level_value(settings.profile, "info"),
                "stage_debug_overlay": bool(settings.stage_debug_overlay),
                "stage_collision_mask": bool(settings.stage_collision_mask),
            }
        )
        data["debug"] = debug
        save_yaml_mapping(self.system_config_path, data)

    def load_startup_settings(self) -> StartupSettings:
        startup = self._system_section("startup")
        return StartupSettings(
            launch_at_login=_bool_value(startup.get("launch_at_login"), False),
        )

    def save_startup_settings(self, settings: StartupSettings) -> None:
        self.save_system_values(
            "startup",
            {"launch_at_login": bool(settings.launch_at_login)},
        )

    def load_theme_settings(self) -> ThemeSettings:
        ui = self._system_section("ui")
        saved = theme_from_mapping(ui.get("theme"))
        return replace(
            DEFAULT_THEME_SETTINGS,
            ai_enabled=saved.ai_enabled,
            visual_effect_mode=saved.visual_effect_mode,
        )

    def save_theme_settings(self, settings: ThemeSettings) -> None:
        ui = self._system_section("ui")
        normalized = (settings or DEFAULT_THEME_SETTINGS).normalized()
        ui["theme"] = theme_to_mapping(
            replace(
                DEFAULT_THEME_SETTINGS,
                ai_enabled=normalized.ai_enabled,
                visual_effect_mode=normalized.visual_effect_mode,
            )
        )
        data = self._system_document()
        data["ui"] = ui
        save_yaml_mapping(self.system_config_path, data)

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

    def load_character_theme_override(self, character_id: str) -> ThemeSettings | None:
        return self.load_character_theme_overrides().get(str(character_id).strip())

    def save_character_theme_override(self, character_id: str, settings: ThemeSettings) -> None:
        key = str(character_id).strip()
        if not key:
            raise ValueError("角色主题覆盖缺少角色 ID。")
        ui = self._system_section("ui")
        raw = ui.get("character_theme_overrides")
        overrides = dict(raw) if isinstance(raw, dict) else {}
        overrides[key] = theme_colors_to_mapping(settings or DEFAULT_THEME_SETTINGS)
        ui["character_theme_overrides"] = overrides
        data = self._system_document()
        data["ui"] = ui
        save_yaml_mapping(self.system_config_path, data)

    def delete_character_theme_override(self, character_id: str) -> None:
        key = str(character_id).strip()
        if not key:
            return
        ui = self._system_section("ui")
        raw = ui.get("character_theme_overrides")
        if not isinstance(raw, dict) or key not in raw:
            return
        overrides = dict(raw)
        overrides.pop(key, None)
        if overrides:
            ui["character_theme_overrides"] = overrides
        else:
            ui.pop("character_theme_overrides", None)
        data = self._system_document()
        data["ui"] = ui
        save_yaml_mapping(self.system_config_path, data)

    def load_screen_awareness_settings(self) -> ScreenAwarenessSettings:
        screen_awareness = self._system_section("screen_awareness")
        if "screen_context_enabled" in screen_awareness:
            raise ValueError("屏幕感知配置使用了已废止的字段。")
        enabled = _bool_value(screen_awareness.get("enabled"), True)
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
        if "screen_context_enabled" in preserved:
            raise ValueError("屏幕感知配置使用了已废止的字段。")
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

    def save_bubble_settings(self, settings: BubbleSettings) -> None:
        # 气泡配置位于 ui section 下，须读-改-写以保留 subtitle_language/theme 等其他 ui 键。
        normalized = settings.normalized()
        ui = self._system_section("ui")
        ui["bubble_auto_hide_enabled"] = bool(normalized.auto_hide_enabled)
        ui["bubble_auto_hide_delay_seconds"] = int(normalized.auto_hide_delay_seconds)
        data = self._system_document()
        data["ui"] = ui
        save_yaml_mapping(self.system_config_path, data)

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

    def save_backchannel_settings(self, settings: BackchannelSettings) -> None:
        normalized = settings.normalized()
        data = self._system_document()
        data["backchannel"] = {
            "enabled": bool(normalized.enabled),
            "mode": normalized.mode,
            "delay_ms": int(normalized.delay_ms),
            "probability": float(normalized.probability),
            "tts_enabled": bool(normalized.tts_enabled),
            "timeout_ms": int(normalized.timeout_ms),
        }
        save_yaml_mapping(self.system_config_path, data)

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

    def load_system_values(self, section: str) -> dict[str, Any]:
        return self._system_section(section)

    def save_system_values(self, section: str, values: dict[str, Any]) -> None:
        data = self._system_document()
        current = _mapping(data.get(section))
        current.update(values)
        data[section] = current
        save_yaml_mapping(self.system_config_path, data)

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


def _join_gpt_sovits_url(base_url: str | None, tts_path: str) -> str:
    base_url = str(base_url or DEFAULT_GPT_SOVITS_BASE_URL).strip()
    path = str(tts_path or DEFAULT_GPT_SOVITS_TTS_PATH).strip()
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base_url.rstrip('/')}{path}"


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


def _dedupe(values: object) -> list[str]:
    result: list[str] = []
    if isinstance(values, (str, bytes)):
        candidates = [str(values)]
    else:
        candidates = list(values or [])  # type: ignore[arg-type]
    for value in candidates:
        text = str(value).strip()
        if text and text not in result:
            result.append(text)
    return result


def _optional_path(value: Any, base_dir: Path) -> Path | None:
    if value is None:
        return None
    text = str(value).strip().strip('"').strip("'")
    if not text:
        return None
    path = Path(text)
    if path.is_absolute():
        return path
    return base_dir / path


def _path_for_config(path: Path | None, base_dir: Path) -> str:
    if path is None:
        return ""
    try:
        return path.resolve().relative_to(base_dir.resolve()).as_posix()
    except ValueError:
        return str(path)


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
