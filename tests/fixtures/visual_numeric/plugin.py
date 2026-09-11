import json
from pathlib import Path

class Plugin:
    def setup(self, context):
        character = context.get("sakura.host.character")
        class Service:
            def config(self, request):
                resource = request["resource"]
                relative = resource["root"] + "/" + resource["entry"]
                path = character.resolve_resource(request["characterId"], relative)
                return json.loads(Path(path).read_text(encoding="utf-8"))

            def describe(self, request):
                config = self.config(request)
                return {
                    "prompt": "Set angle between -30 and " + str(config["maxAngle"]) + "; wave once when requested.",
                    "outputSchema": {
                        "type": "object",
                        "properties": {"angle": {"type": "number", "minimum": -30, "maximum": config["maxAngle"]}, "wave": {"type": "boolean"}},
                        "additionalProperties": False,
                    },
                    "rendererData": {"maxAngle": config["maxAngle"]},
                    "parserData": config,
                }

            def parseControl(self, request, config, payload, legacy):
                if legacy is not None:
                    raise ValueError("numeric controls have no portrait compatibility")
                if not isinstance(payload, dict) or set(payload) - {"angle", "wave"}:
                    raise ValueError("invalid controls")
                angle = payload.get("angle", 0)
                if type(angle) not in (int, float) or not -30 <= angle <= config["maxAngle"]:
                    raise ValueError("angle out of range")
                if type(payload.get("wave", False)) is not bool:
                    raise ValueError("invalid action")
                return {"state": {"angle": angle}, "actions": [{"wave": True}] if payload.get("wave") else []}
            def editorData(self, resource, raw):
                return {"maxAngle": 20, **(raw or {})}

            def exportResource(self, resource, raw):
                return {"entry": "resource.json", "data": self.editorData(resource, raw)}
        context.provide("fixture.numeric.control", Service(), exports=("describe", "parseControl", "editorData", "exportResource"))
