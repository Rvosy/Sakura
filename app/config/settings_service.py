from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from app.agent.mcp.settings import MCPRuntimeSettings, normalize_mcp_runtime_settings
from app.agent.runtime_limits import RuntimeLoopSettings, normalize_runtime_loop_settings
from app.config.character_loader import DEFAULT_CHARACTER_ID, CharacterProfile, CharacterRegistry
from app.config.yaml_config import load_yaml_mapping, save_yaml_mapping
from app.config.defaults import (
    DEFAULT_BASE_URL,
    DEFAULT_PROFILE_ALIAS,
    DEFAULT_PROFILE_ID,
    DEFAULT_TEXT_MODEL,
    DEFAULT_VISION_MODEL,
)
from app.config.model_slots import normalize_provider_models
from app.config.models import (
    MODEL_SLOT_CHAT,
    MODEL_SLOT_MEMORY_CURATION,
    MODEL_SLOT_THEME_AI,
    MODEL_SLOT_VISION_CHAT,
    MODEL_SLOT_VISUAL_CONTEXT,
    ApiConfigProfile,
    ModelSelectionSettings,
    ModelSlotSelection,
)
from app.llm.api_client import ApiSettings
from app.storage.paths import StoragePaths
from app.ui.theme import ThemeSettings, theme_from_mapping, theme_to_mapping
from app.agent.screen_awareness import (
    SCREEN_AWARENESS_DEFAULT_CHECK_INTERVAL_MINUTES,
    SCREEN_AWARENESS_DEFAULT_COOLDOWN_MINUTES,
    SCREEN_AWARENESS_DEFAULT_SCREEN_CONTEXT_BATCH_LIMIT,
    ScreenAwarenessSettings,
)
from app.sensory.settings import SensorySettings, sensory_settings_from_config
from app.voice.tts_settings import (
    DEFAULT_GENIE_TTS_API_URL,
    DEFAULT_GPT_SOVITS_API_URL,
    TTS_PROVIDER_CUSTOM_GPT_SOVITS,
    TTS_PROVIDER_GENIE,
    TTS_PROVIDER_GPT_SOVITS,
    TTS_PROVIDER_NONE,
    GPTSoVITSTTSSettings,
)


API_CONFIG_FILE = "api.yaml"
CHARACTERS_CONFIG_FILE = "characters.yaml"
SYSTEM_CONFIG_FILE = "system_config.yaml"


@dataclass(frozen=True)
class DebugLogSettings:
    """调试日志配置。"""

    enabled: bool = False
    body_enabled: bool = False
    file_enabled: bool = False
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
    """集中管理运行配置；唯一持久化来源是 data/config/*.yaml。"""

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
            base_url=str(data.get("base_url", "https://api.openai.com/v1")).strip().rstrip("/"),
            api_key=str(data.get("api_key", "")).strip(),
            model=str(data.get("model", "gpt-4.1-mini")).strip(),
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
        # 仅写入用户显式配置的高级参数，避免给老配置塞入空键、改变默认行为。
        if settings.temperature is not None:
            llm_data["temperature"] = float(settings.temperature)
        if settings.top_p is not None:
            llm_data["top_p"] = float(settings.top_p)
        if settings.max_tokens is not None:
            llm_data["max_tokens"] = int(settings.max_tokens)
        data["llm"] = llm_data
        save_yaml_mapping(self.api_config_path, data)

    def load_api_profiles(self) -> list[ApiConfigProfile]:
        """从 api.yaml 读取 api_profiles 列表；自动迁移旧格式。"""
        data = load_yaml_mapping(self.api_config_path)
        raw_profiles = data.get("api_profiles")
        if isinstance(raw_profiles, list) and raw_profiles:
            legacy_models = _legacy_model_names(data)
            profiles: list[ApiConfigProfile] = []
            for raw in raw_profiles:
                if not isinstance(raw, dict):
                    continue
                models = normalize_provider_models(raw.get("models"))
                if not models:
                    models = tuple(legacy_models)
                profiles.append(
                    ApiConfigProfile(
                        id=str(raw.get("id", "")).strip(),
                        alias=str(raw.get("alias", "")).strip(),
                        base_url=str(raw.get("base_url", DEFAULT_BASE_URL)).strip().rstrip("/"),
                        api_key=str(raw.get("api_key", "")).strip(),
                        models=models,
                    )
                )
            if any("models" not in raw for raw in raw_profiles if isinstance(raw, dict)):
                self.save_api_profiles(profiles)
            return profiles

        # 旧格式迁移：从 llm 键读取单条配置
        llm = _mapping(data.get("llm"))
        old_model = str(llm.get("model", "")).strip()
        if llm.get("base_url"):
            profile = ApiConfigProfile(
                id=DEFAULT_PROFILE_ID,
                alias=DEFAULT_PROFILE_ALIAS,
                base_url=str(llm.get("base_url", DEFAULT_BASE_URL)).strip().rstrip("/"),
                api_key=str(llm.get("api_key", "")).strip(),
                models=tuple(_dedupe([old_model or DEFAULT_TEXT_MODEL])),
            )
            # 写入新格式
            self.save_api_profiles([profile])
            # 设置 model_selection 指向迁移后的默认配置
            self.save_model_selection(
                ModelSelectionSettings(
                    chat=ModelSlotSelection(
                        profile_id=DEFAULT_PROFILE_ID,
                        model=old_model or DEFAULT_TEXT_MODEL,
                    ),
                )
            )
            return [profile]
        return []

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

    def load_global_model_names(self) -> list[str]:
        data = load_yaml_mapping(self.api_config_path)
        profiles = data.get("api_profiles")
        if isinstance(profiles, list):
            names: list[str] = []
            for raw in profiles:
                if isinstance(raw, dict):
                    names.extend(normalize_provider_models(raw.get("models")))
            if names:
                return _dedupe(names)
        raw = data.get("model_names")
        if isinstance(raw, list):
            return [str(m).strip() for m in raw if isinstance(m, str) and m.strip()]
        return []

    def save_global_model_names(self, names: list[str]) -> None:
        data = load_yaml_mapping(self.api_config_path)
        data["model_names"] = _dedupe(names)
        save_yaml_mapping(self.api_config_path, data)

    def load_model_selection(self) -> ModelSelectionSettings:
        data = load_yaml_mapping(self.api_config_path)
        raw_slots = data.get("model_slots")
        if isinstance(raw_slots, dict):
            return ModelSelectionSettings(
                chat=_slot_selection(raw_slots.get(MODEL_SLOT_CHAT)),
                vision_chat=_optional_slot_selection(raw_slots.get(MODEL_SLOT_VISION_CHAT)),
                visual_context=_optional_slot_selection(raw_slots.get(MODEL_SLOT_VISUAL_CONTEXT)),
                memory_curation=_optional_slot_selection(raw_slots.get(MODEL_SLOT_MEMORY_CURATION)),
                theme_ai=_optional_slot_selection(raw_slots.get(MODEL_SLOT_THEME_AI)),
            )

        # #110 旧格式迁移：视觉/文本模型两槽位。
        if any(
            key in data
            for key in (
                "vision_profile_id",
                "vision_model",
                "text_enabled",
                "text_profile_id",
                "text_model",
            )
        ):
            text_enabled = _bool_value(data.get("text_enabled"), True)
            if text_enabled:
                settings = ModelSelectionSettings(
                    chat=ModelSlotSelection(
                        profile_id=str(data.get("text_profile_id", "")).strip(),
                        model=str(data.get("text_model", DEFAULT_TEXT_MODEL)).strip(),
                    ),
                    vision_chat=ModelSlotSelection(
                        profile_id=str(data.get("vision_profile_id", "")).strip(),
                        model=str(data.get("vision_model", DEFAULT_VISION_MODEL)).strip(),
                    ),
                )
            else:
                settings = ModelSelectionSettings(
                    chat=ModelSlotSelection(
                        profile_id=str(data.get("vision_profile_id", "")).strip(),
                        model=str(data.get("vision_model", DEFAULT_VISION_MODEL)).strip(),
                    ),
                )
            self.save_model_selection(settings)
            return settings

        llm = _mapping(data.get("llm"))
        old_model = str(llm.get("model", "")).strip()
        return ModelSelectionSettings(
            chat=ModelSlotSelection(
                profile_id=DEFAULT_PROFILE_ID if llm.get("base_url") else "",
                model=old_model or DEFAULT_TEXT_MODEL,
            ),
        )

    def save_model_selection(self, settings: ModelSelectionSettings) -> None:
        data = load_yaml_mapping(self.api_config_path)
        slots: dict[str, dict[str, str]] = {}
        for slot in (
            MODEL_SLOT_CHAT,
            MODEL_SLOT_VISION_CHAT,
            MODEL_SLOT_VISUAL_CONTEXT,
            MODEL_SLOT_MEMORY_CURATION,
            MODEL_SLOT_THEME_AI,
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
        data["model_slots"] = slots
        save_yaml_mapping(self.api_config_path, data)

    def load_tts_settings(
        self,
        *,
        validate_enabled: bool = True,
        character_profile: CharacterProfile | None = None,
    ) -> GPTSoVITSTTSSettings:
        data = self._api_section("tts")
        playback_backend = str(data.get("playback_backend", "")).strip()
        gpt_sovits = _mapping(data.get("gpt_sovits"))
        genie_tts = _mapping(data.get("genie_tts"))
        provider = str(data.get("provider", "")).strip().lower()
        enabled = _bool_value(data.get("enabled"), False)
        if provider in {"none", "off", "disabled", "不使用"}:
            enabled = False
            provider = TTS_PROVIDER_NONE
        elif provider in {"gpt-sovits", "gpt_sovits", "gptsovits"}:
            enabled = True
            provider = TTS_PROVIDER_GPT_SOVITS
        elif provider in {
            "custom-gpt-sovits",
            "custom_gpt_sovits",
            "custom-sovits",
            "custom_sovits",
            "external-gpt-sovits",
            "external_gpt_sovits",
            "external-sovits",
            "external_sovits",
        }:
            enabled = True
            provider = TTS_PROVIDER_CUSTOM_GPT_SOVITS
        elif provider in {"genie", "genie-tts", "genie_tts"}:
            enabled = True
            provider = TTS_PROVIDER_GENIE
        else:
            provider = TTS_PROVIDER_GPT_SOVITS if enabled else TTS_PROVIDER_NONE

        # 无语音角色不能启用 TTS，启动和设置页加载时直接降级为关闭。
        if enabled and character_profile is not None and character_profile.voice is None:
            enabled = False

        provider_data = genie_tts if provider == TTS_PROVIDER_GENIE else gpt_sovits
        default_api_url = DEFAULT_GENIE_TTS_API_URL if provider == TTS_PROVIDER_GENIE else DEFAULT_GPT_SOVITS_API_URL
        api_url = str(provider_data.get("api_url", default_api_url)).strip()
        work_dir = _optional_path(provider_data.get("work_dir"), self.base_dir)
        python_path = _optional_path(provider_data.get("python_path"), self.base_dir)
        tts_config_path = _optional_path(provider_data.get("tts_config_path"), self.base_dir)
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
                validate_enabled=validate_enabled,
            )
            if playback_backend:
                settings = replace(settings, playback_backend=playback_backend)
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
            )
            if playback_backend:
                settings = replace(settings, playback_backend=playback_backend)
        if settings.enabled and validate_enabled:
            settings.validate()
        return settings

    def save_tts_settings(self, settings: GPTSoVITSTTSSettings) -> None:
        data = load_yaml_mapping(self.api_config_path)
        saved_provider = settings.provider if settings.enabled else TTS_PROVIDER_NONE
        section_provider = (
            settings.provider
            if settings.provider in {TTS_PROVIDER_GENIE, TTS_PROVIDER_GPT_SOVITS}
            else TTS_PROVIDER_GPT_SOVITS
        )
        tts_data: dict[str, object] = {
            "provider": saved_provider,
            "enabled": bool(settings.enabled),
        }
        if section_provider == TTS_PROVIDER_GENIE:
            tts_data["genie_tts"] = {
                "api_url": settings.api_url.strip() or DEFAULT_GENIE_TTS_API_URL,
                "work_dir": _path_for_config(settings.work_dir, self.base_dir),
                "onnx_model_dir": _path_for_config(settings.onnx_model_dir, self.base_dir),
                "ref_lang": settings.ref_lang.strip(),
                "text_lang": settings.text_lang.strip(),
                "timeout_seconds": int(settings.timeout_seconds),
            }
        elif section_provider == TTS_PROVIDER_GPT_SOVITS:
            tts_data["gpt_sovits"] = {
                "api_url": settings.api_url.strip(),
                "work_dir": _path_for_config(settings.work_dir, self.base_dir),
                "python_path": _path_for_config(settings.python_path, self.base_dir),
                "tts_config_path": _path_for_config(settings.tts_config_path, self.base_dir),
                "ref_lang": settings.ref_lang.strip(),
                "text_lang": settings.text_lang.strip(),
                "timeout_seconds": int(settings.timeout_seconds),
            }
        data["tts"] = tts_data
        save_yaml_mapping(self.api_config_path, data)

    def load_mcp_runtime_settings(self) -> MCPRuntimeSettings:
        mcp = self._system_section("mcp")
        return normalize_mcp_runtime_settings(
            MCPRuntimeSettings(
                windows_enabled=_bool_value(
                    mcp.get("windows_enabled"),
                    False,
                )
            )
        )

    def save_mcp_runtime_settings(self, settings: MCPRuntimeSettings) -> None:
        normalized_settings = normalize_mcp_runtime_settings(settings)
        self.save_system_values(
            "mcp",
            {"windows_enabled": bool(normalized_settings.windows_enabled)},
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
        return DebugLogSettings(
            enabled=_bool_value(debug.get("enabled"), False),
            body_enabled=_bool_value(debug.get("body_enabled"), False),
            file_enabled=_bool_value(debug.get("file_enabled"), False),
            stage_debug_overlay=_bool_value(debug.get("stage_debug_overlay"), False),
            stage_collision_mask=_bool_value(debug.get("stage_collision_mask"), True),
        )

    def save_debug_log_settings(self, settings: DebugLogSettings) -> None:
        self.save_system_values(
            "debug",
            {
                "enabled": bool(settings.enabled),
                "body_enabled": bool(settings.body_enabled),
                "file_enabled": bool(settings.file_enabled),
                "stage_debug_overlay": bool(settings.stage_debug_overlay),
                "stage_collision_mask": bool(settings.stage_collision_mask),
            },
        )

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
        return theme_from_mapping(ui.get("theme"))

    def save_theme_settings(self, settings: ThemeSettings) -> None:
        ui = self._system_section("ui")
        ui["theme"] = theme_to_mapping(settings)
        data = load_yaml_mapping(self.system_config_path)
        data["ui"] = ui
        save_yaml_mapping(self.system_config_path, data)

    def load_screen_awareness_settings(self) -> ScreenAwarenessSettings:
        screen_awareness = self._system_section("screen_awareness")
        if not screen_awareness:
            screen_awareness = self._system_section("proactive_care")
        return ScreenAwarenessSettings(
            enabled=_bool_value(screen_awareness.get("enabled"), True),
            screen_context_enabled=_bool_value(
                screen_awareness.get("screen_context_enabled"),
                True,
            ),
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
        )

    def save_screen_awareness_settings(self, settings: ScreenAwarenessSettings) -> None:
        normalized = settings.normalized()
        data = load_yaml_mapping(self.system_config_path)
        data["screen_awareness"] = {
            "enabled": bool(normalized.enabled),
            "screen_context_enabled": bool(normalized.screen_context_enabled),
            "check_interval_minutes": int(normalized.check_interval_minutes),
            "cooldown_minutes": int(normalized.cooldown_minutes),
            "screen_context_batch_limit": int(normalized.screen_context_batch_limit),
        }
        save_yaml_mapping(self.system_config_path, data)

    def load_sensory_settings(self) -> SensorySettings:
        return sensory_settings_from_config(
            self._system_section("sensory"),
            self._api_section("sensory"),
        )

    def save_sensory_settings(self, settings: SensorySettings) -> None:
        normalized = settings.normalized()

        system_data = load_yaml_mapping(self.system_config_path)
        system_data["sensory"] = normalized.to_system_mapping()
        save_yaml_mapping(self.system_config_path, system_data)

        api_data = load_yaml_mapping(self.api_config_path)
        sensory_api = _mapping(api_data.get("sensory"))
        sensory_api.update(normalized.to_api_mapping())
        api_data["sensory"] = sensory_api
        save_yaml_mapping(self.api_config_path, api_data)

    def load_proactive_care_settings(self) -> ScreenAwarenessSettings:
        """兼容旧调用点；新代码请使用 load_screen_awareness_settings。"""
        return self.load_screen_awareness_settings()

    def save_proactive_care_settings(self, settings: ScreenAwarenessSettings) -> None:
        """兼容旧调用点；新代码请使用 save_screen_awareness_settings。"""
        self.save_screen_awareness_settings(settings)

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
        data = load_yaml_mapping(self.system_config_path)
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
        data = load_yaml_mapping(self.system_config_path)
        data["backchannel"] = {
            "enabled": bool(normalized.enabled),
            "mode": normalized.mode,
            "delay_ms": int(normalized.delay_ms),
            "probability": float(normalized.probability),
            "tts_enabled": bool(normalized.tts_enabled),
            "timeout_ms": int(normalized.timeout_ms),
        }
        save_yaml_mapping(self.system_config_path, data)

    def load_memory_curation_settings(self):
        from app.agent.memory_curator import MemoryCurationSettings

        memory = self._system_section("memory_curation")
        return MemoryCurationSettings(
            enabled=_bool_value(memory.get("enabled"), True),
            trigger_turns=_int_value(memory.get("trigger_turns"), 8),
            backfill_limit=_int_value(memory.get("backfill_limit"), 200),
        )

    def save_memory_curation_settings(self, settings) -> None:
        # 仅写入 memory_curation section 的三个字段；backfill_limit 不在 UI 暴露，
        # 但持久化时一并保留，避免被默认值覆盖。
        self.save_system_values(
            "memory_curation",
            {
                "enabled": bool(settings.enabled),
                "trigger_turns": int(settings.trigger_turns),
                "backfill_limit": int(settings.backfill_limit),
            },
        )

    def load_current_character_id(self, character_registry: CharacterRegistry) -> str:
        data = load_yaml_mapping(self.characters_config_path)
        configured = str(data.get("current_character_id", "")).strip()
        if configured in character_registry.profiles:
            return configured
        if DEFAULT_CHARACTER_ID in character_registry.profiles:
            return DEFAULT_CHARACTER_ID
        if character_registry.profiles:
            return next(iter(character_registry.profiles))
        raise ValueError("未找到任何角色包。")

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
        data = load_yaml_mapping(self.system_config_path)
        current = _mapping(data.get(section))
        current.update(values)
        data[section] = current
        save_yaml_mapping(self.system_config_path, data)

    def _api_section(self, name: str) -> dict[str, Any]:
        return _mapping(load_yaml_mapping(self.api_config_path).get(name))

    def _system_section(self, name: str) -> dict[str, Any]:
        return _mapping(load_yaml_mapping(self.system_config_path).get(name))


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _slot_selection(raw: object) -> ModelSlotSelection:
    if not isinstance(raw, dict):
        return ModelSlotSelection()
    return ModelSlotSelection(
        profile_id=str(raw.get("profile_id", "")).strip(),
        model=str(raw.get("model", "")).strip(),
    )


def _optional_slot_selection(raw: object) -> ModelSlotSelection | None:
    selection = _slot_selection(raw)
    return selection if selection.configured else None


def _legacy_model_names(data: dict[str, Any]) -> list[str]:
    names: list[str] = []
    raw_names = data.get("model_names")
    if isinstance(raw_names, list):
        names.extend(str(item).strip() for item in raw_names if isinstance(item, str))
    for key in ("vision_model", "text_model"):
        names.append(str(data.get(key, "")).strip())
    llm = _mapping(data.get("llm"))
    names.append(str(llm.get("model", "")).strip())
    return _dedupe(names)


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
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled"}:
        return False
    return default
