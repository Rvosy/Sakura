"""Portable character requirements and read-only installed-plugin compatibility.

Plugin hints guide installation; resource capabilities decide compatibility.
This check never starts a model service, converts files, or chooses a provider.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

from app.plugins.visuals import RESOURCE_TYPE_PATTERN

_PLUGIN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
GPT_SOVITS_MODELS = "gpt-sovits.models@1"
GENIE_ONNX = "genie.onnx@1"


def parse_requirements(value):
    if not isinstance(value, list) or len(value) > 64:
        raise ValueError("CHARACTER_PLUGIN_REQUIREMENTS_INVALID")
    result = []
    for item in value:
        if (not isinstance(item, Mapping) or set(item) - {"kind", "type", "plugins"}
            or not isinstance(item.get("kind"), str) or item["kind"] not in {"visual", "tts"}
            or not isinstance(item.get("type"), str) or not RESOURCE_TYPE_PATTERN.fullmatch(item["type"])):
            raise ValueError("CHARACTER_PLUGIN_REQUIREMENTS_INVALID")
        hints = item.get("plugins", [])
        if not isinstance(hints, list) or len(hints) > 16:
            raise ValueError("CHARACTER_PLUGIN_REQUIREMENTS_INVALID")
        plugins = []
        for hint in hints:
            if (not isinstance(hint, Mapping) or set(hint) - {"id", "name"}
                or not isinstance(hint.get("id"), str) or not _PLUGIN_ID.fullmatch(hint["id"])
                or not isinstance(hint.get("name", ""), str) or len(hint.get("name", "")) > 120
                or any(ord(char) < 32 for char in hint.get("name", ""))):
                raise ValueError("CHARACTER_PLUGIN_REQUIREMENTS_INVALID")
            plugins.append({"id": hint["id"], **({"name": hint["name"]} if hint.get("name") else {})})
        result.append({"kind": item["kind"], "type": item["type"], "plugins": plugins})
    return result


def tts_resource_types(value):
    if (not isinstance(value, list) or len(value) > 32
        or any(not isinstance(item, str) or not RESOURCE_TYPE_PATTERN.fullmatch(item) for item in value)):
        raise ValueError("TTS_RESOURCE_MANIFEST_INVALID")
    return tuple(dict.fromkeys(value))


def requirements_for_manifest(manifest, *, include_tts=True):
    requirements = parse_requirements(manifest.get("pluginRequirements", []))
    visuals = manifest.get("visuals") or {}
    visual_types = set()
    for resource in visuals.get("resources", []):
        visual_types.add(resource["type"])
        requirements.extend(parse_requirements(resource.get("pluginRequirements", [])))
        provider = visuals.get("providers", {}).get(resource["id"])
        requirements.append({"kind": "visual", "type": resource["type"], "plugins": [{"id": provider}] if provider else []})
    if "visuals" not in manifest and isinstance(manifest.get("portrait"), Mapping):
        visual_types.add("sakura.visual.portrait@1")
        requirements.append({"kind": "visual", "type": "sakura.visual.portrait@1", "plugins": [{"id": "sakura.portrait", "name": "立绘"}]})
    if include_tts:
        mapping = lambda value: value if isinstance(value, Mapping) else {}
        voice = mapping(manifest.get("voice"))
        extensions = mapping(manifest.get("extensions"))
        shared = mapping(extensions.get("sakura.tts.gpt-sovits"))
        # This is the existing shared model-resource contract, not a demand to
        # install its namesake provider. Genie can consume it via conversion.
        if (shared.get("gptModel") or voice.get("gpt_model")) and (shared.get("sovitsModel") or voice.get("sovits_model")):
            requirements.append({"kind": "tts", "type": GPT_SOVITS_MODELS, "plugins": [
                {"id": "sakura.tts.gpt-sovits", "name": "GPT-SoVITS"}, {"id": "sakura.tts.genie", "name": "Genie"}]})
        if mapping(extensions.get("sakura.tts.genie")).get("onnxModelDir"):
            requirements.append({"kind": "tts", "type": GENIE_ONNX, "plugins": [{"id": "sakura.tts.genie", "name": "Genie"}]})
    merged = {}
    for item in requirements:
        if item["kind"] == "visual" and item["type"] not in visual_types:
            continue
        if item["kind"] == "tts" and not include_tts:
            continue
        key = item["kind"], item["type"]
        target = merged.setdefault(key, {"kind": key[0], "type": key[1], "plugins": []})
        known = {hint["id"] for hint in target["plugins"]}
        for hint in item["plugins"]:
            if hint["id"] not in known:
                target["plugins"].append(hint)
                known.add(hint["id"])
    return parse_requirements(list(merged.values()))


def check_requirements(requirements, records):
    results = []
    for item in parse_requirements(requirements):
        def supports(record):
            return item["type"] in (
                [cap.resource_type for cap in record.visuals]
                if item["kind"] == "visual" else record.tts_resources)

        hinted_ids = {hint["id"] for hint in item["plugins"]}
        candidates = [record for record in records if supports(record) or record.plugin_id in hinted_ids]
        # A valid user installation supersedes a bundled/duplicate record.
        eligible_ids = {record.plugin_id for record in candidates if record.runtime_eligible}
        candidates = [record for record in candidates if record.runtime_eligible or record.plugin_id not in eligible_ids]
        compatible = [record for record in candidates if supports(record) and record.runtime_eligible]
        if any(record.desired_enabled for record in compatible):
            reason = "COMPATIBLE"
        elif compatible:
            reason = "PLUGIN_DISABLED"
        else:
            reason = "PLUGIN_INCOMPATIBLE" if candidates else "PLUGIN_MISSING"
        results.append({**item, "reasonCode": reason, "candidates": [
            {"id": record.plugin_id, "name": record.name, "enabled": record.desired_enabled,
             "compatible": supports(record) and record.runtime_eligible, "installId": record.install_id} for record in candidates]})
    return results
