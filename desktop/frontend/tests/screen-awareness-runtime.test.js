import assert from "node:assert/strict";
import test from "node:test";

import {
  createScreenAwarenessSettingsController,
  validateScreenAwarenessSnapshot,
} from "../settings/screen-awareness-runtime.js";

function snapshot(settings = {}) {
  return {
    schemaVersion: 1,
    settings: {
      // Tauri/serde_json may project map keys in lexical order. Dirty tracking must compare
      // values rather than depending on the insertion order received from the transport.
      batchLimit: 6,
      checkIntervalMinutes: 20,
      cooldownMinutes: 10,
      enabled: true,
      resolution: "fullscreen",
      ...settings,
    },
    windowGeneration: 3,
    coreGenerationId: "generation-a",
  };
}

function control() {
  const listeners = {};
  return {
    value: "",
    checked: false,
    disabled: false,
    min: "",
    max: "",
    addEventListener(name, handler) { listeners[name] = handler; },
    fire(name) { listeners[name]?.(); },
  };
}

test("screen awareness settings are exact and bounded", () => {
  assert.equal(validateScreenAwarenessSnapshot(snapshot()).settings.checkIntervalMinutes, 20);
  assert.throws(() => validateScreenAwarenessSnapshot(snapshot({ batchLimit: 21 })));
  assert.throws(() => validateScreenAwarenessSnapshot({ ...snapshot(), privatePath: "x" }));
});

test("screen awareness settings save both preserves identity and rebases immediately", async () => {
  const controls = {
    enabled: control(),
    checkInterval: control(),
    cooldown: control(),
    batchLimit: control(),
    screenResolution: control(),
  };
  const calls = [];
  const controller = createScreenAwarenessSettingsController({
    document: { getElementById: (id) => controls[id] },
    onDirty: () => {},
    invoke: async (command, args) => {
      calls.push([command, args]);
      return snapshot({ ...args.settings, resolution: args.settings.resolution });
    },
  });
  controller.initialize(snapshot());
  assert.equal(controller.isDirty(), false);
  controls.checkInterval.value = "25";
  controls.checkInterval.fire("input");
  assert.equal(controller.isDirty(), true);
  await controller.save();
  assert.equal(calls[0][0], "settings_screen_awareness_save");
  assert.equal(calls[0][1].windowGeneration, 3);
  assert.equal(calls[0][1].coreGenerationId, "generation-a");
  assert.equal(calls[0][1].settings.checkIntervalMinutes, 25);
  assert.equal(controller.isDirty(), false);
});
