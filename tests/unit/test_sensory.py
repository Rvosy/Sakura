from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.agent.tools import ToolRegistry
from app.agent.runtime import AgentRuntime
from app.llm.api_client import ChatMessage
from app.llm.prompts.types import ContextFragment, ContextRequest
from app.plugins.models import ContextProviderContribution
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
