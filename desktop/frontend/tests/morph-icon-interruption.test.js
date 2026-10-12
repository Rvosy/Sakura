import assert from "node:assert/strict";
import test from "node:test";

import { icons } from "../core/morph-icon-data.js";
import { createMorph } from "../vendor/morphicons/dom.js";

test("two icon changes before the first animation frame preserve the visible source shape", () => {
  const scheduled = new Map();
  let nextId = 0;
  const previousRequest = globalThis.requestAnimationFrame;
  const previousCancel = globalThis.cancelAnimationFrame;
  globalThis.requestAnimationFrame = callback => {
    const id = ++nextId;
    scheduled.set(id, callback);
    return id;
  };
  globalThis.cancelAnimationFrame = id => scheduled.delete(id);
  let currentPath;
  const morph = createMorph({ setAttribute: (_name, value) => { currentPath = value; } }, icons["volume-2"]);
  const bounds = path => {
    const coordinates = [...path.matchAll(/-?[\d.]+/g)].map(match => Number(match[0]));
    const x = coordinates.filter((_, index) => index % 2 === 0);
    const y = coordinates.filter((_, index) => index % 2 === 1);
    return [Math.min(...x), Math.max(...x), Math.min(...y), Math.max(...y)];
  };
  const initialBounds = bounds(currentPath);
  try {
    morph.morphTo(icons["loader-circle"], "snappy");
    morph.morphTo(icons.stop, "snappy");
    const frame = scheduled.values().next().value;
    assert.equal(typeof frame, "function");
    scheduled.clear();
    frame(0);

    // 重采样允许小幅误差，连续切换的首帧仍应留在原图标范围内。
    const interruptedBounds = bounds(currentPath);
    for (let axis = 0; axis < 2; axis++) {
      const start = axis * 2;
      const tolerance = (initialBounds[start + 1] - initialBounds[start]) * 0.05;
      assert.ok(Math.abs(interruptedBounds[start] - initialBounds[start]) <= tolerance, currentPath);
      assert.ok(Math.abs(interruptedBounds[start + 1] - initialBounds[start + 1]) <= tolerance, currentPath);
    }
  } finally {
    morph.destroy();
    globalThis.requestAnimationFrame = previousRequest;
    globalThis.cancelAnimationFrame = previousCancel;
  }
});
