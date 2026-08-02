import assert from "node:assert/strict";
import test from "node:test";

import { createPortraitController } from "../pet/portrait-controller.js";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

test("rapid A to B to C transitions only preview and commit C", async () => {
  const pending = new Map();
  const previews = [];
  const commits = [];
  const timers = [];
  const controller = createPortraitController({
    assets: { A: "a.png", B: "b.png", C: "c.png" },
    defaultKey: "A",
    loadImage(source) {
      const request = deferred();
      pending.set(source, request);
      return request.promise;
    },
    preview(value) { previews.push(value.key); },
    commit(value) { commits.push(value.key); },
    setTimer(callback, delay) { timers.push({ callback, delay }); return timers.length; },
    clearTimer() {},
  });
  controller.beginGeneration("g1");
  const first = controller.show("A", { immediate: true, generation: "g1" });
  pending.get("a.png").resolve({});
  await first;
  const b = controller.show("B", { generation: "g1" });
  const c = controller.show("C", { generation: "g1" });
  pending.get("b.png").resolve({});
  await Promise.resolve();
  pending.get("c.png").resolve({});
  await Promise.resolve();
  assert.deepEqual(previews, ["C"]);
  assert.equal(timers.at(-1).delay, 300);
  timers.at(-1).callback();
  await Promise.all([b, c]);
  assert.deepEqual(commits, ["A", "C"]);
});

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

test("same key is a no-op and preloading is reused by show", async () => {
  const loads = [];
  const timers = [];
  const controller = createPortraitController({
    assets: { A: "a.png", B: "b.png" },
    defaultKey: "A",
    async loadImage(source) { loads.push(source); return {}; },
    setTimer(callback, delay) { timers.push({ callback, delay }); return timers.length; },
    clearTimer() {},
  });
  controller.beginGeneration("g1");
  await controller.show("A", { immediate: true, generation: "g1" });
  await controller.show("A", { generation: "g1" });
  await controller.preload("B", { generation: "g1" });
  const shown = controller.show("B", { generation: "g1" });
  timers.at(-1).callback();
  await shown;
  assert.deepEqual(loads, ["a.png", "b.png"]);
});
