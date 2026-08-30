import assert from "node:assert/strict";
import test from "node:test";

import {
  isNativePetDragPointRejected,
  nativePetDragErrorCode,
  startNativePetDragWithRevisionRecovery,
} from "../pet/native-drag.js";

test("stale native drag refreshes the surface and retries once at the current revision", async () => {
  const calls = [];
  const synced = [];
  let attempts = 0;
  const result = await startNativePetDragWithRevisionRecovery({
    revision: 4,
    point: [120, 240],
    start: ({ revision, point }) => {
      calls.push({ revision, point });
      attempts += 1;
      if (attempts === 1) throw new Error("invoke failed: PET_DRAG_REVISION_STALE");
      return "started";
    },
    readSurfaceDiagnostics: async () => ({
      revision: 9,
      contentScale: 1,
      logicalBounds: [40, 80, 720, 600],
    }),
    syncSurface: async (diagnostics) => synced.push(diagnostics),
    getPoint: () => [121, 241],
  });

  assert.equal(result, "started");
  assert.deepEqual(calls, [
    { revision: 4, point: [120, 240] },
    { revision: 9, point: [121, 241] },
  ]);
  assert.equal(synced.length, 1);
  assert.equal(synced[0].revision, 9);
});

test("transparent drag rejection is classified without becoming a user-facing failure", () => {
  assert.equal(nativePetDragErrorCode("PET_DRAG_POINT_REJECTED"), "PET_DRAG_POINT_REJECTED");
  assert.equal(isNativePetDragPointRejected({ message: "invoke: PET_DRAG_POINT_REJECTED" }), true);
  assert.equal(isNativePetDragPointRejected("PET_DRAG_REVISION_STALE"), false);
});
