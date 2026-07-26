import assert from "node:assert/strict";
import test from "node:test";

import { createPresentationVisibility } from "../pet/presentation-visibility.js";

function clock() {
  let id = 0;
  const pending = new Map();
  const captured = new Map();
  return {
    setTimer(callback, delay) {
      const token = ++id;
      const task = { callback, delay };
      pending.set(token, task);
      captured.set(token, task);
      return token;
    },
    clearTimer(token) {
      pending.delete(token);
    },
    first() {
      return pending.entries().next().value;
    },
    run(token) {
      const task = pending.get(token);
      pending.delete(token);
      task?.callback();
    },
    runCaptured(token) {
      captured.get(token)?.callback();
    },
    size() {
      return pending.size;
    },
  };
}

test("composer open to send to settled follows the real 12 second app coordination path", () => {
  const scheduler = clock();
  const composerStates = [];
  const events = [];
  const visibility = createPresentationVisibility({
    setTimer: scheduler.setTimer,
    clearTimer: scheduler.clearTimer,
    onComposerChanged: (open) => composerStates.push(open),
    onBubbleHidden: () => events.push("hidden"),
    onIdle: () => events.push("idle"),
  });

  visibility.revealComposer();
  visibility.syncPhase("thinking");
  visibility.syncPhase("typing");
  visibility.syncPhase("settled");
  const [token, task] = scheduler.first();
  assert.equal(task.delay, 12000);
  scheduler.run(token);

  assert.deepEqual(composerStates, [true, false]);
  assert.deepEqual(events, ["hidden", "idle"]);
  assert.equal(visibility.snapshot().autoHide.hidden, true);
  assert.equal(visibility.snapshot().composerOpen, false);
});

test("hover, draft, IME, restart, and dispose defer or invalidate auto hide without losing draft", () => {
  const scheduler = clock();
  let hidden = 0;
  const visibility = createPresentationVisibility({
    setTimer: scheduler.setTimer,
    clearTimer: scheduler.clearTimer,
    onBubbleHidden: () => hidden++,
  });
  visibility.revealComposer();
  visibility.syncPhase("settled");
  const [staleToken] = scheduler.first();

  visibility.setHovered(true);
  assert.equal(scheduler.size(), 0);
  visibility.setHovered(false);
  visibility.setInputState({ draft: "未发送草稿", composing: false });
  assert.equal(scheduler.size(), 0);
  assert.equal(visibility.snapshot().draft, "未发送草稿");
  visibility.setInputState({ composing: true });
  assert.equal(scheduler.size(), 0);
  visibility.setInputState({ draft: "", composing: false });
  assert.equal(scheduler.size(), 1);

  visibility.restart();
  assert.equal(scheduler.size(), 0);
  scheduler.runCaptured(staleToken);
  assert.equal(hidden, 0);
  visibility.syncPhase("settled");
  visibility.dispose();
  assert.equal(scheduler.size(), 0);
  assert.equal(visibility.snapshot().disposed, true);
});
