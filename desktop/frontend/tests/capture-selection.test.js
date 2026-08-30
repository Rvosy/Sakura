import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { normalizedSelection, selectionAccepted } from "../capture/capture-selection.js";
import { applyCaptureTheme, normalizeCapturePrimary } from "../capture/capture-theme.js";

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

test("capture selection follows the validated current theme primary", () => {
  const applied = [];
  const root = { style: { setProperty: (...entry) => applied.push(entry) } };
  assert.equal(applyCaptureTheme("A1b2C3", root), "#a1b2c3");
  assert.deepEqual(applied, [["--capture-primary", "#a1b2c3"]]);
  assert.equal(normalizeCapturePrimary("bad"), "#4b9ac4");
  assert.equal(normalizeCapturePrimary("#123456; color: red"), "#4b9ac4");
});

test("capture selection outline and fill share the themed primary variable", () => {
  const css = readFileSync(new URL("../capture/capture.css", import.meta.url), "utf8");
  assert.match(css, /#selection-box\s*\{[\s\S]*border:\s*2px solid var\(--capture-primary\)/);
  assert.match(
    css,
    /#selection-box\s*\{[\s\S]*background:\s*color-mix\(in srgb, var\(--capture-primary\) 8%, transparent\)/,
  );
  assert.doesNotMatch(css, /#ff7fb5|255 127 181/);
});
