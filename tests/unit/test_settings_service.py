from __future__ import annotations

import json
import uuid
from pathlib import Path

from app.agent.mcp.settings import MCPRuntimeSettings
from app.config.character_loader import CharacterRegistry
from app.config.settings_service import (
    AppSettingsService,
    BubbleSettings,
    DebugLogSettings,
    StartupSettings,
)
from app.config.yaml_config import load_yaml_mapping
from app.llm.api_client import ApiSettings
from app.agent.screen_awareness import ScreenAwarenessSettings
from app.ui.theme import (
    DEFAULT_THEME_SETTINGS,
    DEFAULT_PET_WINDOW_STYLESHEET,
    THEME_COLOR_FIELDS,
    ThemeSettings,
    build_pet_window_stylesheet,
    parse_ai_theme_response,
)
from app.voice.tts_settings import TTS_PROVIDER_CUSTOM_GPT_SOVITS, TTS_PROVIDER_NONE, GPTSoVITSTTSSettings


class CharacterRegistryStub:
    profiles = {"sakura": object(), "nanami": object()}

    def get(self, character_id: str) -> object:
        if character_id not in self.profiles:
            raise KeyError(character_id)
        return self.profiles[character_id]


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
    service.save_mcp_runtime_settings(MCPRuntimeSettings(windows_enabled=True))
    service.save_debug_log_settings(DebugLogSettings(enabled=True, body_enabled=True, file_enabled=True))
    service.save_startup_settings(StartupSettings(launch_at_login=True))
    service.save_screen_awareness_settings(
        ScreenAwarenessSettings(
            enabled=True,
            screen_context_enabled=True,
            check_interval_minutes=5,
            cooldown_minutes=7,
            screen_context_batch_limit=3,
        )
    )

    api = load_yaml_mapping(service.api_config_path)
    characters = load_yaml_mapping(service.characters_config_path)
    system = load_yaml_mapping(service.system_config_path)

    assert api["llm"]["model"] == "demo-model"
    assert api["tts"]["provider"] == "gpt-sovits"
    assert api["tts"]["gpt_sovits"]["work_dir"] == "tts/gpt"
    assert api["tts"]["gpt_sovits"]["timeout_seconds"] == 22
    assert characters["current_character_id"] == "nanami"
    assert system["mcp"]["windows_enabled"] is True
    assert system["debug"]["enabled"] is True
    assert system["debug"]["body_enabled"] is True
    assert system["debug"]["file_enabled"] is True
    assert system["startup"]["launch_at_login"] is True
    assert system["screen_awareness"]["check_interval_minutes"] == 5


def test_settings_service_loads_and_saves_startup_settings() -> None:
    root = _runtime_root("yaml_startup")
    service = AppSettingsService(root)

    assert service.load_startup_settings() == StartupSettings(launch_at_login=False)

    service.save_startup_settings(StartupSettings(launch_at_login=True))

    assert service.load_startup_settings() == StartupSettings(launch_at_login=True)
    system = load_yaml_mapping(service.system_config_path)
    assert system["startup"]["launch_at_login"] is True


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


def test_settings_service_loads_tts_work_dir_and_keeps_legacy_blank() -> None:
    root = _runtime_root("yaml_tts_work_dir")
    service = AppSettingsService(root)
    service.api_config_path.parent.mkdir(parents=True)
    service.api_config_path.write_text(
        """
tts:
  provider: gpt-sovits
  enabled: true
  gpt_sovits:
    api_url: http://127.0.0.1:9880/tts
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

    service.api_config_path.write_text(
        """
tts:
  provider: gpt-sovits
  enabled: true
  gpt_sovits:
    api_url: http://127.0.0.1:9880/tts
""".lstrip(),
        encoding="utf-8",
    )

    legacy_settings = service.load_tts_settings(validate_enabled=False)

    assert legacy_settings.work_dir is None


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
    api_url: http://127.0.0.1:9880/tts
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
    assert settings.provider == TTS_PROVIDER_NONE
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


def test_settings_service_saves_and_loads_custom_gpt_sovits_settings() -> None:
    root = _runtime_root("yaml_custom_gpt_sovits")
    service = AppSettingsService(root)
    settings = GPTSoVITSTTSSettings(
        enabled=True,
        provider=TTS_PROVIDER_CUSTOM_GPT_SOVITS,
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
    )

    service.save_tts_settings(settings)
    saved = load_yaml_mapping(service.api_config_path)
    loaded = service.load_tts_settings(validate_enabled=False)

    assert saved["tts"]["provider"] == TTS_PROVIDER_CUSTOM_GPT_SOVITS
    assert saved["tts"]["gpt_sovits"]["api_url"] == "http://192.168.1.20:9880/tts"
    assert saved["tts"]["gpt_sovits"]["work_dir"] == "external/GPT-SoVITS"
    assert saved["tts"]["gpt_sovits"]["python_path"] == "external/miniforge3/envs/gpt-sovits/bin/python"
    assert saved["tts"]["gpt_sovits"]["tts_config_path"] == "external/GPT-SoVITS/GPT_SoVITS/configs/tts_infer.yaml"
    assert loaded.provider == TTS_PROVIDER_CUSTOM_GPT_SOVITS
    assert loaded.api_url == "http://192.168.1.20:9880/tts"
    assert loaded.work_dir == root / "external" / "GPT-SoVITS"
    assert loaded.python_path == root / "external" / "miniforge3" / "envs" / "gpt-sovits" / "bin" / "python"
    assert loaded.tts_config_path == root / "external" / "GPT-SoVITS" / "GPT_SoVITS" / "configs" / "tts_infer.yaml"
    assert loaded.timeout_seconds == 44


def test_settings_service_loads_debug_log_settings() -> None:
    root = _runtime_root("yaml_debug")
    service = AppSettingsService(root)
    service.save_system_values("debug", {"enabled": True, "body_enabled": False, "file_enabled": True})

    settings = service.load_debug_log_settings()

    assert settings == DebugLogSettings(enabled=True, body_enabled=False, file_enabled=True)


def test_settings_service_loads_debug_file_disabled_by_default() -> None:
    root = _runtime_root("yaml_debug_legacy")
    service = AppSettingsService(root)
    service.save_system_values("debug", {"enabled": True, "body_enabled": False})

    settings = service.load_debug_log_settings()

    assert settings == DebugLogSettings(enabled=True, body_enabled=False, file_enabled=False)


def test_settings_service_saves_and_loads_theme_settings() -> None:
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
    )

    service.save_theme_settings(settings)
    loaded = service.load_theme_settings()
    system = load_yaml_mapping(service.system_config_path)

    assert loaded == settings
    for field, _label, _default in THEME_COLOR_FIELDS:
        assert system["ui"]["theme"][field] == getattr(settings, field)
    assert system["ui"]["theme"]["ai_enabled"] is True


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

    assert settings == ThemeSettings(
        primary_color=DEFAULT_THEME_SETTINGS.primary_color,
        primary_hover_color=DEFAULT_THEME_SETTINGS.primary_hover_color,
        accent_color=DEFAULT_THEME_SETTINGS.accent_color,
        text_color=DEFAULT_THEME_SETTINGS.text_color,
        secondary_text_color=DEFAULT_THEME_SETTINGS.secondary_text_color,
        muted_text_color=DEFAULT_THEME_SETTINGS.muted_text_color,
        page_background_color=DEFAULT_THEME_SETTINGS.page_background_color,
        panel_background_color=DEFAULT_THEME_SETTINGS.panel_background_color,
        input_background_color=DEFAULT_THEME_SETTINGS.input_background_color,
        bubble_background_color=DEFAULT_THEME_SETTINGS.bubble_background_color,
        border_color=DEFAULT_THEME_SETTINGS.border_color,
        ai_enabled=True,
    )


def test_default_theme_stylesheet_matches_legacy_pet_window_stylesheet() -> None:
    assert build_pet_window_stylesheet(DEFAULT_THEME_SETTINGS) == DEFAULT_PET_WINDOW_STYLESHEET


def test_theme_stylesheet_contains_configured_colors() -> None:
    stylesheet = build_pet_window_stylesheet(
        ThemeSettings(
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
        )
    )

    assert "#112233" in stylesheet
    assert "rgba(34, 51, 68" in stylesheet
    assert "#445566" in stylesheet
    assert "#070809" in stylesheet
    assert "rgba(17, 34, 51" in stylesheet


def test_parse_ai_theme_response_validates_json_and_colors() -> None:
    theme = parse_ai_theme_response(
        json.dumps(
            {
                "primary_color": "#112233",
                "primary_hover_color": "#223344",
                "accent_color": "#445566",
                "text_color": "#070809",
                "secondary_text_color": "#111213",
                "muted_text_color": "#141516",
                "page_background_color": "#f1f2f3",
                "panel_background_color": "#e1e2e3",
                "input_background_color": "#ffffff",
                "bubble_background_color": "#d1d2d3",
                "border_color": "#c1c2c3",
            }
        ),
        ai_enabled=True,
    )

    assert theme == ThemeSettings(
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
    )

    try:
        parse_ai_theme_response('{"primary_color":"#112233"}', ai_enabled=False)
    except ValueError as exc:
        assert "缺少字段" in str(exc)
    else:
        raise AssertionError("缺字段时应报错")

    try:
        parse_ai_theme_response(
            json.dumps(
                {
                    "primary_color": "112233",
                    "primary_hover_color": "#223344",
                    "accent_color": "#445566",
                    "text_color": "#070809",
                    "secondary_text_color": "#111213",
                    "muted_text_color": "#141516",
                    "page_background_color": "#f1f2f3",
                    "panel_background_color": "#e1e2e3",
                    "input_background_color": "#ffffff",
                    "bubble_background_color": "#d1d2d3",
                    "border_color": "#c1c2c3",
                }
            ),
            ai_enabled=False,
        )
    except ValueError as exc:
        assert "#RRGGBB" in str(exc)
    else:
        raise AssertionError("非法颜色时应报错")

    try:
        parse_ai_theme_response("not json", ai_enabled=False)
    except ValueError as exc:
        assert "有效 JSON" in str(exc)
    else:
        raise AssertionError("非 JSON 时应报错")


def _runtime_root(name: str) -> Path:
    root = Path(__file__).resolve().parents[2] / "__pycache__" / "test_runtime" / name / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    return root
