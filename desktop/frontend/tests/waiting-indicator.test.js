import assert from "node:assert/strict";
import test from "node:test";

import {
  WAITING_INDICATOR_FRAMES,
  WAITING_INDICATOR_INTERVAL_MS,
  createWaitingIndicator,
} from "../chat/waiting-indicator.js";

test("waiting indicator advances frames every 360ms", () => {
  const timers = [];
  const rendered = [];
  const indicator = createWaitingIndicator({
    setTimer(callback, delay) {
      timers.push({ callback, delay });
      return timers.length;
    },
    clearTimer() {},
    onFrame(frame) { rendered.push(frame); },
  });

  assert.deepEqual(WAITING_INDICATOR_FRAMES, [".", "..", "...", "....", ".....", "......", "....."]);
  assert.equal(WAITING_INDICATOR_INTERVAL_MS, 360);
  indicator.start();
  assert.equal(rendered.at(-1), ".");
  for (let index = 1; index < WAITING_INDICATOR_FRAMES.length; index += 1) {
    const timer = timers.shift();
    assert.equal(timer.delay, 360);
    timer.callback();
  }
  assert.deepEqual(rendered, WAITING_INDICATOR_FRAMES);
});

test("stopping rejects stale waiting frames after another run starts", () => {
  const timers = [], rendered = [];
  const indicator = createWaitingIndicator({
    setTimer(callback) { timers.push(callback); return timers.length; },
    clearTimer() {},
    onFrame(frame) { rendered.push(frame); },
  });
  indicator.start();
  const stale = timers.shift();
  indicator.stop();
  indicator.start();
  const count = rendered.length;
  stale();
  assert.equal(rendered.length, count);
  timers.shift()();
  assert.equal(rendered.at(-1), "..");
  indicator.stop();
  assert.equal(indicator.active(), false);
});
