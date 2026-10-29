import assert from "node:assert/strict";
import test from "node:test";

import { createPortraitController } from "../../../plugins/builtin/sakura_portrait/frontend/renderer.js";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

test("decode failure keeps an already committed portrait visible", async () => {
  const fallbacks = [];
  const commits = [];
  const controller = createPortraitController({
    assets: { A: "a.png", B: "b.png" },
    defaultKey: "A",
    loadImage: async (source) => {
      if (source === "b.png") throw new Error("decode failed");
      return {};
    },
    commit(value) { commits.push(value.key); },
    showFallback(value) { fallbacks.push(value.key); },
  });
  controller.beginGeneration("g1");
  await controller.show("A", { immediate: true, generation: "g1" });
  const result = await controller.show("B", { generation: "g1" });
  assert.equal(result.failed, true);
  assert.deepEqual(commits, ["A"]);
  assert.deepEqual(fallbacks, []);
  assert.equal(controller.current(), "A");
});

test("visual readiness follows the transition start and still releases text on decode failure", async () => {
  const pending = deferred();
  const events = [];
  const timers = [];
  const controller = createPortraitController({
    assets: { A: "a.png", B: "b.png", C: "c.png" },
    defaultKey: "A",
    loadImage(source) {
      if (source === "b.png") return pending.promise;
      if (source === "c.png") throw new Error("decode failed");
      return {};
    },
    preview() { events.push("preview"); },
    commit() {},
    setTimer(callback, delay) { timers.push({ callback, delay }); return timers.length; },
    clearTimer() {},
  });
  controller.beginGeneration("g1");
  await controller.show("A", { immediate: true, generation: "g1" });

  const shown = controller.show("B", {
    generation: "g1",
    onVisualReady() { events.push("text"); },
  });
  assert.deepEqual(events, []);
  pending.resolve({});
  await Promise.resolve();
  assert.deepEqual(events, ["preview", "text"]);
  timers.at(-1).callback();
  await shown;

  await controller.show("C", {
    generation: "g1",
    onVisualReady() { events.push("failed-text"); },
  });
  assert.deepEqual(events, ["preview", "text", "failed-text"]);
});

test("portrait image failures keep the original exception for host diagnostics", async () => {
  const failure = new DOMException("PNG decoder rejected image", "EncodingError");
  const errors = [];
  const controller = createPortraitController({ assets: { A: "a.png" }, defaultKey: "A",
    loadImage: async () => { throw failure; }, reportError: error => errors.push(error),
  });
  controller.beginGeneration("g");
  assert.equal((await controller.show("A", { generation: "g" })).failed, true);
  assert.equal(errors[0].error, failure);
  assert.equal(errors[0].code, "PORTRAIT_DECODE_FAILED");
  controller.dispose();
});
