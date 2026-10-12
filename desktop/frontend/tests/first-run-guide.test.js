import assert from "node:assert/strict";
import test from "node:test";

import {
  calloutPosition,
  firstRunGuideRequested,
  modelSlotFeatures,
  spotlightGeometry,
} from "../settings/first-run-guide.js";

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
  assert.ok(position.top >= spotlight.top + spotlight.height);
  assert.ok(position.maxHeight > 0);
});

test("model guide uses labels and descriptions supplied by the current slots", () => {
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
});
