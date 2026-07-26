import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { computePetLayout, PRODUCT_LAYOUT_STATE, validateLayoutContract } from "../pet/layout.js";

const contract = validateLayoutContract(
  JSON.parse(await readFile(new URL("../pet/layout-contract.json", import.meta.url), "utf8")),
);

test("one fixed product envelope defines every visible surface", () => {
  const layout = computePetLayout(contract);
  assert.equal(PRODUCT_LAYOUT_STATE, "product");
  assert.deepEqual(layout.windowSize, [816, 680]);
  assert.deepEqual(layout.activeWindowSize, [816, 680]);
  assert.deepEqual(layout.activeOffset, [0, 0]);
  assert.deepEqual(layout.portraitRect, [384, 88, 416, 580]);
  assert.deepEqual(layout.bubbleRect, [20, 70, 410, 272]);
  assert.deepEqual(layout.inputRect, [20, 356, 410, 78]);
  assert.deepEqual(layout.portraitAnchor, [592, 668]);
});

test("every chat lifecycle reuses the identical product geometry", () => {
  const baseline = computePetLayout(contract, "product");
  for (const phase of ["ready", "thinking", "typing", "settled", "error", "reconnecting"]) {
    const layout = computePetLayout(contract, "product", phase);
    for (const key of ["windowSize", "portraitRect", "bubbleRect", "inputRect", "portraitAnchor"]) {
      assert.deepEqual(layout[key], baseline[key], `${phase}.${key}`);
    }
  }
});

test("long and hostile text cannot change native window dimensions", () => {
  const normal = computePetLayout(contract, "product", "short");
  const long = computePetLayout(contract, "product", "樱".repeat(100_000));
  assert.deepEqual(long.windowSize, normal.windowSize);
  assert.deepEqual(long.portraitRect, normal.portraitRect);
  assert.equal(long.placeholderText.length, 4096);
});

test("unknown states and malformed fixed envelopes fail closed", () => {
  assert.throws(() => computePetLayout(contract, "thinking"), /unknown pet state/);
  assert.throws(() => validateLayoutContract({ schemaVersion: 99, states: {} }));
  const moved = structuredClone(contract);
  moved.states.product.portraitAnchor = [591, 668];
  assert.throws(() => validateLayoutContract(moved), /fixed product viewport/);
  const escaped = structuredClone(contract);
  escaped.states.product.inputRect = [800, 650, 40, 40];
  assert.throws(() => validateLayoutContract(escaped), /escapes native window bounds/);
});
