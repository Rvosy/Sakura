import assert from "node:assert/strict";
import test from "node:test";

import {
  FIRST_RUN_GUIDE_STEPS,
  calloutPosition,
  firstRunGuideRequested,
  modelSlotFeatures,
  nextGuideIndex,
  spotlightGeometry,
} from "../settings/first-run-guide.js";

test("first-run guide has the non-blocking character, provider, and model sequence", () => {
  assert.deepEqual(
    FIRST_RUN_GUIDE_STEPS.map(({ id, page, selector }) => ({ id, page, selector })),
    [
      { id: "character", page: "character", selector: "#characterImportButton" },
      { id: "providers", page: "providers", selector: "#addProviderButton" },
      { id: "models", page: "model", selector: "#page-model > fieldset.settings-group" },
    ],
  );
  assert.equal(nextGuideIndex(0, -1), 0);
  assert.equal(nextGuideIndex(0, 1), 1);
  assert.equal(nextGuideIndex(2, 1), 2);
});

test("first-run guide query is explicit and replay-safe", () => {
  assert.equal(firstRunGuideRequested("?guide=first-run"), true);
  assert.equal(firstRunGuideRequested("?guide=other"), false);
  assert.equal(firstRunGuideRequested(""), false);
});

test("spotlight geometry matches the target exactly and clips only at the viewport", () => {
  assert.deepEqual(
    spotlightGeometry(
      { left: 2, top: 4, right: 122, bottom: 64, width: 120, height: 60 },
      { width: 800, height: 600 },
    ),
    { left: 2, top: 4, width: 120, height: 60 },
  );
  assert.deepEqual(
    spotlightGeometry(
      { left: -5, top: -4, right: 35, bottom: 26, width: 40, height: 30 },
      { width: 800, height: 600 },
    ),
    { left: 0, top: 0, width: 35, height: 26 },
  );
  assert.equal(spotlightGeometry(null, { width: 800, height: 600 }), null);
});

test("guide copy stays outside a wide spotlight", () => {
  const spotlight = { left: 200, top: 158, width: 758, height: 170 };
  const position = calloutPosition(
    spotlight,
    { width: 980, height: 684 },
    { width: 300, height: 250 },
  );
  assert.ok(position.top >= spotlight.top + spotlight.height + 18);
  assert.ok(position.maxHeight > 0);
});

test("model guide uses dynamic slot labels and falls back when slots are unavailable", () => {
  const row = (label, description) => ({
    querySelector(selector) {
      const textContent = selector === ".setting-title" ? label : description;
      return { textContent };
    },
  });
  const root = {
    querySelectorAll() {
      return [row("对话模型", "主要聊天"), row("视觉模型", "处理图片")];
    },
  };
  assert.deepEqual(modelSlotFeatures(root), [
    { label: "对话模型", description: "主要聊天" },
    { label: "视觉模型", description: "处理图片" },
  ]);
  assert.deepEqual(
    modelSlotFeatures(null).map(({ label }) => label),
    ["对话模型", "视觉对话模型"],
  );
});
