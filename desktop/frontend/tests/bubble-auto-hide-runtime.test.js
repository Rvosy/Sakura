import assert from "node:assert/strict";
import test from "node:test";

import {
  createBubbleAutoHideSettingsController,
  validateBubbleAutoHideSnapshot,
} from "../settings/bubble-auto-hide-runtime.js";

function control() {
  const listeners = {};
  return {
    checked: false,
    disabled: false,
    value: "",
    min: "",
    max: "",
    addEventListener(name, listener) { listeners[name] = listener; },
    fire(name) { listeners[name]?.(); },
  };
}

function snapshot() {
  return {
    schemaVersion: 1,
    windowGeneration: 7,
    values: { autoHideEnabled: true, autoHideDelaySeconds: 5 },
    limits: { autoHideDelaySeconds: [1, 120, 5] },
  };
}

test("bubble auto-hide snapshot is generation-scoped and bounded", () => {
  assert.equal(validateBubbleAutoHideSnapshot(snapshot()).values.autoHideDelaySeconds, 5);
  assert.throws(() => validateBubbleAutoHideSnapshot({ ...snapshot(), windowGeneration: 0 }));
  assert.throws(() => validateBubbleAutoHideSnapshot({
    ...snapshot(),
    values: { autoHideEnabled: true, autoHideDelaySeconds: 121 },
  }));
});

test("failed save keeps the bubble auto-hide draft dirty", async () => {
  const controls = { bubbleAutoHide: control(), bubbleAutoHideDelay: control() };
  const controller = createBubbleAutoHideSettingsController({
    document: { getElementById: (id) => controls[id] },
    invoke: async () => { throw new Error("WRITE_FAILED"); },
    onDirty: () => {},
  });
  controller.initialize(snapshot());
  controls.bubbleAutoHide.checked = false;
  controls.bubbleAutoHide.fire("change");
  assert.equal(controls.bubbleAutoHideDelay.disabled, true);
  assert.equal(controller.isDirty(), true);
  await assert.rejects(() => controller.save(), /WRITE_FAILED/);
  assert.equal(controller.isDirty(), true);
  controller.discard();
  assert.equal(controls.bubbleAutoHide.checked, true);
  assert.equal(controls.bubbleAutoHideDelay.value, "5");
});
