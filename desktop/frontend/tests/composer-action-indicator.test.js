import assert from "node:assert/strict";
import test from "node:test";

import { createComposerActionIndicator } from "../chat/composer-action-indicator.js";

function fixture({ reducedMotion = false } = {}) {
  const attributes = new Map();
  const shape = {
    style: {},
    setAttribute(name, value) { attributes.set(name, value); },
  };
  const animations = [];
  const svg = {
    style: {},
    animate(frames, options) {
      const animation = { frames, options, cancelled: false, cancel() { this.cancelled = true; } };
      animations.push(animation);
      return animation;
    },
  };
  const indicator = createComposerActionIndicator({ svg, shape, prefersReducedMotion: () => reducedMotion });
  return { indicator, svg, shape, attributes, animations };
}

test("busy composer action uses the existing cancel SVG as a clickable rotating ring", () => {
  const { indicator, shape, attributes, animations } = fixture();
  assert.equal(attributes.get("rx"), "7.5");
  assert.equal(shape.style.fill, "none");
  assert.equal(shape.style.stroke, "currentColor");
  assert.equal(shape.style.strokeDasharray, "34 14");

  indicator.setBusy(true);
  indicator.setBusy(true);
  assert.equal(animations.length, 1);
  assert.deepEqual(animations[0].options, { duration: 820, iterations: Infinity, easing: "linear" });

  indicator.setBusy(false);
  assert.equal(animations[0].cancelled, true);
});

test("reduced motion keeps the ring visible without rotating it", () => {
  const { indicator, animations } = fixture({ reducedMotion: true });
  indicator.setBusy(true);
  assert.equal(animations.length, 0);
});
