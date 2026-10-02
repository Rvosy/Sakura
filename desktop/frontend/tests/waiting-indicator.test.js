import assert from "node:assert/strict";
import test from "node:test";

import { createWaitingIndicator } from "../chat/waiting-indicator.js";

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
