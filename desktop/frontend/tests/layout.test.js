import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { computePetLayout, PRESENTATION_STATES, validateLayoutContract } from "../pet/layout.js";

const contract = validateLayoutContract(
  JSON.parse(await readFile(new URL("../pet/layout-contract.json", import.meta.url), "utf8")),
);

const expectedSizes = {
  idle: [320, 420],
  bubble: [736, 500],
  composer: [736, 592],
  expanded: [816, 680],
};

test("four presentation states have deterministic bounded layouts", () => {
  for (const state of PRESENTATION_STATES) {
    const layout = computePetLayout(contract, state);
    assert.deepEqual(layout.activeWindowSize, expectedSizes[state]);
    assert.deepEqual(layout.windowSize, [816, 680]);
    assert.equal(layout.portraitAnchor[0], layout.portraitRect[0] + layout.portraitRect[2] / 2);
    assert.equal(layout.portraitAnchor[1], layout.portraitRect[1] + layout.portraitRect[3]);
  }
});

test("states expand upward and left from the portrait anchor", () => {
  const idle = computePetLayout(contract, "idle");
  for (const state of ["bubble", "composer", "expanded"]) {
    const layout = computePetLayout(contract, state);
    assert.deepEqual(layout.portraitRect, idle.portraitRect);
    assert.deepEqual(layout.portraitAnchor, idle.portraitAnchor);
    assert.ok(layout.activeWindowSize[0] >= idle.activeWindowSize[0]);
    assert.ok(layout.activeWindowSize[1] >= idle.activeWindowSize[1]);
    assert.ok(layout.activeOffset[0] <= idle.activeOffset[0]);
    assert.ok(layout.activeOffset[1] <= idle.activeOffset[1]);
  }
});

test("long and extreme placeholder text cannot change native window dimensions", () => {
  const normal = computePetLayout(contract, "expanded", "short");
  const long = computePetLayout(contract, "expanded", "樱".repeat(100_000));
  const hostile = computePetLayout(contract, "expanded", `${Number.MAX_VALUE}\n${"W".repeat(20_000)}`);
  assert.deepEqual(long.activeWindowSize, normal.activeWindowSize);
  assert.deepEqual(hostile.activeWindowSize, normal.activeWindowSize);
  assert.equal(long.placeholderText.length, 4096);
  assert.equal(hostile.placeholderText.length, 4096);
});

test("invalid or oversized contracts fail closed", () => {
  assert.throws(() => validateLayoutContract({ schemaVersion: 99, states: {} }));
  const oversized = structuredClone(contract);
  oversized.states.expanded.windowSize = [1201, 680];
  assert.throws(() => validateLayoutContract(oversized), /unsafe native window size/);
  const invalidViewport = structuredClone(contract);
  invalidViewport.viewport.windowSize = [0, 680];
  assert.throws(() => validateLayoutContract(invalidViewport), /invalid native viewport/);
});
