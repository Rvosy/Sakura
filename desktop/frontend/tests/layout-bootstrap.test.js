import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  applyBootstrapPetLayout,
  computePetLayout,
  samePetSurfaceGeometry,
  validateBootstrapSurfaceDiagnostics,
  validateLayoutContract,
} from "../pet/layout.js";

const contract = validateLayoutContract(
  JSON.parse(await readFile(new URL("../pet/layout-contract.json", import.meta.url), "utf8")),
);

test("expanded layout limits preserve requested positions with a full-height composer", () => {
  for (const offset of [-400, -300, 0, 300, 400]) {
    for (const inputBarOffset of [0, 400]) {
      const layout = computePetLayout(contract, "product", "", {
        controlPanelWidth: 860,
        bubbleMaxHeight: 400,
        controlPanelVerticalOffset: offset,
        inputBarOffset,
      }, { inputHeight: 152 });
      assert.equal(layout.bubbleRect[3], 400);
      assert.equal(layout.bubbleRect[1] + 400, 808 - offset);
      assert.equal(layout.inputRect[1], 818 - offset + inputBarOffset);
      assert.ok(layout.inputRect[1] + 152 <= layout.windowSize[1]);
      assert.deepEqual(layout.portraitAnchor, contract.viewport.portraitAnchor);
    }
  }
});

function stage() {
  const properties = new Map();
  return {
    dataset: {},
    style: {
      left: "",
      top: "",
      setProperty: (name, value) => properties.set(name, value),
      properties,
    },
  };
}

test("revision zero diagnostics restore the WebView surface geometry", () => {
  const root = stage();
  const restored = applyBootstrapPetLayout(root, computePetLayout(contract), {
    revision: 0,
    contentScale: 0.875,
    logicalBounds: [0, 0, 900, 1490],
  });

  assert.equal(restored.revision, 0);
  assert.equal(root.style.properties.get("--content-scale"), "0.875");
  assert.equal(root.style.left, "0px");
  assert.equal(root.style.top, "0px");
  assert.equal(root.dataset.surfaceX, "0");
  assert.equal(root.dataset.surfaceY, "0");
  assert.equal(root.dataset.layoutState, "product");
});

test("invalid bootstrap diagnostics fail closed", () => {
  for (const value of [
    null,
    { revision: -1, contentScale: 1, logicalBounds: [0, 0, 900, 996] },
    { revision: 0, contentScale: 0, logicalBounds: [0, 0, 900, 996] },
    { revision: 0, contentScale: 1, logicalBounds: [0, 0, 0, 996] },
  ]) {
    assert.throws(() => validateBootstrapSurfaceDiagnostics(value), /bootstrap pet surface/);
  }
});

test("repeated native surface publications do not invalidate unchanged WebView geometry", () => {
  const currentBounds = [120, 640, 428, 276];
  assert.equal(samePetSurfaceGeometry(1, currentBounds, {
    revision: 9,
    contentScale: 1,
    activeBounds: [...currentBounds],
  }), true);
  assert.equal(samePetSurfaceGeometry(1, currentBounds, {
    revision: 10,
    contentScale: 1,
    activeBounds: [120, 300, 428, 616],
  }), false);
});
