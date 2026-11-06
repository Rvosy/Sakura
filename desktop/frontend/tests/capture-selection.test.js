import assert from "node:assert/strict";
import test from "node:test";

import { normalizedSelection, selectionAccepted } from "../capture/capture-selection.js";

test("capture selection normalizes every drag direction in monitor-local coordinates", () => {
  assert.deepEqual(
    normalizedSelection({ x: 90, y: 70 }, { x: 10, y: 20 }),
    { x: 10, y: 20, width: 80, height: 50 },
  );
  assert.deepEqual(
    normalizedSelection({ x: 10, y: 70 }, { x: 90, y: 20 }),
    { x: 10, y: 20, width: 80, height: 50 },
  );
});

test("capture selection rejects clicks and undersized regions", () => {
  assert.equal(selectionAccepted({ x: 1, y: 1, width: 7, height: 20 }), false);
  assert.equal(selectionAccepted({ x: 1, y: 1, width: 8, height: 8 }), true);
  assert.equal(selectionAccepted(null), false);
});
