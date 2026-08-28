from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from app.agent.mcp.settings import MCPRuntimeSettings
from app.agent.runtime_limits import RuntimeLoopSettings
from app.config.character_loader import CharacterRegistry
from app.config.settings_service import (
    AppSettingsService,
    BubbleSettings,
    DebugLogSettings,
    StartupSettings,
)
from app.config.model_slots import resolve_model_slot
from app.config.models import (
    DEFAULT_THEME_SETTINGS,
    THEME_COLOR_FIELDS,
    MODEL_SLOT_CHAT,
    MODEL_SLOT_VISION_CHAT,
    ModelSelectionSettings,
    ModelSlotSelection,
    ThemeSettings,
)
from app.config.yaml_config import load_yaml_mapping
from app.llm.api_client import ApiSettings
from app.agent.screen_awareness import ScreenAwarenessSettings
from app.voice.tts_settings import GPTSoVITSTTSSettings


class CharacterRegistryStub:
    profiles = {"sakura": object(), "nanami": object()}

    def get(self, character_id: str) -> object:
        if character_id not in self.profiles:
            raise KeyError(character_id)
        return self.profiles[character_id]


def test_settings_service_keeps_missing_api_config_empty() -> None:
    root = _runtime_root("empty_api")
    service = AppSettingsService(root)

    assert service.load_api_settings() == ApiSettings("", "", "")
    assert service.load_api_profiles() == []
    assert service.load_model_selection() == ModelSelectionSettings()


@pytest.mark.parametrize("content", ["debug: {}\n", "config_version: 2\n"])
def test_settings_service_rejects_non_v1_system_config(content: str) -> None:
    root = _runtime_root(f"system_schema_{uuid.uuid4().hex}")
    service = AppSettingsService(root)
    service.system_config_path.parent.mkdir(parents=True)
    service.system_config_path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match="版本不受支持"):
        service.load_startup_settings()


def test_settings_service_loads_yaml_api_config() -> None:
    root = _runtime_root("yaml_api")
    service = AppSettingsService(root)
    service.api_config_path.parent.mkdir(parents=True)
    service.api_config_path.write_text(
        """
llm:
  base_url: https://yaml.example/v1
  api_key: yaml-key
  model: yaml-model
  timeout_seconds: 12
""".lstrip(),
        encoding="utf-8",
    )

    settings = service.load_api_settings()

    assert settings == ApiSettings(
        base_url="https://yaml.example/v1",
        api_key="yaml-key",
        model="yaml-model",
        timeout_seconds=12,
    )


def test_api_profiles_do_not_fall_back_to_llm_config() -> None:
    root = _runtime_root("model_slots_llm_only")
    service = AppSettingsService(root)
    service.api_config_path.parent.mkdir(parents=True)
    service.api_config_path.write_text(
        """
llm:
  base_url: https://old.example/v1
  api_key: old-key
  model: old-chat
  timeout_seconds: 20
""".lstrip(),
        encoding="utf-8",
    )

    assert service.load_api_profiles() == []
    assert service.load_model_selection() == ModelSelectionSettings()
    assert "api_profiles" not in load_yaml_mapping(service.api_config_path)
    assert "model_slots" not in load_yaml_mapping(service.api_config_path)


def test_api_profiles_reject_old_string_model_entries() -> None:
    root = _runtime_root("provider_string_models")
    service = AppSettingsService(root)
    service.api_config_path.parent.mkdir(parents=True)
    service.api_config_path.write_text(
        """
api_profiles:
  - id: p1
    alias: Provider
    base_url: https://api.example/v1
    api_key: key
    models:
      - old-string-model
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Provider 模型列表"):
        service.load_api_profiles()


def test_api_model_slots_do_not_fall_back_to_old_root_fields() -> None:
    root = _runtime_root("model_slots_old_root_fields")
    service = AppSettingsService(root)
    service.api_config_path.parent.mkdir(parents=True)
    service.api_config_path.write_text(
        """
api_profiles:
  - id: p1
    alias: 主供应商
    base_url: https://api.example/v1
    api_key: key
    models:
      - name: text-model
      - name: vision-model
text_enabled: true
text_profile_id: p1
text_model: text-model
vision_profile_id: p1
vision_model: vision-model
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="已废止"):
        service.load_api_profiles()
    with pytest.raises(ValueError, match="已废止"):
        service.load_model_selection()
    assert "model_slots" not in load_yaml_mapping(service.api_config_path)


def test_api_model_slots_reject_old_list_shape() -> None:
    root = _runtime_root("model_slots_old_list_shape")
    service = AppSettingsService(root)
    service.api_config_path.parent.mkdir(parents=True)
    service.api_config_path.write_text(
        """
model_slots:
  - slot_id: chat
    selection:
      profile_id: p1
      model: chat-model
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="模型槽"):
        service.load_model_selection()


def test_model_slot_resolver_uses_configured_fallbacks() -> None:
    root = _runtime_root("model_slot_resolver")
    service = AppSettingsService(root)
    service.api_config_path.parent.mkdir(parents=True)
    service.api_config_path.write_text(
        """
llm:
  base_url: https://api.example/v1
  api_key: key
  model: chat-model
api_profiles:
  - id: p1
    alias: 主供应商
    base_url: https://api.example/v1
    api_key: key
    models:
      - name: chat-model
      - name: vision-model
model_slots:
  chat:
    profile_id: p1
    model: chat-model
  vision_chat:
    profile_id: p1
    model: vision-model
  visual_context:
    profile_id: p1
    model: visual-model
  theme_ai:
    profile_id: p1
    model: visual-model
""".lstrip(),
        encoding="utf-8",
    )
    profiles = service.load_api_profiles()
    selection = service.load_model_selection()
    base = service.load_api_settings()

    assert resolve_model_slot(profiles, selection, MODEL_SLOT_CHAT, base).settings.model == "chat-model"  # type: ignore[union-attr]
    assert resolve_model_slot(profiles, selection, MODEL_SLOT_VISION_CHAT, base).settings.model == "vision-model"  # type: ignore[union-attr]
    service.save_model_selection(selection)
    assert set(load_yaml_mapping(service.api_config_path)["model_slots"]) == {
        MODEL_SLOT_CHAT,
        MODEL_SLOT_VISION_CHAT,
    }
    bad_selection = ModelSelectionSettings(
        chat=ModelSlotSelection(profile_id="p1", model="missing-model"),
    )
    assert resolve_model_slot(profiles, bad_selection, MODEL_SLOT_CHAT, base) is None


def test_settings_service_saves_runtime_config_to_yaml() -> None:
    root = _runtime_root("yaml_save")
    service = AppSettingsService(root)

    service.save_api_settings(
        ApiSettings(
            base_url="https://api.example/v1",
            api_key="secret",
            model="demo-model",
            timeout_seconds=30,
        )
    )
    service.save_tts_settings(
        GPTSoVITSTTSSettings(
            enabled=True,
            api_url="http://127.0.0.1:9880/tts",
            ref_audio_path=root / "ref.wav",
            ref_text_path=root / "ref.txt",
            ref_text="hello",
            work_dir=root / "tts" / "gpt",
            ref_lang="ja",
            text_lang="ja",
            timeout_seconds=22,
        )
    )
    service.save_current_character_id(CharacterRegistryStub(), "nanami")  # type: ignore[arg-type]
    service.save_mcp_runtime_settings(MCPRuntimeSettings(desktop_enabled=True))
    service.save_debug_log_settings(
        DebugLogSettings(
            enabled=True,
            body_enabled=True,
            file_enabled=True,
            profile="debug",
        )
    )
    service.save_startup_settings(StartupSettings(launch_at_login=True))
    service.save_screen_awareness_settings(
        ScreenAwarenessSettings(
            enabled=True,
            screen_context_enabled=True,
            check_interval_minutes=5,
            cooldown_minutes=7,
            screen_context_batch_limit=3,
            screen_context_resolution="720p",
        )
    )

    api = load_yaml_mapping(service.api_config_path)
    characters = load_yaml_mapping(service.characters_config_path)
    system = load_yaml_mapping(service.system_config_path)

    assert api["llm"]["model"] == "demo-model"
    assert api["tts"]["provider"] == "gpt-sovits"
    assert api["tts"]["gpt_sovits"]["managed_runtime"]["work_dir"] == "tts/gpt"
    assert api["tts"]["gpt_sovits"]["timeout_seconds"] == 22
    assert characters["current_character_id"] == "nanami"
    assert system["mcp"]["desktop_enabled"] is True
    assert system["debug"]["enabled"] is True
    assert system["debug"]["body_enabled"] is True
    assert system["debug"]["file_enabled"] is True
    assert system["debug"]["profile"] == "debug"
    assert "raw_tts_service_enabled" not in system["debug"]
    assert system["startup"]["launch_at_login"] is True
    assert system["screen_awareness"]["check_interval_minutes"] == 5
    assert system["screen_awareness"]["screen_context_resolution"] == "720p"


def test_settings_service_loads_and_saves_startup_settings() -> None:
    root = _runtime_root("yaml_startup")
    service = AppSettingsService(root)

    assert service.load_startup_settings() == StartupSettings(launch_at_login=False)

    service.save_startup_settings(StartupSettings(launch_at_login=True))

    assert service.load_startup_settings() == StartupSettings(launch_at_login=True)
    system = load_yaml_mapping(service.system_config_path)
    assert system["startup"]["launch_at_login"] is True


def test_screen_awareness_loader_does_not_fall_back_to_proactive_care() -> None:
    service = AppSettingsService(_runtime_root("screen_awareness_no_legacy_fallback"))
    service.save_system_values(
        "proactive_care",
        {"enabled": False, "check_interval_minutes": 99},
    )

    assert service.load_screen_awareness_settings() == ScreenAwarenessSettings()


def test_settings_service_exposes_only_screen_awareness_methods() -> None:
    assert not hasattr(AppSettingsService, "load_proactive_care_settings")
    assert not hasattr(AppSettingsService, "save_proactive_care_settings")


def test_settings_service_loads_and_saves_bubble_settings() -> None:
    root = _runtime_root("yaml_bubble")
    service = AppSettingsService(root)

    # 默认：开启、5 秒。
    assert service.load_bubble_settings() == BubbleSettings(
        auto_hide_enabled=True,
        auto_hide_delay_seconds=5,
    )

    # 超出上限的时长应被 normalized 钳制到 120 秒。
    service.save_bubble_settings(
        BubbleSettings(auto_hide_enabled=False, auto_hide_delay_seconds=999)
    )

    loaded = service.load_bubble_settings()
    assert loaded.auto_hide_enabled is False
    assert loaded.auto_hide_delay_seconds == 120
    system = load_yaml_mapping(service.system_config_path)
    assert system["ui"]["bubble_auto_hide_enabled"] is False
    assert system["ui"]["bubble_auto_hide_delay_seconds"] == 120


def test_save_bubble_settings_preserves_other_ui_keys() -> None:
    root = _runtime_root("yaml_bubble_preserve")
    service = AppSettingsService(root)
    service.save_system_values("ui", {"subtitle_language": "ja"})

    service.save_bubble_settings(
        BubbleSettings(auto_hide_enabled=True, auto_hide_delay_seconds=8)
    )

    system = load_yaml_mapping(service.system_config_path)
    # 写气泡配置时用读-改-写，原有 ui 键不应丢失。
    assert system["ui"]["subtitle_language"] == "ja"
    assert system["ui"]["bubble_auto_hide_delay_seconds"] == 8


def test_settings_service_loads_and_saves_runtime_loop_settings() -> None:
    root = _runtime_root("yaml_runtime_loop")
    service = AppSettingsService(root)

    assert service.load_runtime_loop_settings() == RuntimeLoopSettings()

    service.save_runtime_loop_settings(
        RuntimeLoopSettings(
            max_agent_steps_per_turn=20,
            max_tool_calls_per_step=6,
            max_tool_calls_per_turn=4,
        )
    )

    loaded = service.load_runtime_loop_settings()
    assert loaded.max_agent_steps_per_turn == 12
    assert loaded.max_tool_calls_per_step == 6
    assert loaded.max_tool_calls_per_turn == 6
    system = load_yaml_mapping(service.system_config_path)
    assert system["tool_loop"]["max_agent_steps_per_turn"] == 12
    assert system["tool_loop"]["max_tool_calls_per_step"] == 6
    assert system["tool_loop"]["max_tool_calls_per_turn"] == 6


def test_settings_service_loads_current_tts_managed_runtime() -> None:
    root = _runtime_root("yaml_tts_work_dir")
    service = AppSettingsService(root)
    service.api_config_path.parent.mkdir(parents=True)
    service.api_config_path.write_text(
        """
tts:
  provider: gpt-sovits
  enabled: true
  gpt_sovits:
    managed_runtime:
      work_dir: data/tts_bundles/installed/gpt_sovits_v2pro
    ref_lang: ja
    text_lang: ja
""".lstrip(),
        encoding="utf-8",
    )

    settings = service.load_tts_settings(validate_enabled=False)

    assert settings.work_dir == root / "data" / "tts_bundles" / "installed" / "gpt_sovits_v2pro"
    assert settings.python_path is None
    assert settings.tts_config_path is None

    assert settings.api_url == "http://127.0.0.1:9880/tts"


@pytest.mark.parametrize("provider", ["gpt_sovits", "custom-gpt-sovits", "genie", "off"])
def test_settings_service_rejects_old_tts_provider_aliases(provider: str) -> None:
    root = _runtime_root(f"yaml_tts_alias_{provider}")
    service = AppSettingsService(root)
    service.api_config_path.parent.mkdir(parents=True)
    service.api_config_path.write_text(
        f"tts:\n  provider: {provider}\n  enabled: false\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="TTS Provider"):
        service.load_tts_settings(validate_enabled=False)


def test_settings_service_rejects_old_flat_gpt_sovits_fields() -> None:
    root = _runtime_root("yaml_tts_old_flat_fields")
    service = AppSettingsService(root)
    service.api_config_path.parent.mkdir(parents=True)
    service.api_config_path.write_text(
        """
tts:
  provider: gpt-sovits
  enabled: false
  gpt_sovits:
    api_url: http://127.0.0.1:9880/tts
    work_dir: tts/gpt
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="已废止"):
        service.load_tts_settings(validate_enabled=False)


def test_settings_service_disables_tts_for_voice_less_character() -> None:
    root = _runtime_root("yaml_tts_no_voice_character")
    service = AppSettingsService(root)
    service.api_config_path.parent.mkdir(parents=True)
    service.api_config_path.write_text(
        """
tts:
  provider: gpt-sovits
  enabled: true
  gpt_sovits:
    ref_lang: ja
    text_lang: ja
""".lstrip(),
        encoding="utf-8",
    )
    character_dir = root / "characters" / "demo"
    character_dir.mkdir(parents=True)
    (character_dir / "card.md").write_text("system prompt", encoding="utf-8")
    (character_dir / "portrait.png").write_bytes(b"portrait")
    (character_dir / "character.json").write_text(
        json.dumps(
            {
                "id": "demo",
                "display_name": "Demo",
                "initial_message": "hello",
                "card": "card.md",
                "portrait": {"default": "portrait.png"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    profile = CharacterRegistry(root).get("demo")

    settings = service.load_tts_settings(character_profile=profile)

    assert not settings.enabled
    assert settings.provider == "gpt-sovits"
    assert settings.character_name == "Demo"


def test_settings_service_saves_and_loads_genie_tts_settings() -> None:
    root = _runtime_root("yaml_genie_tts")
    service = AppSettingsService(root)
    settings = GPTSoVITSTTSSettings(
        enabled=True,
        provider="genie-tts",
        api_url="http://127.0.0.1:9881/",
        ref_audio_path=root / "ref.wav",
        ref_text_path=root / "ref.txt",
        ref_text="hello",
        work_dir=root / "tts" / "cpu",
        character_name="夜乃桜",
        onnx_model_dir=root / "data" / "tts_bundles" / "onnx" / "sakura",
        ref_lang="ja",
        text_lang="ja",
        timeout_seconds=33,
    )

    service.save_tts_settings(settings)
    saved = load_yaml_mapping(service.api_config_path)
    loaded = service.load_tts_settings(validate_enabled=False)

    assert saved["tts"]["provider"] == "genie-tts"
    assert saved["tts"]["genie_tts"]["api_url"] == "http://127.0.0.1:9881/"
    assert saved["tts"]["genie_tts"]["work_dir"] == "tts/cpu"
    assert saved["tts"]["genie_tts"]["onnx_model_dir"] == "data/tts_bundles/onnx/sakura"
    assert loaded.provider == "genie-tts"
    assert loaded.work_dir == root / "tts" / "cpu"
    assert loaded.onnx_model_dir == root / "data" / "tts_bundles" / "onnx" / "sakura"
    assert loaded.timeout_seconds == 33


def test_settings_service_preserves_inactive_tts_provider_configuration() -> None:
    root = _runtime_root("yaml_tts_provider_switch")
    service = AppSettingsService(root)
    service.api_config_path.parent.mkdir(parents=True, exist_ok=True)
    service.api_config_path.write_text(
        """
tts:
  provider: gpt-sovits
  enabled: true
  unknown_key: keep
  gpt_sovits:
    managed_runtime:
      work_dir: tts/gpt
  genie_tts:
    api_url: http://127.0.0.1:9881/
    work_dir: tts/old-genie
""".strip(),
        encoding="utf-8",
    )
    settings = GPTSoVITSTTSSettings(
        enabled=True,
        provider="genie-tts",
        api_url="http://127.0.0.1:9882/",
        ref_audio_path=root / "ref.wav",
        ref_text_path=root / "ref.txt",
        ref_text="hello",
        work_dir=root / "tts" / "new-genie",
        character_name="Sakura",
        onnx_model_dir=root / "onnx",
        timeout_seconds=30,
    )

    service.save_tts_settings(settings)
    saved = load_yaml_mapping(service.api_config_path)["tts"]

    assert saved["gpt_sovits"]["managed_runtime"]["work_dir"] == "tts/gpt"
    assert saved["genie_tts"]["work_dir"] == "tts/new-genie"
    assert saved["unknown_key"] == "keep"


def test_settings_service_saves_and_loads_custom_gpt_sovits_settings() -> None:
    root = _runtime_root("yaml_custom_gpt_sovits")
    service = AppSettingsService(root)
    settings = GPTSoVITSTTSSettings(
        enabled=True,
        provider="gpt-sovits",
        api_url="http://192.168.1.20:9880/tts",
        ref_audio_path=root / "ref.wav",
        ref_text_path=root / "ref.txt",
        ref_text="hello",
        work_dir=root / "external" / "GPT-SoVITS",
        python_path=root / "external" / "miniforge3" / "envs" / "gpt-sovits" / "bin" / "python",
        tts_config_path=root / "external" / "GPT-SoVITS" / "GPT_SoVITS" / "configs" / "tts_infer.yaml",
        ref_lang="ja",
        text_lang="ja",
        timeout_seconds=44,
        custom_base_url="http://192.168.1.20:9880",
    )

    service.save_tts_settings(settings)
    saved = load_yaml_mapping(service.api_config_path)
    loaded = service.load_tts_settings(validate_enabled=False)

    assert saved["tts"]["provider"] == "gpt-sovits"
    assert saved["tts"]["gpt_sovits"]["custom_base_url"] == "http://192.168.1.20:9880"
    assert saved["tts"]["gpt_sovits"]["tts_path"] == "/tts"
    runtime = saved["tts"]["gpt_sovits"]["managed_runtime"]
    assert runtime["work_dir"] == "external/GPT-SoVITS"
    assert runtime["python_path"] == "external/miniforge3/envs/gpt-sovits/bin/python"
    assert runtime["tts_config_path"] == "external/GPT-SoVITS/GPT_SoVITS/configs/tts_infer.yaml"
    assert loaded.provider == "gpt-sovits"
    assert loaded.custom_base_url == "http://192.168.1.20:9880"
    assert loaded.api_url == "http://192.168.1.20:9880/tts"
    assert loaded.work_dir == root / "external" / "GPT-SoVITS"
    assert loaded.python_path == root / "external" / "miniforge3" / "envs" / "gpt-sovits" / "bin" / "python"
    assert loaded.tts_config_path == root / "external" / "GPT-SoVITS" / "GPT_SoVITS" / "configs" / "tts_infer.yaml"
    assert loaded.timeout_seconds == 44

def test_settings_service_rejects_retired_debug_field() -> None:
    root = _runtime_root("yaml_debug")
    service = AppSettingsService(root)
    service.save_system_values(
        "debug",
        {
            "enabled": True,
            "body_enabled": False,
            "file_enabled": True,
            "profile": "trace",
            "raw_tts_service_enabled": True,
        },
    )

    with pytest.raises(ValueError, match="已废止"):
        service.load_debug_log_settings()


def test_settings_service_enables_console_log_when_setting_is_missing() -> None:
    service = AppSettingsService(_runtime_root("yaml_debug_default_enabled"))

    assert service.load_debug_log_settings().enabled is True


def test_settings_service_save_rejects_retired_debug_field() -> None:
    root = _runtime_root("yaml_debug_remove_raw")
    service = AppSettingsService(root)
    service.save_system_values("debug", {"raw_tts_service_enabled": False, "file_enabled": False})

    with pytest.raises(ValueError, match="巹止|已废止"):
        service.save_debug_log_settings(DebugLogSettings(file_enabled=True))


def test_settings_service_loads_debug_file_enabled_by_default() -> None:
    root = _runtime_root("yaml_debug_default")
    service = AppSettingsService(root)
    service.save_system_values("debug", {"enabled": True, "body_enabled": False})

    settings = service.load_debug_log_settings()

    assert settings == DebugLogSettings(enabled=True, body_enabled=False, file_enabled=True)


def test_settings_service_respects_explicit_debug_file_disabled() -> None:
    root = _runtime_root("yaml_debug_file_disabled")
    service = AppSettingsService(root)
    service.save_system_values("debug", {"enabled": True, "file_enabled": False})

    settings = service.load_debug_log_settings()

    assert settings.file_enabled is False


def test_settings_service_saves_user_theme_preferences_without_global_colors() -> None:
    root = _runtime_root("yaml_theme")
    service = AppSettingsService(root)
    settings = ThemeSettings(
        primary_color="#112233",
        primary_hover_color="#223344",
        accent_color="#445566",
        text_color="#070809",
        secondary_text_color="#111213",
        muted_text_color="#141516",
        page_background_color="#f1f2f3",
        panel_background_color="#e1e2e3",
        input_background_color="#ffffff",
        bubble_background_color="#d1d2d3",
        border_color="#c1c2c3",
        ai_enabled=True,
        visual_effect_mode="solid",
    )

    service.save_theme_settings(settings)
    loaded = service.load_theme_settings()
    system = load_yaml_mapping(service.system_config_path)

    assert loaded == ThemeSettings(ai_enabled=True, visual_effect_mode="solid")
    for field, _label, _default in THEME_COLOR_FIELDS:
        assert system["ui"]["theme"][field] == getattr(DEFAULT_THEME_SETTINGS, field)
    assert system["ui"]["theme"]["ai_enabled"] is True
    assert system["ui"]["theme"]["visual_effect_mode"] == "solid"


def test_settings_service_saves_and_deletes_character_theme_override() -> None:
    root = _runtime_root("yaml_character_theme_override")
    service = AppSettingsService(root)
    settings = ThemeSettings(primary_color="#112233", accent_color="#445566")

    service.save_character_theme_override("N.A.V.I", settings)

    loaded = service.load_character_theme_override("N.A.V.I")
    system = load_yaml_mapping(service.system_config_path)
    assert loaded is not None
    assert loaded.primary_color == "#112233"
    assert loaded.accent_color == "#445566"
    assert "visual_effect_mode" not in system["ui"]["character_theme_overrides"]["N.A.V.I"]

    service.delete_character_theme_override("N.A.V.I")

    assert service.load_character_theme_override("N.A.V.I") is None
    system = load_yaml_mapping(service.system_config_path)
    assert "character_theme_overrides" not in system.get("ui", {})


def test_settings_service_loads_default_theme_for_invalid_values() -> None:
    root = _runtime_root("yaml_theme_invalid")
    service = AppSettingsService(root)
    service.save_system_values(
        "ui",
        {
            "theme": {
                "primary_color": "bad",
                "primary_hover_color": "#123",
                "accent_color": "#123",
                "text_color": None,
                "secondary_text_color": "",
                "ai_enabled": "yes",
            }
        },
    )

    settings = service.load_theme_settings()

    assert settings == ThemeSettings(ai_enabled=True)


def _runtime_root(name: str) -> Path:
    root = Path(__file__).resolve().parents[2] / "temp" / "test_runtime" / uuid.uuid4().hex / name
    root.mkdir(parents=True, exist_ok=True)
    return root
