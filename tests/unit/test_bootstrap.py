from __future__ import annotations

from pathlib import Path
import uuid

import pytest


def test_build_initial_app_context_skips_deferred_runtime_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.core.bootstrap as bootstrap
    from app.voice.tts import NullTTSProvider

    root = _build_startup_root()
    calls: list[str] = []

    import app.voice.factory as tts_factory

    monkeypatch.setattr(
        tts_factory,
        "GPTSoVITSTTSProvider",
        lambda _settings, **_kwargs: calls.append("tts"),
    )
    monkeypatch.setattr(
        bootstrap.PluginManager,
        "load_from_config",
        lambda _self, _registry: calls.append("plugins"),
    )
    monkeypatch.setattr(
        bootstrap,
        "register_mcp_tools_from_config",
        lambda *_args, **_kwargs: calls.append("mcp"),
    )

    context = bootstrap.build_initial_app_context(root)

    assert context.startup_initializing
    assert context.character_profile.id == "demo"
    assert isinstance(context.tts_provider, NullTTSProvider)
    assert context.mcp_tool_provider is None
    assert calls == []


def test_build_deferred_services_loads_injectable_runtime_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.core.bootstrap as bootstrap
    from app.agent.tools import Tool
    from app.voice.tts_settings import GPTSoVITSTTSSettings

    root = _build_startup_root()
    context = bootstrap.build_initial_app_context(root)
    tts_provider = object()
    mcp_provider = object()
    provider_base_dirs: list[Path | None] = []

    monkeypatch.setattr(
        type(context.settings_service),
        "load_tts_settings",
        lambda _self, **_kwargs: GPTSoVITSTTSSettings(
            enabled=True,
            api_url="http://127.0.0.1:9880/tts",
            ref_audio_path=root / "characters" / "demo" / "portrait.png",
            ref_text_path=root / "characters" / "demo" / "card.md",
            ref_text="test",
            ref_lang="ja",
            text_lang="ja",
            timeout_seconds=1,
        ),
    )
    def fake_gpt_provider(_settings, *, base_dir: Path | None = None, **_kwargs):  # type: ignore[no-untyped-def]
        provider_base_dirs.append(base_dir)
        return tts_provider

    import app.voice.factory as tts_factory
    monkeypatch.setattr(tts_factory, "GPTSoVITSTTSProvider", fake_gpt_provider)

    def fake_load_plugins(self, registry):  # type: ignore[no-untyped-def]
        registry.register(Tool(name="plugin_demo", description="plugin"))

    def fake_register_mcp(_base_dir, registry, **_kwargs):  # type: ignore[no-untyped-def]
        registry.register(Tool(name="mcp_demo", description="mcp", group="mcp"))
        return mcp_provider

    monkeypatch.setattr(bootstrap.PluginManager, "load_from_config", fake_load_plugins)
    monkeypatch.setattr(bootstrap, "register_mcp_tools_from_config", fake_register_mcp)

    services = bootstrap.build_deferred_services(root, context)

    assert services.tts_provider is tts_provider
    assert services.mcp_tool_provider is mcp_provider
    assert services.tool_registry.get("plugin_demo") is not None
    assert services.tool_registry.get("mcp_demo") is not None
    assert services.errors == ()
    assert provider_base_dirs == [root]


def test_build_deferred_services_creates_genie_tts_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.core.bootstrap as bootstrap
    from app.voice.tts_settings import GPTSoVITSTTSSettings

    root = _build_startup_root()
    context = bootstrap.build_initial_app_context(root)
    genie_provider = object()
    provider_base_dirs: list[Path | None] = []

    monkeypatch.setattr(
        type(context.settings_service),
        "load_tts_settings",
        lambda _self, **_kwargs: GPTSoVITSTTSSettings(
            enabled=True,
            provider="genie-tts",
            api_url="http://127.0.0.1:9880/",
            ref_audio_path=root / "characters" / "demo" / "portrait.png",
            ref_text_path=root / "characters" / "demo" / "card.md",
            ref_text="test",
            ref_lang="ja",
            text_lang="ja",
            timeout_seconds=1,
        ),
    )
    def fake_genie_provider(_settings, *, base_dir: Path | None = None, **_kwargs):  # type: ignore[no-untyped-def]
        provider_base_dirs.append(base_dir)
        return genie_provider

    import app.voice.factory as tts_factory
    monkeypatch.setattr(tts_factory, "GenieTTSProvider", fake_genie_provider)
    monkeypatch.setattr(bootstrap.PluginManager, "load_from_config", lambda *_args: None)
    monkeypatch.setattr(bootstrap, "register_mcp_tools_from_config", lambda *_args, **_kwargs: None)

    services = bootstrap.build_deferred_services(root, context)

    assert services.tts_provider is genie_provider
    assert provider_base_dirs == [root]


def test_build_deferred_services_disables_tts_for_voice_less_character(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.core.bootstrap as bootstrap
    from app.voice.tts import NullTTSProvider

    root = _build_startup_root()
    (root / "data" / "config" / "api.yaml").write_text(
        """
llm:
  base_url: https://api.example.com/v1
  api_key: test-key
  model: test-model
tts:
  provider: gpt-sovits
  enabled: true
  gpt_sovits:
    api_url: http://127.0.0.1:9880/tts
    ref_lang: ja
    text_lang: ja
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(bootstrap.PluginManager, "load_from_config", lambda *_args: None)
    monkeypatch.setattr(bootstrap, "register_mcp_tools_from_config", lambda *_args, **_kwargs: None)

    context = bootstrap.build_initial_app_context(root)
    services = bootstrap.build_deferred_services(root, context)

    assert isinstance(services.tts_provider, NullTTSProvider)
    assert not any(error.startswith("TTS") for error in services.errors)


def _build_startup_root() -> Path:
    root = (
        Path(__file__).resolve().parents[2]
        / "__pycache__"
        / "test_runtime"
        / "startup_state"
        / uuid.uuid4().hex
    )
    config_dir = root / "data" / "config"
    character_dir = root / "characters" / "demo"
    config_dir.mkdir(parents=True)
    character_dir.mkdir(parents=True)

    (config_dir / "api.yaml").write_text(
        """
llm:
  base_url: https://api.example.com/v1
  api_key: test-key
  model: test-model
tts:
  provider: none
  enabled: false
""".strip(),
        encoding="utf-8",
    )
    (config_dir / "characters.yaml").write_text(
        "current_character_id: demo\n",
        encoding="utf-8",
    )
    (config_dir / "system_config.yaml").write_text(
        """
ui:
  portrait_scale_percent: 125
mcp:
  windows_enabled: true
""".strip(),
        encoding="utf-8",
    )
    (character_dir / "card.md").write_text("system prompt", encoding="utf-8")
    (character_dir / "portrait.png").write_bytes(b"not a real image")
    (character_dir / "character.json").write_text(
        """
{
  "id": "demo",
  "display_name": "Demo",
  "initial_message": "hello",
  "card": "card.md",
  "portrait": {
    "default": "portrait.png"
  }
}
""".strip(),
        encoding="utf-8",
    )
    return root
