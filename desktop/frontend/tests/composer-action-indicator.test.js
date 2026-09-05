import assert from "node:assert/strict";
import test from "node:test";

import { createComposerActionIndicator } from "../chat/composer-action-indicator.js";

function fixture({ reducedMotion = false } = {}) {
  const animations = [];
  const departures = [];
  const icon = {
    style: {},
    animate(frames, options) {
      const animation = { frames, options, cancelled: false, cancel() { this.cancelled = true; } };
      animations.push(animation);
      return animation;
    },
  };
  const sendLayer = { animate(frames, options) {
    const animation = { frames, options, cancelled: false, cancel() { this.cancelled = true; } };
    departures.push(animation);
    return animation;
  } };
  const indicator = createComposerActionIndicator({ icon, sendLayer, prefersReducedMotion: () => reducedMotion });
  return { indicator, icon, animations, departures, reduceMotion() { reducedMotion = true; } };
}

test("busy composer action starts one indicator and cancels it when idle or disposed", () => {
  const { indicator, animations, departures } = fixture();
  indicator.setBusy(true);
  indicator.setBusy(true);
  assert.equal(animations.length, 1);
  assert.equal(departures.length, 1);
  assert.deepEqual(animations[0].options, { duration: 820, iterations: Infinity, easing: "linear" });

  indicator.setBusy(false);
  assert.equal(animations[0].cancelled, true);
  assert.equal(departures[0].cancelled, true);
  indicator.setBusy(true);
  assert.equal(animations.length, 2);
  indicator.dispose();
  assert.equal(animations[1].cancelled, true);
});

test("reduced motion keeps the ring visible without rotating it", () => {
  const { indicator, animations, departures } = fixture({ reducedMotion: true });
  indicator.setBusy(true);
  assert.equal(animations.length, 0);
  assert.equal(departures.length, 0);
});

test("switching to reduced motion stops active motion without changing the busy state", () => {
  const { indicator, animations, departures, reduceMotion } = fixture();
  indicator.setBusy(true);
  reduceMotion();
  indicator.setBusy(true);
  assert.equal(animations[0].cancelled, true);
  assert.equal(departures[0].cancelled, true);
});
