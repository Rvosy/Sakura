import assert from "node:assert/strict";
import test from "node:test";

import { createTypewriter } from "../pet/typewriter.js";

function clock() {
  let id = 0;
  const pending = new Map();
  return {
    setTimer(callback) { const token = ++id; pending.set(token, callback); return token; },
    clearTimer(token) { pending.delete(token); },
    runAll(limit = 100) { let count = 0; while (pending.size) { const [token, callback] = pending.entries().next().value; pending.delete(token); callback(); assert.ok(++count < limit); } },
    takeFirst() { const [token, callback] = pending.entries().next().value || []; if (token) pending.delete(token); return callback; },
    size() { return pending.size; },
  };
}

test("complete reply is typed across segments and reports the active portrait", () => {
  const scheduler = clock();
  const texts = [];
  const portraits = [];
  const completions = [];
  const writer = createTypewriter({ setTimer: scheduler.setTimer, clearTimer: scheduler.clearTimer, onText: (value) => texts.push(value), onSegment: (segment) => portraits.push(segment.portrait), onComplete: (result) => completions.push(result) });
  writer.start([{ text: "樱花", portrait: "idle" }, { text: "Sakura", portrait: "smile" }]);
  scheduler.runAll();
  assert.equal(texts.at(-1), "樱花\nSakura");
  assert.deepEqual(portraits, ["idle", "smile"]);
  assert.deepEqual(completions, [{ skipped: false }]);
});

test("skip reveals the full reply without invoking a Core cancellation path", () => {
  const scheduler = clock();
  const texts = [];
  const completions = [];
  const writer = createTypewriter({ setTimer: scheduler.setTimer, clearTimer: scheduler.clearTimer, onText: (value) => texts.push(value), onComplete: (result) => completions.push(result) });
  writer.start([{ text: "abcdef" }, { text: "第二段" }]);
  assert.equal(writer.skip(), true);
  assert.equal(texts.at(-1), "abcdef\n第二段");
  assert.deepEqual(completions, [{ skipped: true }]);
  assert.equal(scheduler.size(), 0);
  assert.equal(writer.skip(), false);
});

test("cancel invalidates a captured late callback", () => {
  const scheduler = clock();
  const texts = [];
  const writer = createTypewriter({ setTimer: scheduler.setTimer, clearTimer: scheduler.clearTimer, onText: (value) => texts.push(value) });
  writer.start([{ text: "late" }]);
  const late = scheduler.takeFirst();
  writer.cancel("已取消");
  late();
  assert.equal(texts.at(-1), "已取消");
});
