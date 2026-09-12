import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from app.config.plugin_requirements import (
    GPT_SOVITS_MODELS, GENIE_ONNX, check_requirements,
    parse_requirements, requirements_for_manifest,
)
from app.plugins.inventory import PluginInventory


def genie_record(tmp_path):
    source = Path(__file__).resolve().parents[2] / "plugins/builtin/sakura_genie/plugin.yaml"
    plugin = tmp_path / "plugins/builtin/genie"
    plugin.mkdir(parents=True)
    shutil.copyfile(source, plugin / "plugin.yaml")
    # Compatibility discovery must never import the implementation or convert models.
    (plugin / "plugin.py").write_text('raise AssertionError("must not start Genie")')
    return PluginInventory(tmp_path).scan().records[0]


def test_genie_alone_satisfies_gpt_sovits_without_onnx(tmp_path):
    manifest = {
        "voice": {"gpt_model": "voice/model.ckpt", "sovits_model": "voice/model.pth"},
        "pluginRequirements": [{"kind": "tts", "type": GPT_SOVITS_MODELS,
                                "plugins": [{"id": "sakura.tts.gpt-sovits"}]}],
    }
    original = json.dumps(manifest)
    record = genie_record(tmp_path)
    assert set(record.tts_resources) == {GPT_SOVITS_MODELS, GENIE_ONNX}
    result = check_requirements(requirements_for_manifest(manifest), [record])
    assert result[0]["reasonCode"] == "COMPATIBLE"
    assert [item["id"] for item in result[0]["candidates"]] == ["sakura.tts.genie"]
    assert json.dumps(manifest) == original
    assert not (tmp_path / "data").exists()


def test_compatibility_distinguishes_absent_disabled_and_unsupported(tmp_path):
    requirement = [{"kind": "tts", "type": GPT_SOVITS_MODELS, "plugins": []}]
    record = genie_record(tmp_path)
    for records, expected in [
        ([], "PLUGIN_MISSING"),
        ([replace(record, desired_enabled=False)], "PLUGIN_DISABLED"),
        ([replace(record, runtime_eligible=False, supported=False)], "PLUGIN_INCOMPATIBLE"),
        ([replace(record, plugin_id="future.tts")], "COMPATIBLE"),
    ]:
        assert check_requirements(requirement, records)[0]["reasonCode"] == expected


def test_multiple_tts_types_and_hints_are_independent(tmp_path):
    hints = [{"id": "sakura.tts.genie"}] * 2
    requirements = requirements_for_manifest({"pluginRequirements": [
        {"kind": "tts", "type": GPT_SOVITS_MODELS, "plugins": hints},
        {"kind": "tts", "type": "future.voice@2", "plugins": []},
    ], "extensions": {"sakura.tts.gpt-sovits": ["private-data"]}})
    assert len(requirements[0]["plugins"]) == 1
    assert [item["reasonCode"] for item in check_requirements(requirements, [genie_record(tmp_path)])] == ["COMPATIBLE", "PLUGIN_MISSING"]
    assert requirements_for_manifest({"pluginRequirements": requirements}, include_tts=False) == []


@pytest.mark.parametrize("value", [None, {}, [{"kind": "tts", "type": "unversioned"}],
    [{"kind": "exec", "type": "custom@1"}],
    [{"kind": "tts", "type": "custom@1", "plugins": [{"id": "../run"}]}]])
def test_invalid_declaration_is_rejected(value):
    with pytest.raises(ValueError, match="CHARACTER_PLUGIN_REQUIREMENTS_INVALID"):
        parse_requirements(value)


def test_both_shared_models_and_explicit_onnx_are_declared():
    requirements = requirements_for_manifest({"extensions": {
        "sakura.tts.gpt-sovits": {"gptModel": "model.ckpt", "sovitsModel": "model.pth"},
        "sakura.tts.genie": {"onnxModelDir": "onnx"},
    }})
    assert {item["type"] for item in requirements} == {GPT_SOVITS_MODELS, GENIE_ONNX}
