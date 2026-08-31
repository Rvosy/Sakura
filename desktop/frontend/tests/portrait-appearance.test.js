import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { constrainedPortraitScale } from "../pet/appearance.js";

const portraitRect = [150, 328, 600, 656];
const windowSize = [900, 1374];
const styles = await readFile(new URL("../styles.css", import.meta.url), "utf8");

function renderedSize(sourceSize, requestedPercent = 100) {
  const contain = Math.min(portraitRect[2] / sourceSize[0], portraitRect[3] / sourceSize[1]);
  const scale = constrainedPortraitScale({
    requestedPercent,
    sourceSize,
    portraitRect,
    windowSize,
  });
  return [sourceSize[0] * contain * scale, sourceSize[1] * contain * scale];
}

test("wide tall square and extreme portraits remain contained at normal scale", () => {
  for (const sourceSize of [
    [1600, 954],
    [954, 1600],
    [1600, 1600],
    [1600, 200],
    [200, 1600],
  ]) {
    const [width, height] = renderedSize(sourceSize);
    assert.ok(width <= portraitRect[2] + Number.EPSILON);
    assert.ok(height <= portraitRect[3] + Number.EPSILON);
  }
});

test("portrait scaling stays bounded across the supported range", () => {
  for (const requestedPercent of [50, 100, 150]) {
    for (const sourceSize of [[1600, 954], [954, 1600], [1600, 200]]) {
      const [width, height] = renderedSize(sourceSize, requestedPercent);
      assert.ok(width <= windowSize[0] + Number.EPSILON);
      assert.ok(height <= windowSize[1] + Number.EPSILON);
    }
  }
});

test("both portrait transition layers keep contain and bottom-center positioning", () => {
  const rule = styles.match(/\.portrait-image\s*\{([^}]+)\}/)?.[1] || "";
  assert.match(rule, /width:\s*100%/);
  assert.match(rule, /height:\s*100%/);
  assert.match(rule, /object-fit:\s*contain/);
  assert.match(rule, /object-position:\s*center bottom/);
});
