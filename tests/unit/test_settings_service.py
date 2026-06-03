from __future__ import annotations

import uuid
from pathlib import Path

from app.agent.mcp.settings import MCPRuntimeSettings
from app.config.settings_service import AppSettingsService, DebugLogSettings
from app.config.yaml_config import load_yaml_mapping
from app.llm.api_client import ApiSettings
from app.agent.proactive_care import ProactiveCareSettings
from app.voice.tts import TTS_PROVIDER_CUSTOM_GPT_SOVITS, GPTSoVITSTTSSettings


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
            work_dir=root / "data" / "tts_bundles" / "installed" / "gpt_sovits_v2pro",
            ref_lang="ja",
            text_lang="ja",
            timeout_seconds=22,
        )
    )
    service.save_current_character_id(CharacterRegistryStub(), "nanami")  # type: ignore[arg-type]
    service.save_mcp_runtime_settings(MCPRuntimeSettings(windows_enabled=True))
    service.save_debug_log_settings(DebugLogSettings(enabled=True, body_enabled=True, file_enabled=True))
    service.save_proactive_care_settings(
        ProactiveCareSettings(
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
    assert api["tts"]["gpt_sovits"]["work_dir"] == "data/tts_bundles/installed/gpt_sovits_v2pro"
    assert api["tts"]["gpt_sovits"]["timeout_seconds"] == 22
    assert characters["current_character_id"] == "nanami"
    assert system["mcp"]["windows_enabled"] is False
    assert system["debug"]["enabled"] is True
    assert system["debug"]["body_enabled"] is True
    assert system["debug"]["file_enabled"] is True
    assert system["proactive_care"]["check_interval_minutes"] == 5


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
        work_dir=root / "data" / "tts_bundles" / "installed" / "genie_tts_server",
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
    assert saved["tts"]["genie_tts"]["work_dir"] == "data/tts_bundles/installed/genie_tts_server"
    assert saved["tts"]["genie_tts"]["onnx_model_dir"] == "data/tts_bundles/onnx/sakura"
    assert loaded.provider == "genie-tts"
    assert loaded.work_dir == root / "data" / "tts_bundles" / "installed" / "genie_tts_server"
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
    assert loaded.provider == TTS_PROVIDER_CUSTOM_GPT_SOVITS
    assert loaded.api_url == "http://192.168.1.20:9880/tts"
    assert loaded.work_dir == root / "external" / "GPT-SoVITS"
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


def _runtime_root(name: str) -> Path:
    root = Path(__file__).resolve().parents[2] / "__pycache__" / "test_runtime" / name / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    return root
