import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  FIRST_RUN_GUIDE_STEPS,
  calloutPosition,
  firstRunGuideRequested,
  modelSlotFeatures,
  nextGuideIndex,
  spotlightGeometry,
} from "../settings/first-run-guide.js";

const guideSource = await readFile(new URL("../settings/first-run-guide.js", import.meta.url), "utf8");
const settingsSource = await readFile(new URL("../settings/settings.js", import.meta.url), "utf8");
const settingsHtml = await readFile(new URL("../settings/index.html", import.meta.url), "utf8");
const settingsStyles = await readFile(new URL("../settings/styles.css", import.meta.url), "utf8");

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

test("four dark blockers leave the spotlight open and stop outside clicks", () => {
  assert.match(guideSource, /\["top", "right", "bottom", "left"\]/);
  assert.match(settingsStyles, /\.first-run-guide__blocker\s*\{[\s\S]*pointer-events:\s*auto/);
  assert.match(settingsStyles, /\.first-run-guide__spotlight\s*\{[\s\S]*box-shadow:\s*0 0 0 100vmax rgba\(18, 25, 31, 0\.62\)[\s\S]*pointer-events:\s*none/);
  assert.doesNotMatch(settingsStyles, /\.is-first-run-guide-target|\.first-run-guide__spotlight\s*\{[^}]*border:/);
});

test("guide actions have no decorative divider", () => {
  const actions = settingsStyles.match(/\.first-run-guide__actions\s*\{([^}]*)\}/)?.[1] || "";
  assert.doesNotMatch(actions, /border/);
  assert.match(guideSource, /document\.createElement\("div"\);\s*actions\.className = "first-run-guide__actions"/);
  assert.doesNotMatch(guideSource, /document\.createElement\("footer"\)/);
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

test("guide completion and skip share the same non-gated completion path", () => {
  assert.match(guideSource, /nextButton\.textContent = index === FIRST_RUN_GUIDE_STEPS\.length - 1 \? "完成" : "下一步"/);
  assert.match(guideSource, /skipButton\.addEventListener\("click", \(\) => \{ void finish\(\); \}\)/);
  assert.match(guideSource, /if \(persistCompletion\) await invoke\("first_run_guide_complete"\)/);
  assert.doesNotMatch(guideSource, /character.*required|provider.*required|model.*required/i);
});

test("system help replay is explicit and never rewrites the completed marker", () => {
  const systemPage = settingsHtml.match(/<section id="page-system"[\s\S]*?<\/section>/)?.[0] || "";
  const aboutPage = settingsHtml.match(/<section id="page-about"[\s\S]*?<\/section>/)?.[0] || "";
  assert.match(systemPage, /新手引导[\s\S]*重新查看/);
  assert.doesNotMatch(aboutPage, /新手引导/);
  assert.match(settingsSource, /firstRunGuideController\?\.start\(\{ persist: false \}\)/);
  assert.match(guideSource, /start\(\{ persist = true \} = \{\}\)/);
});

test("missing targets, scrolling, resizing, focus, and reduced motion stay supported", () => {
  assert.match(guideSource, /当前没有这个功能，可以继续/);
  assert.match(guideSource, /window\.addEventListener\("resize", updateGeometry\)/);
  assert.match(guideSource, /geometryFrame = window\.requestAnimationFrame\(track\)/);
  assert.match(guideSource, /window\.cancelAnimationFrame\(geometryFrame\)/);
  assert.match(guideSource, /querySelector\("\.page-scroll"\)\?\.addEventListener\("scroll", updateGeometry/);
  assert.match(guideSource, /nextButton\.focus\(\{ preventScroll: true \}\)/);
  assert.match(settingsStyles, /prefers-reduced-motion:\s*reduce/);
  assert.match(settingsStyles, /width:\s*min\(300px,/);
});
