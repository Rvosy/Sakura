from __future__ import annotations

import json
import posixpath
from pathlib import Path, PurePosixPath, PureWindowsPath




DEFAULT_KEY = "__default__"


def _relative(value):
    if (not isinstance(value, str) or not value or "\\" in value
        or PurePosixPath(value).is_absolute() or PureWindowsPath(value).drive
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or any(char in value for char in ':\x00')):
        raise ValueError("VISUAL_RESOURCE_INVALID")
    return value


def _legacy_path(value):
    if not isinstance(value, str):
        raise ValueError("VISUAL_RESOURCE_INVALID")
    raw = value.strip().strip('"').strip("'").replace("\\", "/")
    if PurePosixPath(raw).is_absolute() or PureWindowsPath(raw).drive:
        raise ValueError("VISUAL_RESOURCE_INVALID")
    return _relative(posixpath.normpath(raw))


def portrait_configuration(value, *, legacy=False):
    if not isinstance(value, dict) or "expressionRows" in value:
        raise ValueError("VISUAL_RESOURCE_INVALID")
    path_value = _legacy_path if legacy else _relative
    default = path_value(value.get("default"))
    expressions = value.get("expressions") or {}
    if not isinstance(expressions, dict) or len(expressions) > 63:
        raise ValueError("VISUAL_RESOURCE_INVALID")
    assets = {DEFAULT_KEY: default}
    for label, path in expressions.items():
        if not isinstance(label, str) or not label.strip() or len(label) > 256 or label == DEFAULT_KEY:
            raise ValueError("VISUAL_RESOURCE_INVALID")
        if any(ord(char) < 32 for char in label) or label.strip() in assets:
            raise ValueError("VISUAL_RESOURCE_INVALID")
        assets[label.strip()] = path_value(path)
    return assets


def inspect_png(path):
    size = path.stat().st_size
    if not 33 <= size <= 8 * 1024 * 1024:
        raise ValueError("VISUAL_RESOURCE_INVALID")
    with path.open("rb") as stream:
        header = stream.read(33)
    if header[:16] != b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR':
        raise ValueError("VISUAL_RESOURCE_INVALID")
    width, height = int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")
    if not 0 < width <= 8192 or not 0 < height <= 8192 or width * height > 40_000_000:
        raise ValueError("VISUAL_RESOURCE_INVALID")
    return {"width": width, "height": height, "byteLength": size}


class PortraitService:
    def __init__(self, character, logger):
        self.character = character
        self.logger = logger

    def describe(self, request):
        try:
            return self._describe(request)
        except (ValueError, OSError, RuntimeError):
            self.logger.warning("立绘资源无法加载", fields={"reason_code": "VISUAL_RESOURCE_INVALID", "stage": "visual.describe"})
            return {"error": "VISUAL_RESOURCE_INVALID"}

    def _describe(self, request):
        resource = request["resource"]
        prefix = "" if resource["root"] == "." else resource["root"] + "/"
        entry = Path(self.character.resolve_resource(request["characterId"], prefix + resource["entry"]))
        if entry.stat().st_size > 256 * 1024:
            raise ValueError("VISUAL_RESOURCE_INVALID")
        try:
            config = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError("VISUAL_RESOURCE_INVALID") from error
        # Old character.json is interpreted here, without rewriting the package.
        legacy = resource["root"] == "." and resource["entry"] == "character.json"
        if legacy:
            config = config.get("portrait") if isinstance(config, dict) else None
        paths = portrait_configuration(config, legacy=legacy)
        assets, metadata = {}, {}
        for key, relative in paths.items():
            relative = prefix + relative
            path = Path(self.character.resolve_resource(request["characterId"], relative))
            metadata[key] = inspect_png(path)
            assets[key] = relative
        choices = list(paths)[1:]
        return {
            "prompt": "立绘控制：在 control.payload.key 中填写图片标签。可选标签："
                + "、".join([DEFAULT_KEY, *choices])
                + "。按本段内容选择，缺省使用默认图。",
            "outputSchema": {"type": "object", "properties": {"key": {"type": "string", "enum": [DEFAULT_KEY, *choices]}}, "required": ["key"], "additionalProperties": False},
            "rendererData": {"defaultKey": DEFAULT_KEY, "keys": list(paths), "metadata": metadata},
            "parserData": {"keys": list(paths)},
            "assets": assets,
        }

    def editorData(self, resource, raw):
        legacy = resource["root"] == "." and resource["entry"] == "character.json"
        raw = raw.get("portrait", {}) if legacy and isinstance(raw, dict) else raw
        config = dict(raw) if isinstance(raw, dict) else {}
        config.setdefault("default", "")
        config.setdefault("expressions", {})
        if legacy:
            config["default"] = _legacy_path(config["default"]) if config["default"] else ""
            config["expressions"] = {key: _legacy_path(path) for key, path in config["expressions"].items()}
        return config

    def exportResource(self, resource, raw):
        return {"entry": "resource.json", "data": self.editorData(resource, raw)}

    def previewImage(self, resource, raw):
        return self.editorData(resource, raw).get("default") or None

    def parseControl(self, request, parserData, payload, legacy):
        keys = parserData["keys"]
        if payload is not None:
            if not isinstance(payload, dict) or set(payload) - {"key"}:
                raise ValueError("VISUAL_CONTROL_INVALID")
            explicit = payload.get("key")
        else:
            explicit = (legacy or {}).get("portrait")
        tone = request.get("segment", {}).get("tone") or (legacy or {}).get("tone")
        explicit = explicit.strip() if isinstance(explicit, str) else ""
        tone = tone.strip() if isinstance(tone, str) else ""
        key = explicit if explicit in keys else tone if tone in keys else DEFAULT_KEY
        return {"state": {"key": key}, "actions": []}


class PortraitPlugin:
    def setup(self, context):
        context.provide("sakura.visual.portrait", PortraitService(context.get("sakura.host.character"), context.get("sakura.host.logging")), exports=("describe", "parseControl", "editorData", "exportResource", "previewImage"))
