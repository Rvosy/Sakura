import assert from "node:assert/strict";
import test from "node:test";

import { createPortraitController } from "../pet/portrait-controller.js";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

test("late image load cannot replace a newer portrait request", async () => {
  const loads = new Map();
  const commits = [];
  const controller = createPortraitController({ assets: { idle: "idle.svg", thinking: "thinking.svg" }, loadImage: (source) => { const load = deferred(); loads.set(source, load); return load.promise; }, commit: ({ key }) => commits.push(key), reducedMotion: true });
  const first = controller.show("idle");
  const second = controller.show("thinking");
  loads.get("thinking.svg").resolve({ width: 240, height: 336 });
  loads.get("idle.svg").resolve({ width: 240, height: 336 });
  assert.equal((await second).applied, true);
  assert.equal((await first).applied, false);
  assert.deepEqual(commits, ["thinking"]);
});

test("decode failure exposes fallback without clearing the current safe key", async () => {
  const fallbacks = [];
  const controller = createPortraitController({ assets: { idle: "missing.svg" }, loadImage: async () => { throw new Error("decode"); }, showFallback: (value) => fallbacks.push(value) });
  const result = await controller.show("unknown");
  assert.equal(result.failed, true);
  assert.equal(result.key, "idle");
  assert.equal(fallbacks[0].source, "missing.svg");
});

test("a new request settles an interrupted transition promise", async () => {
  let timerCallback;
  const commits = [];
  const controller = createPortraitController({ assets: { idle: "idle.svg", smile: "smile.svg" }, loadImage: async () => ({ width: 1, height: 1 }), commit: ({ key }) => commits.push(key), setTimer: (callback) => { timerCallback = callback; return 1; }, clearTimer: () => {} });
  await controller.show("idle", { immediate: true });
  const interrupted = controller.show("smile");
  await Promise.resolve();
  const latest = controller.show("idle");
  assert.equal((await interrupted).applied, false);
  timerCallback?.();
  assert.equal((await latest).applied, true);
  assert.equal(commits.at(-1), "idle");
});
