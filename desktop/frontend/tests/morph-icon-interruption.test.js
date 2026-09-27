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
  try {
    morph.morphTo(icons["loader-circle"], "snappy");
    morph.morphTo(icons.stop, "snappy");
    const frame = scheduled.values().next().value;
    assert.equal(typeof frame, "function");
    scheduled.clear();
    frame(0);

    // The zero-time frame must still occupy the speaker's original 24px grid,
    // rather than collapse to (0, 0) and fly in from the corner.
    const points = [...currentPath.matchAll(/[ML](-?[\d.]+) (-?[\d.]+)/g)]
      .map(([, x, y]) => [Number(x), Number(y)]);
    const xs = points.map(([x]) => x);
    const ys = points.map(([, y]) => y);
    assert.ok(points.length > 0);
    assert.ok(Math.min(...xs) >= 1.9 && Math.max(...xs) >= 21.9, currentPath);
    assert.ok(Math.min(...ys) >= 3.9 && Math.max(...ys) >= 19.9, currentPath);
  } finally {
    morph.destroy();
    globalThis.requestAnimationFrame = previousRequest;
    globalThis.cancelAnimationFrame = previousCancel;
  }
});
