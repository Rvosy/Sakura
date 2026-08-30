import assert from "node:assert/strict";
import test from "node:test";

import {
  SCREEN_AWARENESS_PROMPT,
  createScreenAwarenessController,
} from "../chat/screen-awareness-controller.js";

function settings(overrides = {}) {
  return {
    enabled: true,
    checkIntervalMinutes: 1,
    cooldownMinutes: 2,
    batchLimit: 3,
    resolution: "1080p",
    ...overrides,
  };
}

function harness({ enabled = true } = {}) {
  let clock = 0;
  let idle = true;
  let generation = "generation-a";
  let captureCount = 0;
  let sendFailure = false;
  const calls = [];
  const sends = [];
  const controller = createScreenAwarenessController({
    now: () => clock,
    generationId: () => generation,
    isIdle: () => idle,
    invoke: async (command, args) => {
      calls.push([command, args]);
      if (command === "capture_screen_awareness_frame") return { count: ++captureCount, droppedCount: 0 };
      if (command === "attach_screen_awareness_batch") {
        return { attachmentId: `screen-${"a".repeat(32)}`, count: captureCount };
      }
      return true;
    },
    send: async (payload) => {
      sends.push(payload);
      if (sendFailure) throw new Error("CHAT_FAILED");
      return { operationId: "op-1" };
    },
    setInterval: () => 1,
    clearInterval: () => {},
  });
  controller.applySettings(settings({ enabled }));
  return {
    controller,
    calls,
    sends,
    setClock: (value) => { clock = value; },
    setIdle: (value) => { idle = value; },
    setGeneration: (value) => { generation = value; },
    failSend: () => { sendFailure = true; },
    resetCaptures: () => { captureCount = 0; },
  };
}

function commands(env, name) {
  return env.calls.filter(([command]) => command === name);
}

test("disabled screen awareness never captures", async () => {
  const env = harness({ enabled: false });
  env.setClock(10 * 60_000);
  await env.controller.tick();
  assert.equal(commands(env, "capture_screen_awareness_frame").length, 0);
});

test("capture interval and first-frame cooldown produce one ordered ordinary chat send", async () => {
  const env = harness();
  env.setClock(60_000);
  await env.controller.tick();
  env.setClock(120_000);
  await env.controller.tick();
  assert.equal(env.sends.length, 0);
  env.setClock(180_000);
  await env.controller.tick();

  assert.equal(commands(env, "capture_screen_awareness_frame").length, 3);
  assert.equal(commands(env, "attach_screen_awareness_batch").length, 1);
  assert.deepEqual(env.sends, [{
    message: SCREEN_AWARENESS_PROMPT,
    attachmentId: `screen-${"a".repeat(32)}`,
  }]);
});

test("busy state, fresh input, and long sleep skip work without catch-up", async () => {
  const env = harness();
  env.setClock(60_000);
  env.setIdle(false);
  await env.controller.tick();
  assert.equal(commands(env, "capture_screen_awareness_frame").length, 0);

  env.setIdle(true);
  env.controller.noteActivity();
  env.setClock(119_999);
  await env.controller.tick();
  assert.equal(commands(env, "capture_screen_awareness_frame").length, 0);

  env.setClock(8 * 60 * 60_000);
  await env.controller.tick();
  assert.equal(commands(env, "capture_screen_awareness_frame").length, 1);
  await env.controller.tick();
  assert.equal(commands(env, "capture_screen_awareness_frame").length, 1);
});

test("manual send, hot settings, generation change, and dispose clear the native batch", async () => {
  const env = harness();
  const before = commands(env, "clear_screen_awareness_batch").length;
  env.controller.noteManualSend();
  env.controller.applySettings(settings({ resolution: "720p" }));
  env.setGeneration("generation-b");
  await env.controller.tick();
  env.controller.dispose();
  assert.equal(commands(env, "clear_screen_awareness_batch").length, before + 4);
});

test("failed automatic send releases the attachment and does not retry", async () => {
  const env = harness();
  env.failSend();
  env.setClock(60_000);
  await env.controller.tick();
  env.setClock(180_000);
  await env.controller.tick();
  await Promise.resolve();
  assert.equal(env.sends.length, 1);
  assert.equal(commands(env, "release_screen_attachment").length, 1);
  await env.controller.tick();
  assert.equal(env.sends.length, 1);
});
