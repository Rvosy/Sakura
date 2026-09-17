import assert from "node:assert/strict";
import test from "node:test";
import { createScreenAwarenessController } from "../chat/screen-awareness-controller.js";

const attachmentId = `screen-${"a".repeat(32)}`;
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

function harness(plans = [], overrides = {}) {
  const calls = [];
  const sends = [];
  let idle = true;
  const controller = createScreenAwarenessController({
    generationId: () => "generation-a", isIdle: () => idle,
    invoke: async (command, args) => {
      calls.push([command, args]);
      if (overrides[command]) return overrides[command](args);
      if (command === "screen_awareness_step") return plans.shift() || { action: "none" };
      if (command === "capture_screen_awareness_frame") return { count: 2 };
      if (command === "attach_screen_awareness_batch") return { attachmentId, count: 2 };
      return true;
    },
    send: async payload => { sends.push(payload); if (overrides.send) return overrides.send(payload); },
  });
  controller.applySettings();
  return { controller, calls, sends, setIdle(value) { idle = value; } };
}

test("UI executes Core capture and submit decisions with facts and an opaque attachment", async () => {
  const env = harness([
    { action: "capture", revision: 3, resolution: "1080p", batchLimit: 6 },
    { action: "submit", revision: 3, count: 2 },
  ]);
  await env.controller.tick();
  assert.deepEqual(env.calls.filter(([name]) => name === "screen_awareness_step").map(([, args]) => args.payload), [
    { idle: true, activity: true, reset: true },
    { idle: true, activity: false, reset: false, revision: 3, count: 2 },
  ]);
  assert.deepEqual(env.sends, [{ attachmentId }]);
});

test("UI reports busy and activity even when Core chooses no action", async () => {
  const env = harness();
  env.setIdle(false);
  await env.controller.tick();
  env.controller.noteActivity();
  await env.controller.tick();
  assert.deepEqual(env.calls.filter(([name]) => name === "screen_awareness_step")[1][1].payload,
    { idle: false, activity: true, reset: false });
  assert.equal(env.calls.some(([name]) => name === "capture_screen_awareness_frame"), false);
});

test("late capture after same-generation role switch is discarded and cleared", async () => {
  const entered = deferred();
  const captured = deferred();
  const env = harness([{ action: "capture", revision: 1, resolution: "fullscreen", batchLimit: 6 }], {
    capture_screen_awareness_frame: () => { entered.resolve(); return captured.promise; },
  });
  const pending = env.controller.tick();
  await entered.promise;
  env.controller.generationChanged("generation-a");
  captured.resolve({ count: 1 });
  await pending;
  assert.equal(env.sends.length, 0);
  assert.equal(env.calls.at(-1)[0], "clear_screen_awareness_batch");
  await env.controller.tick();
  assert.equal(env.calls.at(-1)[1].payload.reset, true);
});

test("activity during attachment creation releases the attachment without a proactive send", async () => {
  const entered = deferred();
  const attached = deferred();
  const env = harness([{ action: "submit", revision: 1, count: 2 }], {
    attach_screen_awareness_batch: () => { entered.resolve(); return attached.promise; },
  });
  const pending = env.controller.tick();
  await entered.promise;
  env.controller.noteActivity();
  attached.resolve({ attachmentId, count: 2 });
  await pending;
  assert.equal(env.sends.length, 0);
  assert.ok(env.calls.some(([command, args]) => command === "release_screen_attachment" && args.payload.attachmentId === attachmentId));
});

test("send failure releases resources and requests a new Core cycle without retrying", async () => {
  const env = harness([{ action: "submit", revision: 1, count: 2 }], { send: () => { throw new Error("CHAT_FAILED"); } });
  await env.controller.tick();
  assert.equal(env.sends.length, 1);
  assert.ok(env.calls.some(([command]) => command === "release_screen_attachment"));
  await env.controller.tick();
  assert.equal(env.calls.at(-1)[1].payload.reset, true);
  assert.equal(env.sends.length, 1);
});

test("dispose rejects a late attachment and stops future steps", async () => {
  const entered = deferred();
  const attached = deferred();
  const env = harness([{ action: "submit", revision: 1, count: 2 }], {
    attach_screen_awareness_batch: () => { entered.resolve(); return attached.promise; },
  });
  const pending = env.controller.tick();
  await entered.promise;
  env.controller.dispose();
  attached.resolve({ attachmentId, count: 2 });
  await pending;
  const count = env.calls.length;
  await env.controller.tick();
  assert.equal(env.calls.length, count);
  assert.equal(env.sends.length, 0);
  assert.ok(env.calls.some(([command]) => command === "release_screen_attachment"));
});
