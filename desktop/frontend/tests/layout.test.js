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
    assert.deepEqual(layout.windowSize, expectedSizes[state]);
    assert.ok(layout.windowSize[0] <= 816);
    assert.ok(layout.windowSize[1] <= 680);
    assert.equal(layout.portraitAnchor[0], layout.portraitRect[0] + layout.portraitRect[2] / 2);
    assert.equal(layout.portraitAnchor[1], layout.portraitRect[1] + layout.portraitRect[3]);
  }
});

test("states expand upward and left from the portrait anchor", () => {
  const idle = computePetLayout(contract, "idle");
  for (const state of ["bubble", "composer", "expanded"]) {
    const layout = computePetLayout(contract, state);
    assert.ok(layout.portraitAnchor[0] >= idle.portraitAnchor[0]);
    assert.ok(layout.portraitAnchor[1] >= idle.portraitAnchor[1]);
    assert.ok(layout.windowSize[0] >= idle.windowSize[0]);
    assert.ok(layout.windowSize[1] >= idle.windowSize[1]);
  }
});

test("long and extreme placeholder text cannot change native window dimensions", () => {
  const normal = computePetLayout(contract, "expanded", "short");
  const long = computePetLayout(contract, "expanded", "樱".repeat(100_000));
  const hostile = computePetLayout(contract, "expanded", `${Number.MAX_VALUE}\n${"W".repeat(20_000)}`);
  assert.deepEqual(long.windowSize, normal.windowSize);
  assert.deepEqual(hostile.windowSize, normal.windowSize);
  assert.equal(long.placeholderText.length, 4096);
  assert.equal(hostile.placeholderText.length, 4096);
});

test("invalid or oversized contracts fail closed", () => {
  assert.throws(() => validateLayoutContract({ schemaVersion: 99, states: {} }));
  const oversized = structuredClone(contract);
  oversized.states.expanded.windowSize = [1201, 680];
  assert.throws(() => validateLayoutContract(oversized), /unsafe native window size/);
});
