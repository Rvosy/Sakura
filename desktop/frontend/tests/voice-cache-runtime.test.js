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

test("voice cache save uses the native request envelope and commits the returned settings", async () => {
  const controls = {
    voiceCacheDirectory: input(),
    voiceCacheMaxMegabytes: input(),
    voiceCacheIdleFill: input(),
  };
  const request = { directory: "/fixture/custom-cache", maxMegabytes: 64, idleFill: true };
  const saved = { ...request, directory: "/fixture/resolved-cache", limits: [32, 20480] };
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
    idleFill: false, limits: [32, 20480] });
  controls.voiceCacheDirectory.value = " /fixture/custom-cache ";
  controls.voiceCacheMaxMegabytes.value = "64";
  controls.voiceCacheIdleFill.checked = true;
  controls.voiceCacheIdleFill.fire("change");
  assert.equal(controller.isDirty(), true);

  assert.deepEqual(await controller.save(), saved);
  assert.equal(controller.isDirty(), false);
  assert.equal(controls.voiceCacheDirectory.value, saved.directory);
  controls.voiceCacheMaxMegabytes.value = "128";
  controls.voiceCacheMaxMegabytes.fire("input");
  controller.discard();
  assert.equal(controls.voiceCacheMaxMegabytes.value, "64");
  assert.equal(controller.isDirty(), false);
});
