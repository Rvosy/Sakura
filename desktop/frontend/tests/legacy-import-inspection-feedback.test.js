import assert from "node:assert/strict";
import test from "node:test";

import {
  beginLegacyInspection,
  legacyInspectionProgress,
} from "../onboarding/legacy-import-state.js";

test("directory inspection becomes visible before the backend scan completes", () => {
  const selected = {
    selectionId: "opaque-selection",
    sourceLabel: "Sakura 0.9",
    state: "selected",
  };

  const inspecting = beginLegacyInspection(selected);
  const progress = legacyInspectionProgress(inspecting);

  assert.equal(inspecting.state, "inspecting");
  assert.equal(inspecting.selectionId, selected.selectionId);
  assert.equal(progress.indeterminate, true);
  assert.equal(progress.progressValue, null);
  assert.equal(legacyInspectionProgress(selected), null);
});
