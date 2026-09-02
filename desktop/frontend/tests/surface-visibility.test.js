import assert from "node:assert/strict";
import test from "node:test";

import {
  createSurfaceHoverTracker,
  createSurfaceVisibilityController,
  validateBubbleAutoHideSettings,
  waitForSurfaceFadeCompletion,
} from "../pet/surface-visibility.js";

function fixture(settings = { autoHideEnabled: true, autoHideDelaySeconds: 5 }) {
  const timers = new Map();
  const changes = [];
  let nextTimer = 0;
  const controller = createSurfaceVisibilityController({
    settings,
    onVisibilityChange: (kind, visible) => changes.push([kind, visible]),
    setTimer: (callback, delay) => {
      const id = ++nextTimer;
      timers.set(id, { callback, delay });
      return id;
    },
    clearTimer: (id) => timers.delete(id),
  });
  return {
    controller,
    changes,
    timers,
    fireTimer() {
      const [id, timer] = timers.entries().next().value || [];
      if (!timer) return false;
      timers.delete(id);
      timer.callback();
      return true;
    },
  };
}

test("bubble settings are exact and bounded", () => {
  assert.deepEqual(
    validateBubbleAutoHideSettings({ autoHideEnabled: false, autoHideDelaySeconds: 120 }),
    { autoHideEnabled: false, autoHideDelaySeconds: 120 },
  );
  assert.throws(() => validateBubbleAutoHideSettings({ autoHideEnabled: true, autoHideDelaySeconds: 0 }));
  assert.throws(() => validateBubbleAutoHideSettings({ autoHideEnabled: 1, autoHideDelaySeconds: 5 }));
});

test("surface hover handoff does not publish a false leave between overlapping controls", () => {
  const timers = new Map();
  const changes = [];
  let nextTimer = 0;
  const tracker = createSurfaceHoverTracker({
    onHoverChange: (active) => changes.push(active),
    setTimer: (callback, delay) => {
      const id = ++nextTimer;
      timers.set(id, { callback, delay });
      return id;
    },
    clearTimer: (id) => timers.delete(id),
  });
  tracker.enter("bubble");
  tracker.leave("bubble");
  assert.equal(timers.values().next().value.delay, 80);
  tracker.enter("portrait");
  assert.deepEqual(changes, [true]);
  assert.equal(timers.size, 0);

  tracker.leave("portrait");
  const [id, timer] = timers.entries().next().value;
  timers.delete(id);
  timer.callback();
  assert.deepEqual(changes, [true, false]);
});

test("native contraction waits for opacity completion and two painted frames", async () => {
  const listeners = new Map();
  const frames = [];
  const timers = new Map();
  let nextTimer = 0;
  const element = {
    addEventListener(name, listener) { listeners.set(name, listener); },
    removeEventListener(name, listener) {
      if (listeners.get(name) === listener) listeners.delete(name);
    },
  };
  let finished = false;
  const completion = waitForSurfaceFadeCompletion(element, {
    setTimer: (callback, delay) => {
      const id = ++nextTimer;
      timers.set(id, { callback, delay });
      return id;
    },
    clearTimer: (id) => timers.delete(id),
    requestFrame: (callback) => frames.push(callback),
  }).then(() => { finished = true; });
  await Promise.resolve();
  assert.equal(timers.values().next().value.delay, 320);
  assert.equal(frames.length, 0);

  listeners.get("transitionend")({ target: element, propertyName: "opacity" });
  await Promise.resolve();
  assert.equal(timers.size, 0);
  assert.equal(frames.length, 1);
  frames.shift()();
  assert.equal(frames.length, 1);
  assert.equal(finished, false);
  frames.shift()();
  await completion;
  assert.equal(finished, true);
});

test("settled bubble hides after the configured idle delay and pet activation reveals it", () => {
  const env = fixture();
  env.controller.start("settled");
  assert.equal(env.timers.values().next().value.delay, 5000);
  assert.equal(env.fireTimer(), true);
  assert.deepEqual(env.changes, [["input", false], ["bubble", false]]);
  assert.equal(env.controller.activatePet(), true);
  assert.deepEqual(env.changes.at(-1), ["bubble", true]);
  assert.equal(env.timers.size, 1);
});

test("hover pauses bubble countdown and drives unpinned input visibility", () => {
  const env = fixture();
  env.controller.start("settled");
  env.controller.setHoverActive(true);
  assert.equal(env.timers.size, 0);
  assert.deepEqual(env.changes.at(-1), ["input", true]);
  env.controller.setHoverActive(false);
  assert.deepEqual(env.changes.at(-1), ["input", false]);
  assert.equal(env.timers.size, 1);
});

test("input remains visible while pinned and active reply reveals a hidden bubble", () => {
  const env = fixture();
  env.controller.start("settled");
  env.fireTimer();
  env.controller.setInputPinned(true);
  assert.deepEqual(env.changes.at(-1), ["input", true]);
  env.controller.setHoverActive(true);
  env.controller.setHoverActive(false);
  assert.equal(env.controller.snapshot().inputVisible, true);
  env.controller.setPhase("thinking");
  assert.deepEqual(env.changes.at(-1), ["bubble", true]);
  assert.equal(env.timers.size, 0);
});

test("the settings appearance session keeps the input and bubble visible until the window closes", () => {
  const env = fixture();
  env.controller.start("settled");
  env.fireTimer();
  assert.equal(env.controller.snapshot().bubbleVisible, false);
  assert.equal(env.controller.snapshot().inputVisible, false);

  env.controller.setSettingsAppearanceActive(true);
  assert.deepEqual(env.changes.slice(-2), [["bubble", true], ["input", true]]);
  assert.equal(env.timers.size, 0);
  env.controller.setHoverActive(true);
  env.controller.setHoverActive(false);
  assert.equal(env.controller.snapshot().bubbleVisible, true);
  assert.equal(env.controller.snapshot().inputVisible, true);
  assert.equal(env.timers.size, 0);

  env.controller.setSettingsAppearanceActive(false);
  assert.deepEqual(env.changes.at(-1), ["input", false]);
  assert.equal(env.timers.size, 1);
});

test("bubble preview reveals a hidden bubble and restarts its idle countdown", () => {
  const env = fixture();
  env.controller.start("settled");
  env.fireTimer();
  assert.equal(env.controller.snapshot().bubbleVisible, false);

  assert.equal(env.controller.previewBubble(), true);
  assert.deepEqual(env.changes.at(-1), ["bubble", true]);
  const firstTimer = env.timers.keys().next().value;
  assert.equal(env.controller.previewBubble(), false);
  assert.equal(env.timers.has(firstTimer), false);
  assert.equal(env.timers.size, 1);
});

test("disabling auto hide reveals the bubble and stops future countdowns", () => {
  const env = fixture();
  env.controller.start("settled");
  env.fireTimer();
  env.controller.setSettings({ autoHideEnabled: false, autoHideDelaySeconds: 8 });
  assert.deepEqual(env.changes.at(-1), ["bubble", true]);
  assert.equal(env.timers.size, 0);
});
