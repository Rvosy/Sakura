import assert from "node:assert/strict";
import test from "node:test";

import {
  createChatTimingController,
  validateChatTimingSnapshot,
} from "../settings/chat-timing-runtime.js";

function snapshot(values = { subtitleTypingIntervalMs: 28, replySegmentPauseMs: 160 }) {
  return {
    schemaVersion: 1,
    windowGeneration: 4,
    values,
    limits: {
      subtitleTypingIntervalMs: [5, 200, 28],
      replySegmentPauseMs: [0, 3000, 160],
    },
  };
}

function input() {
  const listeners = {};
  return {
    value: "",
    min: "",
    max: "",
    addEventListener(name, listener) { listeners[name] = listener; },
    fire(name) { listeners[name]?.(); },
  };
}

test("timing snapshot is exact, bounded, and generation-scoped", () => {
  assert.equal(validateChatTimingSnapshot(snapshot()).windowGeneration, 4);
  assert.throws(() => validateChatTimingSnapshot(snapshot({ subtitleTypingIntervalMs: 4, replySegmentPauseMs: 160 })));
  assert.throws(() => validateChatTimingSnapshot({ ...snapshot(), windowGeneration: 0 }));
});

test("failed timing save retains the committed baseline and dirty draft", async () => {
  const controls = {
    subtitleTypingInterval: input(),
    replySegmentPause: input(),
  };
  let dirtyCalls = 0;
  const controller = createChatTimingController({
    document: { getElementById: (id) => controls[id] },
    invoke: async () => { throw new Error("WRITE_FAILED"); },
    onDirty: () => { dirtyCalls += 1; },
  });
  controller.initialize(snapshot());
  controls.subtitleTypingInterval.value = "41";
  controls.subtitleTypingInterval.fire("input");
  assert.equal(controller.isDirty(), true);
  await assert.rejects(() => controller.save(), /WRITE_FAILED/);
  assert.equal(controller.isDirty(), true);
  controller.discard();
  assert.equal(controls.subtitleTypingInterval.value, "28");
  assert.equal(controller.isDirty(), false);
  assert.ok(dirtyCalls >= 3);
});
