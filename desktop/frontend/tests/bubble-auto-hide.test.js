import assert from "node:assert/strict";
import test from "node:test";

import { createBubbleAutoHide } from "../pet/bubble-auto-hide.js";

test("bubble hides only after settled, pauses on hover, and reappears for interaction", () => {
  let timer = null;
  let hidden = 0;
  let shown = 0;
  const controller = createBubbleAutoHide({ setTimer: (callback) => { timer = callback; return 1; }, clearTimer: () => { timer = null; }, onHidden: () => hidden++, onShown: () => shown++ });
  controller.notifyBusy();
  assert.equal(timer, null);
  controller.notifySettled();
  assert.equal(typeof timer, "function");
  controller.setHovered(true);
  assert.equal(timer, null);
  controller.setHovered(false);
  timer();
  assert.equal(hidden, 1);
  assert.equal(controller.snapshot().hidden, true);
  controller.show();
  assert.equal(shown, 1);
  assert.equal(controller.snapshot().hidden, false);
});

test("disabling auto hide reveals a hidden bubble and clears its timer", () => {
  let timer;
  let shown = 0;
  const controller = createBubbleAutoHide({ setTimer: (callback) => { timer = callback; return 1; }, clearTimer: () => { timer = null; }, onShown: () => shown++ });
  controller.notifySettled();
  timer();
  controller.configure(false);
  assert.equal(shown, 1);
  assert.equal(controller.snapshot().scheduled, false);
});

test("draft or IME deferral pauses settled countdown and stale timer callbacks cannot hide", () => {
  let timer = null;
  const captured = [];
  let hidden = 0;
  const controller = createBubbleAutoHide({
    setTimer: (callback) => { captured.push(callback); timer = callback; return captured.length; },
    clearTimer: () => { timer = null; },
    onHidden: () => hidden++,
  });
  controller.notifySettled();
  const stale = captured[0];
  controller.setDeferred(true);
  assert.equal(timer, null);
  stale();
  assert.equal(hidden, 0);
  controller.setDeferred(false);
  assert.equal(typeof timer, "function");
  timer();
  assert.equal(hidden, 1);
});
