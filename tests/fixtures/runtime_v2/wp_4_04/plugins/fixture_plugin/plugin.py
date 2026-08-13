import time

from app.llm.prompts.types import ContextFragment
from app.plugins import (
    ContextProviderContribution,
    PluginBase,
    PluginSettingsAction,
    PluginSettingsContribution,
    PluginSettingsField,
    PromptPatchContribution,
    ToolContribution,
)


class FixturePlugin(PluginBase):
    plugin_id = "fixture_plugin"
    plugin_version = "1.0.0"

    def initialize(self, register, context):
        self.context = context
        register.register_tool(ToolContribution(
            name="fixture_echo",
            description="Echo a bounded fixture value.",
            parameters={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            handler=lambda arguments: (
                time.sleep(30) if arguments["value"] == "__hang__" else {"echo": arguments["value"]}
            ),
            group="fixture",
            risk="high",
            requires_confirmation=True,
        ))
        register.register_prompt_patch(PromptPatchContribution(
            patch_id="fixture_prompt",
            system_prompt_append="fixture prompt fact",
        ))
        register.register_context_provider(ContextProviderContribution(
            provider_id="fixture_context",
            description="Fixture context",
            build_context=lambda request: [ContextFragment(
                fragment_id="value",
                source="ignored",
                content=f"input={request.current_input}",
            )],
        ))
        register.register_plugin_settings(PluginSettingsContribution(
            section_id="general",
            title="General",
            fields=(PluginSettingsField(
                key="label",
                label="Label",
                field_type="string",
                default="fixture",
            ),),
            load=lambda: self.context.get_config(),
            save=lambda values: self.context.save_config(values),
            actions=(PluginSettingsAction(
                action_id="reset",
                label="Reset",
                handler=lambda _values: {"values": {"label": "fixture"}, "message": "reset"},
            ),),
        ))

    def on_user_message(self, event):
        config = self.context.get_config()
        config.update({
            "event_role": event.payload.get("role", ""),
            "event_characters": event.payload.get("characters", 0),
        })
        self.context.save_config(config)
