import assert from "node:assert/strict";
import test from "node:test";

import {
  createChatTimingController,
  validateChatTimingSnapshot,
} from "../settings/chat-timing-runtime.js";

function snapshot(values = {
  subtitleTypingIntervalMs: 28,
  replySegmentPauseMs: 160,
  silentSegmentPauseMs: 2000,
}) {
  return {
    schemaVersion: 1,
    windowGeneration: 4,
    values,
    limits: {
      subtitleTypingIntervalMs: [5, 200, 28],
      replySegmentPauseMs: [0, 3000, 160],
      silentSegmentPauseMs: [0, 10000, 2000],
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
  assert.throws(() => validateChatTimingSnapshot(snapshot({
    subtitleTypingIntervalMs: 4,
    replySegmentPauseMs: 160,
    silentSegmentPauseMs: 2000,
  })));
  assert.throws(() => validateChatTimingSnapshot(snapshot({
    subtitleTypingIntervalMs: 28,
    replySegmentPauseMs: 160,
    silentSegmentPauseMs: 10001,
  })));
  assert.throws(() => validateChatTimingSnapshot({ ...snapshot(), windowGeneration: 0 }));
});

test("failed timing save retains the committed baseline and dirty draft", async () => {
  const controls = {
    subtitleTypingInterval: input(),
    replySegmentPause: input(),
    silentSegmentPause: input(),
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

for (const value of ["", "3001", "160.5"]) {
  test(`incomplete or invalid timing edit ${JSON.stringify(value)} stays dirty until corrected or discarded`, async () => {
    const controls = {
      subtitleTypingInterval: input(),
      replySegmentPause: input(),
      silentSegmentPause: input(),
    };
    const calls = [];
    let dirtyCalls = 0;
    const controller = createChatTimingController({
      document: { getElementById: (id) => controls[id] },
      invoke: async (command, args) => {
        calls.push([command, args]);
        return args.values;
      },
      onDirty: () => { dirtyCalls += 1; },
    });
    controller.initialize(snapshot());
    controls.replySegmentPause.value = value;
    assert.doesNotThrow(() => controls.replySegmentPause.fire("input"));
    assert.equal(controller.isDirty(), true);
    assert.equal(dirtyCalls, 2);
    await assert.rejects(controller.save());
    assert.equal(calls.length, 0, "invalid values never reach persistence");
    assert.equal(controls.replySegmentPause.value, value);
    assert.equal(controller.isDirty(), true);

    controls.replySegmentPause.value = "160";
    controls.replySegmentPause.fire("input");
    assert.equal(controller.isDirty(), false, "restoring the original value clears dirty state");
    controls.replySegmentPause.value = "0";
    controls.replySegmentPause.fire("input");
    await controller.save();
    assert.deepEqual(calls[0], ["settings_chat_presentation_timing_save", {
      windowGeneration: 4,
      values: { subtitleTypingIntervalMs: 28, replySegmentPauseMs: 0, silentSegmentPauseMs: 2000 },
    }]);
    assert.equal(controller.isDirty(), false);

    controls.replySegmentPause.value = value;
    controls.replySegmentPause.fire("input");
    assert.equal(controller.isDirty(), true, "an empty edit differs from the valid zero baseline");
    controller.discard();
    assert.equal(controls.replySegmentPause.value, "0");
    assert.equal(controller.isDirty(), false);
  });
}
