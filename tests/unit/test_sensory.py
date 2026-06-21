from __future__ import annotations

import base64
import io
import json
import sys
import wave
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.agent.tools import ToolRegistry
from app.agent.runtime import AgentRuntime
from app.llm.api_client import ChatMessage
from app.llm.prompts.types import ContextFragment, ContextRequest
from app.plugins.models import ContextProviderContribution
from app.sensory import audio_capture as audio_capture_module
from app.sensory.audio_capture import CapturedAudio
from app.sensory.context import SensoryContextProvider
from app.sensory.models import (
    SensoryObservation,
    SensoryProviderMode,
    SensoryRequest,
    SensorySource,
)
from app.sensory.pipeline import SensoryPipeline
from app.sensory.providers import (
    ApiSensoryProvider,
    FakeSensoryProvider,
    LlamaCppSensoryProvider,
    LmStudioSensoryProvider,
    OllamaSensoryProvider,
    SensoryProviderUnavailable,
    provider_from_config,
)
from app.sensory.settings import (
    SensoryProviderConfig,
    SensorySettings,
    SensorySourceSettings,
    sensory_settings_from_config,
)
from app.sensory.store import SensoryObservationStore
from app.sensory.tools import (
    SENSORY_OBSERVATION_CAPABILITY,
    SENSORY_OBSERVATION_TOOL_NAME,
    configured_sensory_sources,
    create_sensory_observation_tool,
)


def test_sensory_settings_defaults_are_conservative() -> None:
    settings = sensory_settings_from_config({}, {})

    assert settings.enabled is False
    assert settings.sources[SensorySource.VISION].mode == SensoryProviderMode.OFF
    assert settings.sources[SensorySource.SPEECH].mode == SensoryProviderMode.OFF
    assert settings.sources[SensorySource.SOUND].mode == SensoryProviderMode.OFF
    assert settings.context_budget_chars == 1200
    assert settings.retention_days == 7


def test_settings_sensory_test_image_is_decodable_png() -> None:
    from PySide6.QtGui import QImage

    from app.ui.settings.workers import _SENSORY_TEST_IMAGE_DATA_URL

    prefix, payload = _SENSORY_TEST_IMAGE_DATA_URL.split(",", 1)
    raw = base64.b64decode(payload, validate=True)
    image = QImage()

    assert prefix == "data:image/png;base64"
    assert image.loadFromData(raw, "PNG")
    assert not image.isNull()
    assert image.width() > 0
    assert image.height() > 0


def test_settings_sensory_test_audio_is_decodable_wav() -> None:
    from app.ui.settings.workers import _SENSORY_TEST_AUDIO_DATA_URL

    prefix, payload = _SENSORY_TEST_AUDIO_DATA_URL.split(",", 1)
    raw = base64.b64decode(payload, validate=True)

    assert prefix == "data:audio/wav;base64"
    with wave.open(io.BytesIO(raw), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getframerate() == 16000
        assert wav.getnframes() > 0


def test_sensory_settings_normalizes_source_and_provider_config() -> None:
    settings = sensory_settings_from_config(
        {
            "enabled": "yes",
            "context_budget_chars": 999999,
            "sources": {
                "vision": {
                    "mode": "api",
                    "confidence_threshold": 2,
                    "context_limit": 999,
                },
                "speech": {"mode": "local", "provider_id": "speech_custom"},
            },
        },
        {
            "providers": {
                "vision_api": {
                    "source": "vision",
                    "mode": "api",
                    "endpoint": "https://vlm.example/v1",
                    "model": "demo-vlm",
                    "timeout_seconds": "40",
                },
                "speech": {
                    "local": {
                        "id": "speech_custom",
                        "endpoint": "http://127.0.0.1:9010",
                        "model": "asr-small",
                    }
                },
            }
        },
    )

    assert settings.context_budget_chars == 6000
    assert settings.sources[SensorySource.VISION].provider_id == "vision_api"
    assert settings.sources[SensorySource.VISION].confidence_threshold == 1.0
    assert settings.sources[SensorySource.VISION].context_limit == 20
    assert settings.providers["vision_api"].endpoint == "https://vlm.example/v1"
    assert settings.providers["speech_custom"].mode == SensoryProviderMode.LOCAL


def test_sensory_pipeline_routes_by_mode_and_provider_id(tmp_path: Path) -> None:
    store = SensoryObservationStore(tmp_path / "sensory.jsonl")
    settings = SensorySettings(
        enabled=True,
        sources={
            SensorySource.VISION: SensorySourceSettings(
                mode=SensoryProviderMode.LOCAL,
                provider_id="fake_vision",
            )
        },
        providers={
            "fake_vision": SensoryProviderConfig(
                provider_id="fake_vision",
                source=SensorySource.VISION,
                mode=SensoryProviderMode.LOCAL,
            )
        },
    ).normalized()
    provider = FakeSensoryProvider(
        provider_id="fake_vision",
        source=SensorySource.VISION,
        summary="屏幕中有一个确认按钮。",
    )
    pipeline = SensoryPipeline(settings=settings, store=store, providers={"fake_vision": provider})

    observation = pipeline.observe(
        SensoryRequest(id="req_1", source=SensorySource.VISION, user_text="看一下屏幕")
    )

    assert observation is not None
    assert observation.provider_id == "fake_vision"
    assert store.recent(limit=1)[0].summary == "屏幕中有一个确认按钮。"


def test_sensory_pipeline_disabled_source_fails_closed(tmp_path: Path) -> None:
    store = SensoryObservationStore(tmp_path / "sensory.jsonl")
    provider = FakeSensoryProvider(provider_id="fake_vision", source=SensorySource.VISION)
    pipeline = SensoryPipeline(
        settings=SensorySettings().normalized(),
        store=store,
        providers={"fake_vision": provider},
    )

    observation = pipeline.observe(SensoryRequest(id="req_1", source=SensorySource.VISION))

    assert observation is None
    assert not store.path.exists()


def test_sensory_pipeline_does_not_capture_system_audio_when_source_disabled(tmp_path: Path) -> None:
    capture = _FakeSystemAudioCapture(tmp_path)
    pipeline = SensoryPipeline(
        settings=SensorySettings().normalized(),
        store=SensoryObservationStore(tmp_path / "sensory.jsonl"),
        providers={},
        audio_capture=capture,
    )

    observation = pipeline.observe_system_audio(source=SensorySource.SPEECH)

    assert observation is None
    assert capture.count == 0
    assert not pipeline.store.path.exists()


def test_system_audio_capture_factory_selects_platform_backend(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    registry = _FakeProcessRegistry()

    monkeypatch.setattr(audio_capture_module.sys, "platform", "darwin")
    macos_capture = audio_capture_module.create_system_audio_capture(
        tmp_path,
        resource_registry=registry,  # type: ignore[arg-type]
    )
    assert isinstance(macos_capture, audio_capture_module.MacOSSystemAudioCapture)
    assert macos_capture.resource_registry is registry

    monkeypatch.setattr(audio_capture_module.sys, "platform", "win32")
    windows_capture = audio_capture_module.create_system_audio_capture(
        tmp_path,
        resource_registry=registry,  # type: ignore[arg-type]
    )
    assert isinstance(windows_capture, audio_capture_module.WindowsSystemAudioCapture)
    assert windows_capture.resource_registry is registry

    monkeypatch.setattr(audio_capture_module.sys, "platform", "linux")
    linux_capture = audio_capture_module.create_system_audio_capture(
        tmp_path,
        resource_registry=registry,  # type: ignore[arg-type]
    )
    assert isinstance(linux_capture, audio_capture_module.LinuxSystemAudioCapture)
    assert linux_capture.resource_registry is registry

    monkeypatch.setattr(audio_capture_module.sys, "platform", "freebsd")
    assert audio_capture_module.create_system_audio_capture(tmp_path) is None


def test_managed_command_registers_and_detaches_process() -> None:
    registry = _FakeProcessRegistry()

    result = audio_capture_module._run_managed_command(
        [sys.executable, "-c", "print('ok')"],
        timeout_seconds=5,
        label="sensory_test_process",
        resource_registry=registry,  # type: ignore[arg-type]
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "ok"
    assert len(registry.resources) == 1
    assert registry.resources[0].label == "sensory_test_process"
    assert registry.resources[0].detached is True
    assert registry.resources[0].stopped is False


def test_linux_audio_capture_builds_pipewire_and_pulse_monitor_candidates(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    binaries = {
        "pw-record": "/usr/bin/pw-record",
        "parec": "/usr/bin/parec",
        "pactl": "/usr/bin/pactl",
    }
    monkeypatch.setattr(audio_capture_module.shutil, "which", lambda name: binaries.get(name))
    monkeypatch.setattr(
        audio_capture_module,
        "_default_pulse_monitor_source",
        lambda _pactl: "alsa_output.pci.monitor",
    )
    capture = audio_capture_module.LinuxSystemAudioCapture(cache_dir=tmp_path)

    commands = capture._candidate_commands(sample_rate=16000, channel_count=1)

    labels = [label for label, _command in commands]
    assert labels == ["pipewire", "pulseaudio"]
    pipewire_command = commands[0][1]
    pulse_command = commands[1][1]
    assert "{ stream.capture.sink=true }" in pipewire_command
    assert "alsa_output.pci.monitor" in pulse_command
    assert "--file-format=wav" in pulse_command


def test_provider_from_config_routes_supported_backends() -> None:
    def config(provider_id: str, backend: str, mode: SensoryProviderMode = SensoryProviderMode.LOCAL) -> SensoryProviderConfig:
        return SensoryProviderConfig(
            provider_id=provider_id,
            source=SensorySource.VISION,
            mode=mode,
            model="vlm",
            extra={"backend": backend},
        )

    assert isinstance(provider_from_config(config("lmstudio_vlm", "lmstudio")), LmStudioSensoryProvider)
    assert isinstance(provider_from_config(config("llama_vlm", "llama")), LlamaCppSensoryProvider)
    assert isinstance(provider_from_config(config("ollama_vlm", "ollama")), OllamaSensoryProvider)
    assert isinstance(
        provider_from_config(config("api_vlm", "api", mode=SensoryProviderMode.API)),
        ApiSensoryProvider,
    )


def test_configured_sensory_sources_require_enabled_provider_and_model(tmp_path: Path) -> None:
    settings = SensorySettings(
        enabled=True,
        sources={
            SensorySource.VISION: SensorySourceSettings(
                mode=SensoryProviderMode.LOCAL,
                provider_id="vision_local",
            ),
            SensorySource.SPEECH: SensorySourceSettings(
                mode=SensoryProviderMode.API,
                provider_id="speech_api",
            ),
        },
        providers={
            "vision_local": SensoryProviderConfig(
                provider_id="vision_local",
                source=SensorySource.VISION,
                mode=SensoryProviderMode.LOCAL,
                model="qwen-vl",
                extra={"backend": "lmstudio"},
            ),
            "speech_api": SensoryProviderConfig(
                provider_id="speech_api",
                source=SensorySource.SPEECH,
                mode=SensoryProviderMode.API,
                endpoint="https://asr.example/v1",
                model="",
            ),
        },
    ).normalized()
    pipeline = SensoryPipeline(
        settings=settings,
        store=SensoryObservationStore(tmp_path / "sensory.jsonl"),
        providers={},
    )

    assert configured_sensory_sources(pipeline) == (SensorySource.VISION,)


def test_observe_sensory_tool_fails_closed_when_source_disabled(tmp_path: Path) -> None:
    pipeline = SensoryPipeline(
        settings=SensorySettings().normalized(),
        store=SensoryObservationStore(tmp_path / "sensory.jsonl"),
        providers={},
    )
    tool = create_sensory_observation_tool(lambda: pipeline)

    result = tool.handler({"source": "speech"}) if tool.handler is not None else {}

    assert result["status"] == "unavailable"
    assert result["reason"] == "source_disabled"
    assert not pipeline.store.path.exists()


def test_observe_sensory_tool_routes_to_provider_and_records_observation(tmp_path: Path) -> None:
    settings = SensorySettings(
        enabled=True,
        sources={
            SensorySource.SPEECH: SensorySourceSettings(
                mode=SensoryProviderMode.LOCAL,
                provider_id="speech_fake",
            )
        },
        providers={
            "speech_fake": SensoryProviderConfig(
                provider_id="speech_fake",
                source=SensorySource.SPEECH,
                mode=SensoryProviderMode.LOCAL,
                endpoint="http://127.0.0.1:9000/v1",
                model="tiny-asr",
            )
        },
    ).normalized()
    pipeline = SensoryPipeline(
        settings=settings,
        store=SensoryObservationStore(tmp_path / "sensory.jsonl"),
        providers={
            "speech_fake": FakeSensoryProvider(
                provider_id="speech_fake",
                source=SensorySource.SPEECH,
                summary="用户说测试增强感知。",
            )
        },
    )
    tool = create_sensory_observation_tool(lambda: pipeline)

    result = tool.handler(
        {"source": "speech", "text": "测试增强感知", "event_type": "user_message"}
    ) if tool.handler is not None else {}

    assert result["status"] == "ok"
    assert result["observation"]["summary"] == "用户说测试增强感知。"
    assert pipeline.store.recent(limit=1)[0].summary == "用户说测试增强感知。"


def test_observe_sensory_tool_captures_system_audio_when_audio_media_missing(tmp_path: Path) -> None:
    settings = SensorySettings(
        enabled=True,
        sources={
            SensorySource.SPEECH: SensorySourceSettings(
                mode=SensoryProviderMode.LOCAL,
                provider_id="speech_fake",
            )
        },
        providers={
            "speech_fake": SensoryProviderConfig(
                provider_id="speech_fake",
                source=SensorySource.SPEECH,
                mode=SensoryProviderMode.LOCAL,
                endpoint="http://127.0.0.1:9000/v1",
                model="tiny-asr",
            )
        },
    ).normalized()
    capture = _FakeSystemAudioCapture(tmp_path)
    observed_paths: list[Path] = []

    def factory(request: SensoryRequest) -> SensoryObservation:
        audio_path = Path(request.media_ref)
        assert audio_path.is_file()
        assert request.metadata["capture_source"] == "system_audio"
        assert request.metadata["duration_seconds"] == 1.25
        observed_paths.append(audio_path)
        return SensoryObservation(
            id="obs_system_audio",
            source=request.source,
            created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            summary="系统音频中有人说 hello。",
            confidence=0.88,
            provider_id="speech_fake",
            mode=SensoryProviderMode.LOCAL,
            event_type=request.event_type,
        )

    pipeline = SensoryPipeline(
        settings=settings,
        store=SensoryObservationStore(tmp_path / "sensory.jsonl"),
        providers={
            "speech_fake": FakeSensoryProvider(
                provider_id="speech_fake",
                source=SensorySource.SPEECH,
                factory=factory,
            )
        },
        audio_capture=capture,
    )
    tool = create_sensory_observation_tool(lambda: pipeline)

    result = tool.handler(
        {"source": "speech", "duration_seconds": 1.25, "event_type": "user_message"}
    ) if tool.handler is not None else {}

    assert result["status"] == "ok"
    assert result["observation"]["summary"] == "系统音频中有人说 hello。"
    assert len(observed_paths) == 1
    assert not observed_paths[0].exists()
    assert pipeline.store.recent(limit=1)[0].summary == "系统音频中有人说 hello。"


def test_observe_sensory_tool_fails_closed_when_system_audio_capture_unavailable(tmp_path: Path) -> None:
    settings = SensorySettings(
        enabled=True,
        sources={
            SensorySource.SOUND: SensorySourceSettings(
                mode=SensoryProviderMode.LOCAL,
                provider_id="sound_fake",
            )
        },
        providers={
            "sound_fake": SensoryProviderConfig(
                provider_id="sound_fake",
                source=SensorySource.SOUND,
                mode=SensoryProviderMode.LOCAL,
                endpoint="http://127.0.0.1:9000/v1",
                model="audio-model",
            )
        },
    ).normalized()
    provider = FakeSensoryProvider(
        provider_id="sound_fake",
        source=SensorySource.SOUND,
        factory=lambda _request: (_ for _ in ()).throw(AssertionError("provider should not be called")),
    )
    pipeline = SensoryPipeline(
        settings=settings,
        store=SensoryObservationStore(tmp_path / "sensory.jsonl"),
        providers={"sound_fake": provider},
        audio_capture=None,
    )
    tool = create_sensory_observation_tool(lambda: pipeline)

    result = tool.handler({"source": "sound"}) if tool.handler is not None else {}

    assert result["status"] == "unavailable"
    assert result["reason"] == "audio_capture_unavailable"
    assert result["source"] == "sound"
    assert not pipeline.store.path.exists()


def test_observe_sensory_vision_requests_media_before_provider_call(tmp_path: Path) -> None:
    settings = SensorySettings(
        enabled=True,
        sources={
            SensorySource.VISION: SensorySourceSettings(
                mode=SensoryProviderMode.LOCAL,
                provider_id="vision_fake",
            )
        },
        providers={
            "vision_fake": SensoryProviderConfig(
                provider_id="vision_fake",
                source=SensorySource.VISION,
                mode=SensoryProviderMode.LOCAL,
                endpoint="http://127.0.0.1:9000/v1",
                model="vlm",
            )
        },
    ).normalized()
    pipeline = SensoryPipeline(
        settings=settings,
        store=SensoryObservationStore(tmp_path / "sensory.jsonl"),
        providers={
            "vision_fake": FakeSensoryProvider(
                provider_id="vision_fake",
                source=SensorySource.VISION,
            )
        },
    )
    tool = create_sensory_observation_tool(lambda: pipeline)

    result = tool.handler({"source": "vision"}) if tool.handler is not None else {}

    assert result["status"] == "needs_media"
    assert "observe_screen" in result["error"]
    assert not pipeline.store.path.exists()


def test_runtime_exposes_sensory_tool_only_when_provider_is_configured(tmp_path: Path) -> None:
    configured_settings = SensorySettings(
        enabled=True,
        sources={
            SensorySource.SPEECH: SensorySourceSettings(
                mode=SensoryProviderMode.LOCAL,
                provider_id="speech_local",
            )
        },
        providers={
            "speech_local": SensoryProviderConfig(
                provider_id="speech_local",
                source=SensorySource.SPEECH,
                mode=SensoryProviderMode.LOCAL,
                model="speech-sense",
                extra={"backend": "lmstudio"},
            )
        },
    ).normalized()
    pipeline = SensoryPipeline(
        settings=configured_settings,
        store=SensoryObservationStore(tmp_path / "sensory.jsonl"),
        providers={},
    )
    client = _CaptureToolClient()
    runtime = AgentRuntime(
        client,  # type: ignore[arg-type]
        "基础提示",
        tools=ToolRegistry([create_sensory_observation_tool(lambda: pipeline)]),
        memory=object(),
    )
    runtime.set_sensory_pipeline(pipeline)

    runtime.handle_user_message([ChatMessage(role="user", content="你刚才听到我说什么？")])

    tool_names = {tool["function"]["name"] for tool in client.last_tools}
    assert SENSORY_OBSERVATION_TOOL_NAME in tool_names
    assert runtime.tools.get(SENSORY_OBSERVATION_TOOL_NAME).capability == SENSORY_OBSERVATION_CAPABILITY
    assert "增强感知" in client.last_system_prompt

    disabled_client = _CaptureToolClient()
    disabled_pipeline = SensoryPipeline(
        settings=SensorySettings().normalized(),
        store=SensoryObservationStore(tmp_path / "disabled.jsonl"),
        providers={},
    )
    disabled_runtime = AgentRuntime(
        disabled_client,  # type: ignore[arg-type]
        "基础提示",
        tools=ToolRegistry([create_sensory_observation_tool(lambda: disabled_pipeline)]),
        memory=object(),
    )
    disabled_runtime.set_sensory_pipeline(disabled_pipeline)

    disabled_runtime.handle_user_message([ChatMessage(role="user", content="普通聊天")])

    disabled_tool_names = {tool["function"]["name"] for tool in disabled_client.last_tools}
    assert SENSORY_OBSERVATION_TOOL_NAME not in disabled_tool_names


def test_lmstudio_provider_posts_openai_compatible_vision_payload(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    captured: dict[str, Any] = {}

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(request.header_items())
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _FakeHTTPResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "屏幕有 LOCAL OK。",
                                    "visible_texts": ["LOCAL OK"],
                                    "confidence": 0.91,
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }
        )

    monkeypatch.setattr("app.sensory.providers.urllib.request.urlopen", fake_urlopen)
    provider = LmStudioSensoryProvider(
        SensoryProviderConfig(
            provider_id="lmstudio_vlm",
            source=SensorySource.VISION,
            mode=SensoryProviderMode.LOCAL,
            model="sakura-vlm",
            api_key="dummy",
        )
    )

    observation = provider.observe(
        SensoryRequest(
            id="req_1",
            source=SensorySource.VISION,
            user_text="读图",
            media_ref="data:image/png;base64,abc123",
        )
    )

    assert captured["url"] == "http://127.0.0.1:1234/v1/chat/completions"
    assert captured["timeout"] == 20
    assert captured["payload"]["model"] == "sakura-vlm"
    user_content = captured["payload"]["messages"][1]["content"]
    assert user_content[1]["image_url"]["url"] == "data:image/png;base64,abc123"
    assert captured["headers"]["Authorization"] == "Bearer dummy"
    assert observation.summary == "屏幕有 LOCAL OK。"
    assert observation.details["visible_texts"] == ["LOCAL OK"]
    assert observation.confidence == 0.91


def test_api_provider_posts_openai_compatible_audio_payload(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    captured: dict[str, Any] = {}

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _FakeHTTPResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "检测到短促提示音。",
                                    "details": {"notable_elements": ["tone"]},
                                    "confidence": 0.82,
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }
        )

    monkeypatch.setattr("app.sensory.providers.urllib.request.urlopen", fake_urlopen)
    provider = ApiSensoryProvider(
        SensoryProviderConfig(
            provider_id="api_sound",
            source=SensorySource.SOUND,
            mode=SensoryProviderMode.API,
            endpoint="https://api.example/v1",
            model="audio-model",
        )
    )

    observation = provider.observe(
        SensoryRequest(
            id="req_audio",
            source=SensorySource.SOUND,
            text="请识别音频。",
            media_ref="data:audio/wav;base64,abc123",
        )
    )

    assert captured["url"] == "https://api.example/v1/chat/completions"
    assert captured["timeout"] == 20
    user_content = captured["payload"]["messages"][1]["content"]
    assert user_content[0]["type"] == "text"
    assert user_content[1] == {
        "type": "input_audio",
        "input_audio": {"data": "abc123", "format": "wav"},
    }
    assert not any(part.get("type") == "image_url" for part in user_content)
    assert observation.summary == "检测到短促提示音。"
    assert observation.details["notable_elements"] == ["tone"]
    assert observation.confidence == 0.82


def test_openai_provider_does_not_treat_wav_path_as_image(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    captured: dict[str, Any] = {}
    audio_path = tmp_path / "sound.wav"
    audio_path.write_bytes(b"RIFFxxxxWAVEfmt ")

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _FakeHTTPResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"summary": "音频已处理。", "confidence": 0.7},
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }
        )

    monkeypatch.setattr("app.sensory.providers.urllib.request.urlopen", fake_urlopen)
    provider = LmStudioSensoryProvider(
        SensoryProviderConfig(
            provider_id="lmstudio_sound",
            source=SensorySource.SOUND,
            mode=SensoryProviderMode.LOCAL,
            model="audio-model",
        )
    )

    provider.observe(
        SensoryRequest(id="req_audio_path", source=SensorySource.SOUND, media_ref=str(audio_path))
    )

    user_content = captured["payload"]["messages"][1]["content"]
    assert user_content[1]["type"] == "input_audio"
    assert user_content[1]["input_audio"]["format"] == "wav"
    assert not any(part.get("type") == "image_url" for part in user_content)


def test_llama_provider_uses_openai_compatible_default_endpoint() -> None:
    provider = LlamaCppSensoryProvider(
        SensoryProviderConfig(
            provider_id="llama_vlm",
            source=SensorySource.VISION,
            mode=SensoryProviderMode.LOCAL,
            model="llava",
        )
    )

    assert provider.config.endpoint == "http://127.0.0.1:8080/v1"


def test_ollama_provider_posts_native_chat_images_payload(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    captured: dict[str, Any] = {}

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _FakeHTTPResponse(
            {
                "message": {
                    "content": json.dumps(
                        {
                            "summary": "图中有按钮。",
                            "details": {"notable_elements": ["button"]},
                            "confidence": 0.77,
                        },
                        ensure_ascii=False,
                    )
                }
            }
        )

    monkeypatch.setattr("app.sensory.providers.urllib.request.urlopen", fake_urlopen)
    provider = OllamaSensoryProvider(
        SensoryProviderConfig(
            provider_id="ollama_vlm",
            source=SensorySource.VISION,
            mode=SensoryProviderMode.LOCAL,
            model="llava:latest",
        )
    )

    observation = provider.observe(
        SensoryRequest(
            id="req_1",
            source=SensorySource.VISION,
            media_ref="data:image/png;base64,abc123",
        )
    )

    assert captured["url"] == "http://127.0.0.1:11434/api/chat"
    assert captured["payload"]["model"] == "llava:latest"
    assert captured["payload"]["stream"] is False
    assert captured["payload"]["format"] == "json"
    assert captured["payload"]["messages"][1]["images"] == ["abc123"]
    assert observation.summary == "图中有按钮。"
    assert observation.details["notable_elements"] == ["button"]
    assert observation.confidence == 0.77


def test_ollama_provider_fails_closed_for_audio_sources(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def should_not_call(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("HTTP should not be called")

    monkeypatch.setattr("app.sensory.providers.urllib.request.urlopen", should_not_call)
    provider = OllamaSensoryProvider(
        SensoryProviderConfig(
            provider_id="ollama_sound",
            source=SensorySource.SOUND,
            mode=SensoryProviderMode.LOCAL,
            model="audio-model",
        )
    )

    try:
        provider.observe(
            SensoryRequest(
                id="req_audio",
                source=SensorySource.SOUND,
                media_ref="data:audio/wav;base64,abc123",
            )
        )
    except SensoryProviderUnavailable as exc:
        assert "does not support audio input" in str(exc)
    else:
        raise AssertionError("expected SensoryProviderUnavailable")


def test_audio_provider_requires_audio_media(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def should_not_call(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("HTTP should not be called")

    monkeypatch.setattr("app.sensory.providers.urllib.request.urlopen", should_not_call)
    provider = ApiSensoryProvider(
        SensoryProviderConfig(
            provider_id="api_sound",
            source=SensorySource.SOUND,
            mode=SensoryProviderMode.API,
            endpoint="https://api.example/v1",
            model="audio-model",
        )
    )

    try:
        provider.observe(SensoryRequest(id="req_audio", source=SensorySource.SOUND))
    except SensoryProviderUnavailable as exc:
        assert "requires an audio media_ref" in str(exc)
    else:
        raise AssertionError("expected SensoryProviderUnavailable")


def test_api_provider_fails_closed_without_model(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def should_not_call(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("HTTP should not be called")

    monkeypatch.setattr("app.sensory.providers.urllib.request.urlopen", should_not_call)
    provider = ApiSensoryProvider(
        SensoryProviderConfig(
            provider_id="api_vlm",
            source=SensorySource.VISION,
            mode=SensoryProviderMode.API,
            endpoint="https://api.example/v1",
            model="",
        )
    )

    try:
        provider.observe(SensoryRequest(id="req_1", source=SensorySource.VISION))
    except SensoryProviderUnavailable as exc:
        assert "has no model" in str(exc)
    else:
        raise AssertionError("expected SensoryProviderUnavailable")


def test_sensory_store_redacts_sensitive_text_and_drops_raw_media(tmp_path: Path) -> None:
    store = SensoryObservationStore(tmp_path / "sensory.jsonl")
    store.append(
        SensoryObservation(
            id="obs_1",
            source=SensorySource.VISION,
            created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            summary="api_key: sk-abc1234567890123456789",
            details={
                "visible_texts": ["密码: hunter2"],
                "image_url": "data:image/png;base64,raw",
            },
            confidence=0.9,
            user_text="token=sk-abcdefabcdefabcdef",
        )
    )

    raw = store.path.read_text(encoding="utf-8")
    loaded = store.recent(limit=1)[0]

    assert "[REDACTED]" in raw
    assert "image_url" not in raw
    assert "data:image" not in raw
    assert loaded.sensitive_redacted is True


def test_sensory_store_prunes_by_retention_and_limit(tmp_path: Path) -> None:
    store = SensoryObservationStore(tmp_path / "sensory.jsonl", retention_days=1, retention_limit=2)
    old_time = (datetime.now().astimezone() - timedelta(days=2)).isoformat(timespec="seconds")
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    for idx, created_at in enumerate([old_time, now, now, now]):
        store.append(
            SensoryObservation(
                id=f"obs_{idx}",
                source=SensorySource.SOUND,
                created_at=created_at,
                summary=f"sound {idx}",
                confidence=0.8,
            )
        )

    records = store.recent(limit=10)

    assert [record.id for record in records] == ["obs_3", "obs_2"]


def test_sensory_context_filters_by_source_confidence_relevance_and_budget(tmp_path: Path) -> None:
    store = SensoryObservationStore(tmp_path / "sensory.jsonl")
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    store.append(
        SensoryObservation(
            id="vision_relevant",
            source=SensorySource.VISION,
            created_at=now,
            summary="屏幕上显示 E42 报错。",
            details={"visible_texts": ["E42", "保存失败"]},
            confidence=0.95,
        )
    )
    store.append(
        SensoryObservation(
            id="vision_low",
            source=SensorySource.VISION,
            created_at=now,
            summary="低置信度内容",
            confidence=0.2,
        )
    )
    store.append(
        SensoryObservation(
            id="speech_irrelevant",
            source=SensorySource.SPEECH,
            created_at=now,
            summary="用户说了晚饭。",
            confidence=0.95,
        )
    )
    settings = SensorySettings(
        enabled=True,
        context_budget_chars=420,
        sources={
            SensorySource.VISION: SensorySourceSettings(confidence_threshold=0.5),
            SensorySource.SPEECH: SensorySourceSettings(confidence_threshold=0.5),
        },
    ).normalized()
    provider = SensoryContextProvider(settings=settings, store=store)

    fragments = provider.build_context(
        {"messages": [ChatMessage(role="user", content="刚才屏幕有什么报错？")]}
    )
    context = fragments[0].content if fragments else ""

    assert "vision_relevant" in context
    assert "E42" in context
    assert "vision_low" not in context
    assert "speech_irrelevant" not in context
    assert len(context) <= 420


def test_agent_runtime_passes_runtime_context_to_context_providers() -> None:
    seen: list[ContextRequest] = []

    class Client:
        def resolve_dialogue_params(self):  # type: ignore[no-untyped-def]
            return 0.8, {}

        def complete_with_tools(self, _system_prompt, _messages, **_kwargs):  # type: ignore[no-untyped-def]
            class Turn:
                content = '{"segments":[{"ja":"見たよ。","zh":"我看到了。","tone":"中性"}]}'
                tool_calls: list[object] = []
                message = {"role": "assistant", "content": content}
                runtime_context_role = "system"

            return Turn()

    def build_context(request: ContextRequest):
        seen.append(request)
        return (
            ContextFragment(
                fragment_id="capture",
                source="test",
                content="runtime context ok",
            ),
        )

    runtime = AgentRuntime(
        Client(),  # type: ignore[arg-type]
        "基础提示",
        memory=object(),
        context_providers=[
            ContextProviderContribution(
                provider_id="capture",
                description="d",
                build_context=build_context,
            )
        ],
    )

    runtime.handle_user_message([ChatMessage(role="user", content="请看屏幕上的错误")])

    assert seen
    assert seen[0].source == "chat"
    assert seen[0].mode == "normal"
    assert seen[0].current_input == "请看屏幕上的错误"


class _FakeHTTPResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.status = 200

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: object | None,
    ) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


class _FakeSystemAudioCapture:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.count = 0

    def capture(
        self,
        *,
        duration_seconds: float = 3.0,
        sample_rate: int = 16000,
        channel_count: int = 1,
        exclude_current_process: bool = True,
    ) -> CapturedAudio:
        del exclude_current_process
        self.count += 1
        path = self.tmp_path / f"captured_system_audio_{self.count}.wav"
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(channel_count)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(b"\x00\x00" * max(1, int(sample_rate * min(duration_seconds, 0.01))))
        return CapturedAudio(
            path=path,
            duration_seconds=duration_seconds,
            sample_rate=sample_rate,
            channel_count=channel_count,
        )


class _FakeProcessRegistry:
    def __init__(self) -> None:
        self.resources: list[_FakeProcessResource] = []

    def adopt_process(self, process, *, label: str = "", **_kwargs):  # type: ignore[no-untyped-def]
        resource = _FakeProcessResource(process=process, label=label)
        self.resources.append(resource)
        return resource


class _FakeProcessResource:
    def __init__(self, *, process, label: str) -> None:  # type: ignore[no-untyped-def]
        self.process = process
        self.label = label
        self.detached = False
        self.stopped = False

    def detach(self):  # type: ignore[no-untyped-def]
        self.detached = True
        return self.process

    def stop(self, _timeout_ms: int = 1000) -> bool:
        self.stopped = True
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=2)
        return True


class _CaptureToolClient:
    def __init__(self) -> None:
        self.last_tools: list[dict[str, Any]] = []
        self.last_system_prompt = ""

    def resolve_dialogue_params(self):  # type: ignore[no-untyped-def]
        return 0.8, {}

    def complete_with_tools(
        self,
        system_prompt: str,
        _messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]],
        **_kwargs: object,
    ):  # type: ignore[no-untyped-def]
        self.last_system_prompt = system_prompt
        self.last_tools = list(tools)

        class Turn:
            content = '{"segments":[{"ja":"わかった。","zh":"知道了。","tone":"中性"}]}'
            tool_calls: list[object] = []
            message = {"role": "assistant", "content": content}
            runtime_context_role = "system"

        return Turn()
