from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.core_host.plugin_character import PluginCharacterStore


@pytest.mark.parametrize("provider", ["gpt-sovits", "genie", "sakuratts"])
def test_legacy_path_spelling_is_declared_as_portable_resources(tmp_path, provider):
    package = tmp_path / "characters/demo"
    (package / "voice/refs").mkdir(parents=True)
    (package / "voice/refs/ref.txt").write_text(" ./voice//neutral.wav |JA|hello|中性\n")
    for name in ("gpt.ckpt", "sovits.pth", "neutral.wav"):
        (package / "voice" / name).write_bytes(b"fixture")
    extension = {
        "toneRefs": " ./voice//refs/ref.txt ",
        "gptModel": " ./voice//gpt.ckpt ",
        "sovitsModel": " ./voice//sovits.pth ",
    }
    manifest_path = package / "character.json"
    original = {"extensions": {"sakura.tts.gpt-sovits": extension}}
    manifest_path.write_text(json.dumps(original))
    owner = f"sakura.tts.{provider}"
    store = PluginCharacterStore(tmp_path)
    store._manifest_paths["demo"] = manifest_path
    character = SimpleNamespace(
        get=lambda cid: store.get(owner, cid),
        resolve_resource=store.resolve_resource,
        declare_resources=lambda cid, declaration: store.declare_resources(owner, cid, declaration),
    )

    if provider == "gpt-sovits":
        from plugins.optional.sakura_gpt_sovits.plugin import _parse_character_voice
        voice = _parse_character_voice(character, "demo", extension)
        audio_path = voice.ref_audio_path
    elif provider == "genie":
        from plugins.optional.sakura_genie.plugin import _parse_character_voice
        voice = _parse_character_voice(character, "demo", extension, endpoint_mode="managed")
        audio_path = voice.reference("中性").ref_audio_path
    else:
        from plugins.builtin.sakura_sakuratts.plugin import character_voice
        voice = character_voice(character, "demo")
        audio_path = voice["ref_audio_path"]

    assert str(audio_path) == str(package / "voice/neutral.wav")
    saved = json.loads(manifest_path.read_text())
    declaration = saved.pop("extensionResources")[owner]
    assert saved == original
    assert set(declaration["paths"]) == {
        "voice/refs/ref.txt", "voice/gpt.ckpt", "voice/sovits.pth", "voice/neutral.wav",
    }
