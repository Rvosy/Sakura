import time


class FixturePlugin:
    def setup(self, context):
        context.get("sakura.host.tools").register(
            {
                "name": "fixture_echo",
                "description": "Echo a bounded fixture value.",
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
                "group": "fixture",
                "risk": "high",
            },
            _fixture_tool,
        )
        context.get("sakura.host.context").register(
            {
                "providerId": "fixture_context",
                "description": "Fixture context",
                "order": 100,
                "enabled": True,
            },
            lambda request: [{
                "content": f"input={request['current_input']}",
                "priority": 50,
                "budgetHint": 512,
                "label": "Fixture",
            }],
        )
        context.get("sakura.host.settings").register(
            {
                "sectionId": "general",
                "title": "General",
                "fields": [{
                    "key": "label",
                    "label": "Label",
                    "type": "text",
                    "default": "fixture",
                }],
                "actions": [{
                    "actionId": "reset",
                    "label": "Reset",
                }],
            },
            load=context.config.get,
            save=context.config.update,
            actions={
                "reset": lambda _values: {
                    "values": {"label": "fixture"},
                    "message": "reset",
                },
            },
        )
        context.on("sakura.host.message.received", lambda payload: _record_event(context, payload))


def _record_event(context, payload):
    context.config.update({
        "event_role": payload.get("role", ""),
        "event_characters": payload.get("characters", 0),
    })


def _fixture_tool(arguments):
    value = arguments["value"]
    if value == "__hang__":
        time.sleep(30)
    if value == "__error__":
        raise OSError(r"private fixture path C:\\secret\\browser.exe")
    return {"echo": value}
