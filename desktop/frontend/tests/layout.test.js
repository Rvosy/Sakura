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
  assert.deepEqual(layout.windowSize, [900, 996]);
  assert.deepEqual(layout.activeWindowSize, [900, 996]);
  assert.deepEqual(layout.activeOffset, [0, 0]);
  assert.deepEqual(layout.portraitRect, [150, 328, 600, 656]);
  assert.deepEqual(layout.bubbleRect, [130, 680, 640, 128]);
  assert.deepEqual(layout.inputRect, [130, 818, 640, 52]);
  assert.deepEqual(layout.portraitAnchor, [450, 984]);
  assert.deepEqual(layout.layoutAdjustments, {
    controlPanelWidth: 640,
    bubbleMaxHeight: 128,
    controlPanelVerticalOffset: 0,
    inputBarOffset: 0,
  });
});

test("reserved appearance settings preserve the centered legacy control-panel semantics", () => {
  const layout = computePetLayout(contract, "product", "", {
    controlPanelWidth: 553,
    bubbleMaxHeight: 139,
    controlPanelVerticalOffset: -26,
    inputBarOffset: 12,
  }, { bubbleHeight: 139, inputHeight: 52 });
  assert.deepEqual(layout.bubbleRect, [174, 695, 553, 139]);
  assert.deepEqual(layout.inputRect, [174, 856, 553, 52]);
  assert.equal(layout.bubbleRect[0], layout.inputRect[0]);
  assert.equal(layout.bubbleRect[2], layout.inputRect[2]);
});

test("appearance settings are normalized to the fixed product envelope", () => {
  const layout = computePetLayout(contract, "product", "", {
    controlPanelWidth: 9999,
    bubbleMaxHeight: -1,
    controlPanelVerticalOffset: 9999,
    inputBarOffset: -80,
  }, { bubbleHeight: 9999, inputHeight: 52 });
  assert.deepEqual(layout.layoutAdjustments, {
    controlPanelWidth: 760,
    bubbleMaxHeight: 96,
    controlPanelVerticalOffset: 160,
    inputBarOffset: 0,
  });
  assert.deepEqual(layout.bubbleRect, [70, 552, 760, 96]);
  assert.deepEqual(layout.inputRect, [70, 658, 760, 52]);
});

test("a four-line composer grows upward and pushes the adaptive bubble without moving its bottom anchor", () => {
  const layout = computePetLayout(contract, "product", "", {}, {
    bubbleHeight: 88,
    inputHeight: 122,
  });
  assert.deepEqual(layout.inputRect, [130, 748, 640, 122]);
  assert.deepEqual(layout.bubbleRect, [130, 650, 640, 88]);
  assert.equal(layout.inputRect[1] - (layout.bubbleRect[1] + layout.bubbleRect[3]), 10);
  assert.equal(layout.inputRect[1] + layout.inputRect[3], 870);
});

test("reply content is clamped by the configured bubble maximum", () => {
  const layout = computePetLayout(contract, "product", "", { bubbleMaxHeight: 116 }, {
    bubbleHeight: 9999,
    inputHeight: 52,
  });
  assert.equal(layout.bubbleRect[3], 116);
  assert.equal(layout.bubbleRect[1] + layout.bubbleRect[3], 808);
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
  moved.states.product.portraitAnchor = [449, 984];
  assert.throws(() => validateLayoutContract(moved), /fixed product viewport/);
  const escaped = structuredClone(contract);
  escaped.states.product.inputRect = [890, 980, 40, 40];
  assert.throws(() => validateLayoutContract(escaped), /escapes native window bounds/);
});
