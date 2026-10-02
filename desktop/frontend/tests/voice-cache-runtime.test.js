import assert from "node:assert/strict";
import test from "node:test";

import { createVoiceCacheController } from "../settings/voice-cache-runtime.js";

function input() {
  const listeners = new Map();
  return {
    value: "", checked: false,
    addEventListener(name, listener) { listeners.set(name, listener); },
    fire(name) { listeners.get(name)?.(); },
  };
}

for (const capacity of [1, 32768]) {
  test(`voice cache saves ${capacity} MB through the native request envelope`, async () => {
    const controls = {
      voiceCacheDirectory: input(),
      voiceCacheMaxMegabytes: input(),
      voiceCacheIdleFill: input(),
    };
    const request = { directory: "/fixture/custom-cache", maxMegabytes: capacity, idleFill: true };
    const saved = { ...request, directory: "/fixture/resolved-cache" };
    const controller = createVoiceCacheController({
      document: { getElementById: (id) => controls[id] },
      onDirty() {},
      invoke: async (command, args) => {
        assert.equal(command, "settings_voice_cache_save");
        assert.deepEqual(args, { request });
        return saved;
      },
    });
    controller.initialize({ directory: "/fixture/default-cache", maxMegabytes: 512,
      idleFill: false });
    controls.voiceCacheDirectory.value = " /fixture/custom-cache ";
    controls.voiceCacheMaxMegabytes.value = String(capacity);
    controls.voiceCacheIdleFill.checked = true;
    controls.voiceCacheIdleFill.fire("change");
    assert.equal(controller.isDirty(), true);

    assert.deepEqual(await controller.save(), saved);
    assert.equal(controller.isDirty(), false);
    assert.equal(controls.voiceCacheDirectory.value, saved.directory);
    controls.voiceCacheMaxMegabytes.value = "128";
    controls.voiceCacheMaxMegabytes.fire("input");
    controller.discard();
    assert.equal(controls.voiceCacheMaxMegabytes.value, String(capacity));
    assert.equal(controller.isDirty(), false);
  });
}
