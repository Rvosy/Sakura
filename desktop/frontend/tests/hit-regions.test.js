import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { computePetLayout, validateLayoutContract } from "../pet/layout.js";

const hitRegions = await import("../pet/hit-regions.js").catch(() => null);
const contract = validateLayoutContract(
  JSON.parse(await readFile(new URL("../pet/layout-contract.json", import.meta.url), "utf8")),
);








test("interactive controls win over an overlapping portrait drag region", () => {
  assert.ok(hitRegions, "hit-region module must exist");
  const product = hitRegions.computeHitRegions(computePetLayout(contract));
  assert.equal(hitRegions.classifyHitPoint(product, [742, 696]), "interactive");
  assert.equal(hitRegions.classifyHitPoint(product, [242, 836]), "interactive");
  assert.notEqual(hitRegions.classifyHitPoint(product, [242, 836]), "drag");
  assert.equal(hitRegions.classifyHitPoint(product, [342, 516]), "drag");
  assert.equal(
    hitRegions.shouldStartNativeDrag({ hitKind: "interactive", button: 0, isPrimary: true }),
    false,
  );
  assert.equal(
    hitRegions.shouldStartNativeDrag({ hitKind: "drag", button: 0, isPrimary: true }),
    true,
  );
  assert.equal(
    hitRegions.shouldStartNativeDrag({ hitKind: "drag", button: 2, isPrimary: true }),
    false,
  );
});

test("interactive reply text overrides its enclosing bubble drag region", () => {
  const model = hitRegions.computeHitRegions(computePetLayout(contract));
  assert.equal(hitRegions.classifyHitPoint(model, [142, 716]), "drag");
  assert.equal(hitRegions.classifyHitPoint(model, [242, 716]), "drag");
  const hitKind = hitRegions.classifyPointerHit({
    model,
    point: [242, 716],
    interactiveTarget: true,
  });
  assert.equal(hitKind, "interactive");
  assert.equal(hitRegions.shouldStartNativeDrag({ hitKind, button: 0, isPrimary: true }), false);
});

test("starting a native drag clears an existing document text selection", () => {
  let removeCount = 0;
  const selection = {
    rangeCount: 1,
    isCollapsed: false,
    removeAllRanges() { removeCount += 1; },
  };
  assert.equal(hitRegions.clearTextSelection(selection), true);
  assert.equal(removeCount, 1);
});

test("missing and collapsed selections remain untouched", () => {
  let removeCount = 0;
  const collapsed = {
    rangeCount: 1,
    isCollapsed: true,
    removeAllRanges() { removeCount += 1; },
  };
  assert.equal(hitRegions.clearTextSelection(null), false);
  assert.equal(hitRegions.clearTextSelection({ ...collapsed, rangeCount: 0 }), false);
  assert.equal(hitRegions.clearTextSelection(collapsed), false);
  assert.equal(removeCount, 0);
});

test("product menu opens from every visible pet region but not transparent space", () => {
  assert.equal(
    hitRegions.shouldOpenProductMenu({ hitKind: "drag", button: 2 }),
    true,
  );
  assert.equal(
    hitRegions.shouldOpenProductMenu({ hitKind: "interactive", button: 2 }),
    true,
  );
  assert.equal(
    hitRegions.shouldOpenProductMenu({ hitKind: "neutral", button: 2 }),
    true,
  );
  assert.equal(
    hitRegions.shouldOpenProductMenu({ hitKind: "transparent", button: 2 }),
    false,
  );
  assert.equal(
    hitRegions.shouldOpenProductMenu({ hitKind: "drag", button: 0 }),
    false,
  );
});

test("invalid, ambiguous, or out-of-envelope rectangles fail closed", () => {
  assert.ok(hitRegions, "hit-region module must exist");
  assert.throws(
    () => hitRegions.computeHitRegions({ state: "product", windowSize: [900, 996] }),
    /portraitRect/,
  );
  const layout = { ...computePetLayout(contract), controlsRect: [890, 990, 20, 20] };
  assert.throws(() => hitRegions.computeHitRegions(layout), /controlsRect/);
});
