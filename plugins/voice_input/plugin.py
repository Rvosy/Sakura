from __future__ import annotations

from typing import Any

from app.plugins import (
    ChatUIWidgetContribution,
    PluginBase,
    PluginCapabilityRegistry,
    PluginContext,
    SettingsPanelContribution,
)


class VoiceInputPlugin(PluginBase):
    """在聊天输入栏提供本地 ASR 语音输入。"""

    plugin_id = "voice_input"
    plugin_version = "0.1.0"

    def __init__(self) -> None:
        self.context: PluginContext | None = None
        self._widgets: list[Any] = []
        self._settings_panels: list[Any] = []

    def initialize(
        self,
        register: PluginCapabilityRegistry,
        context: PluginContext,
    ) -> None:
        self.context = context
        register.register_chat_ui_widget(
            ChatUIWidgetContribution(
                widget_id="voice_input_button",
                build=self._build_chat_button,
                order=20.0,
            )
        )
        register.register_settings_panel(
            SettingsPanelContribution(
                section_id="voice_input_settings",
                title="Voice Input",
                build=self._build_settings_panel,
                order=60.0,
            )
        )

    def shutdown(self) -> None:
        for widget in list(self._widgets):
            shutdown = getattr(widget, "shutdown", None)
            if callable(shutdown):
                shutdown()
        self._widgets.clear()
        for panel in list(self._settings_panels):
            shutdown = getattr(panel, "shutdown", None)
            if callable(shutdown):
                shutdown()
        self._settings_panels.clear()

    def _build_chat_button(self, parent: Any = None) -> Any:
        if self.context is None:
            return None
        try:
            from .ui import VoiceInputButton
        except Exception:
            try:
                from PySide6.QtWidgets import QLabel
            except Exception:
                return None
            return QLabel("语音输入加载失败", parent)
        widget = VoiceInputButton(self.context, parent)
        self._widgets.append(widget)
        return widget

    def _build_settings_panel(self, parent: Any = None) -> Any:
        if self.context is None:
            return None
        try:
            from .settings_panel import VoiceInputSettingsPanel
        except Exception:
            try:
                from PySide6.QtWidgets import QLabel
            except Exception:
                return None
            return QLabel("Voice Input 设置加载失败。", parent)
        panel = VoiceInputSettingsPanel(self.context, parent)
        self._settings_panels.append(panel)
        return panel
