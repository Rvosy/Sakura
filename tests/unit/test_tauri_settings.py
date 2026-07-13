from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.agent.mcp import MCPRuntimeSettings
from app.agent.runtime_limits import RuntimeLoopSettings
from app.agent.screen_awareness import ScreenAwarenessSettings
from app.brain_host.secondary_windows import (
    apply_settings_payload,
    build_settings_request,
    secondary_host_call,
)
from app.config.settings_service import (
    AppSettingsService,
    BackchannelSettings,
    BubbleSettings,
    DebugLogSettings,
    StartupSettings,
)
from app.config.theme import DEFAULT_THEME_SETTINGS
from app.plugins.models import (
    PluginSettingsAction,
    PluginSettingsContribution,
    PluginSettingsField,
)


class FakeSettingsService:
    def __init__(self) -> None:
        self.saved: list[tuple[str, object]] = []
        self.system_values = {
            "ui": {
                "portrait_scale_percent": 100,
                "control_panel_width": 640,
                "bubble_height": 128,
                "control_panel_vertical_offset": 0,
                "input_bar_offset": 0,
                "subtitle_typing_interval_ms": 35,
                "reply_segment_pause_ms": 100,
                "speech_font_size": 16,
                "name_font_size": 13,
                "input_font_size": 15,
                "button_font_size": 13,
            },
            "screen_observation": {"enabled": True, "autonomous_enabled": True},
        }

    def load_system_values(self, section: str):  # type: ignore[no-untyped-def]
        return dict(self.system_values.get(section, {}))

    def save_system_values(self, section: str, values):  # type: ignore[no-untyped-def]
        self.saved.append((section, dict(values)))
        self.system_values.setdefault(section, {}).update(values)

    def load_screen_awareness_settings(self) -> ScreenAwarenessSettings:
        return ScreenAwarenessSettings()

    def load_mcp_runtime_settings(self) -> MCPRuntimeSettings:
        return MCPRuntimeSettings()

    def load_runtime_loop_settings(self) -> RuntimeLoopSettings:
        return RuntimeLoopSettings()

    def load_debug_log_settings(self) -> DebugLogSettings:
        return DebugLogSettings()

    def load_bubble_settings(self) -> BubbleSettings:
        return BubbleSettings()

    def load_theme_settings(self):  # type: ignore[no-untyped-def]
        return DEFAULT_THEME_SETTINGS

    def load_character_theme_overrides(self):  # type: ignore[no-untyped-def]
        return {}

    def load_api_settings(self):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            base_url="https://example.invalid/v1",
            api_key="secret",
            model="demo-model",
            timeout_seconds=60,
            temperature=None,
            top_p=None,
            max_tokens=None,
        )

    def load_api_profiles(self):  # type: ignore[no-untyped-def]
        return []

    def load_model_selection(self):  # type: ignore[no-untyped-def]
        return SimpleNamespace(get=lambda _slot: None)

    def load_tts_settings(self, **_kwargs):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            enabled=False,
            provider="none",
            api_url="",
            work_dir=None,
            python_path=None,
            tts_config_path=None,
            timeout_seconds=60,
        )

    def load_startup_settings(self) -> StartupSettings:
        return StartupSettings()

    def load_backchannel_settings(self) -> BackchannelSettings:
        return BackchannelSettings()

    def load_memory_curation_settings(self):  # type: ignore[no-untyped-def]
        return SimpleNamespace(trigger_turns=8, backfill_limit=200)

    def save_screen_awareness_settings(self, value): self.saved.append(("screen", value))  # type: ignore[no-untyped-def]
    def save_mcp_runtime_settings(self, value): self.saved.append(("mcp", value))  # type: ignore[no-untyped-def]
    def save_runtime_loop_settings(self, value): self.saved.append(("loop", value))  # type: ignore[no-untyped-def]
    def save_debug_log_settings(self, value): self.saved.append(("debug", value))  # type: ignore[no-untyped-def]
    def save_bubble_settings(self, value): self.saved.append(("bubble", value))  # type: ignore[no-untyped-def]
    def save_backchannel_settings(self, value): self.saved.append(("backchannel", value))  # type: ignore[no-untyped-def]
    def save_memory_curation_settings(self, value): self.saved.append(("memory", value))  # type: ignore[no-untyped-def]
    def save_startup_settings(self, value): self.saved.append(("startup", value))  # type: ignore[no-untyped-def]
    def save_current_character_id(self, _registry, value): self.saved.append(("character", value))  # type: ignore[no-untyped-def]
    def save_theme_settings(self, value): self.saved.append(("theme", value))  # type: ignore[no-untyped-def]
    def save_character_theme_override(self, key, value): self.saved.append((f"theme:{key}", value))  # type: ignore[no-untyped-def]


def _context() -> SimpleNamespace:
    settings = FakeSettingsService()
    profile = SimpleNamespace(
        id="demo",
        display_name="Demo",
        voice=None,
        theme_settings=DEFAULT_THEME_SETTINGS,
    )
    registry = SimpleNamespace(profiles={"demo": profile}, get=lambda key: profile if key == "demo" else None)
    return SimpleNamespace(
        base_dir=Path("."),
        settings_service=settings,
        character_profile=profile,
        character_registry=registry,
        screen_awareness_settings=ScreenAwarenessSettings(),
        mcp_settings=MCPRuntimeSettings(),
        debug_log_settings=DebugLogSettings(),
        startup_settings=StartupSettings(),
        memory_curation_settings=SimpleNamespace(trigger_turns=8, backfill_limit=200),
        agent_runtime=SimpleNamespace(
            runtime_loop_settings=RuntimeLoopSettings(),
            set_runtime_loop_settings=lambda value: setattr(registry, "runtime_loop", value),
        ),
        plugin_manager=SimpleNamespace(plugin_settings=[], results=[]),
    )


def test_build_settings_request_matches_existing_frontend_contract() -> None:
    request = build_settings_request(_context(), base_dir=Path("."), nonce="nonce-demo")

    assert request["version"] == 3
    assert request["nonce"] == "nonce-demo"
    assert request["character"]["current_character_id"] == "demo"
    assert request["character"]["layout"]["control_panel_width"] == 640
    assert request["screen_awareness"]["screen_context_resolution"] == "fullscreen"
    assert request["api"]["settings"]["timeout_seconds"] == 60
    assert request["tts"]["providers"][0]["id"] == "gpt-sovits"
    assert request["limits"]["portrait_scale_percent"] == [50, 150]
    assert request["theme_fields"]
    assert request["visual_effect_modes"]


def test_apply_settings_saves_sections_and_updates_runtime_without_restarting_app() -> None:
    context = _context()
    application = SimpleNamespace(
        context=context,
        config=SimpleNamespace(base_dir=Path(".")),
        _screen_awareness_enabled=True,
        _screen_context_resolution="fullscreen",
        sync_scheduler_jobs=lambda **_kwargs: setattr(context, "scheduler_synced", True),
        refresh_character=lambda character_id: setattr(context, "refreshed_character", character_id),
        refresh_tts=lambda: setattr(context, "tts_refreshed", True),
    )
    payload = build_settings_request(context, base_dir=Path("."), nonce="nonce-demo")
    payload["screen_awareness"].update(
        {"enabled": False, "screen_context_enabled": False, "check_interval_minutes": 3}
    )
    payload["character"]["layout"]["control_panel_width"] = 700
    payload["runtime_loop"]["max_agent_steps_per_turn"] = 7

    result = apply_settings_payload(application, payload)

    assert result["applied"] is True
    assert result["restartRequired"] == []
    assert application._screen_awareness_enabled is False
    assert context.scheduler_synced is True
    assert context.settings_service.system_values["ui"]["control_panel_width"] == 700
    assert getattr(context.character_registry, "runtime_loop").max_agent_steps_per_turn == 7
    assert context.refreshed_character == "demo"
    assert context.tts_refreshed is True


def test_settings_module_is_backed_by_app_settings_service() -> None:
    assert AppSettingsService.__module__ == "app.config.settings_service"


def test_tauri_settings_discovers_yaml_plugins_and_serializes_declarative_settings(
    tmp_path: Path,
) -> None:
    plugin_dir = tmp_path / "plugins" / "demo_plugin"
    plugin_dir.mkdir(parents=True)
    plugin_dir.joinpath("plugin.yaml").write_text(
        """
api_version: 2
id: demo_plugin
name: Demo Plugin
entry: plugin:DemoPlugin
enabled: true
permissions:
  - plugin_settings
""".lstrip(),
        encoding="utf-8",
    )
    contribution = PluginSettingsContribution(
        section_id="demo",
        title="Demo 设置",
        fields=(
            PluginSettingsField("enabled", "启用", "boolean", default=False),
            PluginSettingsField("port", "端口", "integer", default=8765, minimum=1, maximum=65535),
        ),
        load=lambda: {"enabled": True, "port": 9000},
        plugin_id="demo_plugin",
    )
    context = _context()
    context.plugin_manager = SimpleNamespace(plugin_settings=[contribution], results=[])

    request = build_settings_request(context, base_dir=tmp_path)

    assert [item["id"] for item in request["plugins"]["items"]] == ["demo_plugin"]
    plugin = request["plugins"]["items"][0]
    assert plugin["permissions"] == ["plugin_settings"]
    assert plugin["settings"][0]["values"] == {"enabled": True, "port": 9000}


def test_tauri_settings_saves_declarative_plugin_values_and_runs_actions(tmp_path: Path) -> None:
    saved: list[dict[str, object]] = []
    actions: list[dict[str, object]] = []
    contribution = PluginSettingsContribution(
        section_id="demo",
        title="Demo 设置",
        fields=(
            PluginSettingsField("enabled", "启用", "boolean", default=False),
            PluginSettingsField("status", "状态", "readonly", default="", readonly=True),
        ),
        load=lambda: {"enabled": False, "status": "idle"},
        save=lambda values: saved.append(dict(values)),
        actions=(
            PluginSettingsAction(
                "refresh",
                "刷新",
                handler=lambda values: actions.append(dict(values)) or {"values": {"status": "ready"}},
            ),
        ),
        plugin_id="demo_plugin",
    )
    context = _context()
    context.plugin_manager = SimpleNamespace(plugin_settings=[contribution], results=[])
    application = SimpleNamespace(
        context=context,
        config=SimpleNamespace(base_dir=tmp_path),
        _screen_awareness_enabled=True,
        _screen_context_resolution="fullscreen",
        sync_scheduler_jobs=lambda **_kwargs: None,
        refresh_character=lambda _character_id: None,
        refresh_tts=lambda: None,
    )
    payload = build_settings_request(context, base_dir=tmp_path)
    payload["plugins"] = {
        "enabled_by_id": {},
        "settings_by_id": {"demo_plugin": {"demo": {"enabled": True}}},
    }

    result = apply_settings_payload(application, payload)
    action_result = secondary_host_call(
        application,
        "plugin.settings_action",
        {
            "plugin_id": "demo_plugin",
            "section_id": "demo",
            "action_id": "refresh",
            "values": {"enabled": True},
        },
    )

    assert result["applied"] is True
    assert saved == [{"enabled": True}]
    assert actions == [{"enabled": True}]
    assert action_result == {"values": {"status": "ready"}}
