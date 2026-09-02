import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { computePetLayout, validateLayoutContract } from "../pet/layout.js";

const hitRegions = await import("../pet/hit-regions.js").catch(() => null);
const contract = validateLayoutContract(
  JSON.parse(await readFile(new URL("../pet/layout-contract.json", import.meta.url), "utf8")),
);

test("composer growth keeps the bubble and input top fixed", () => {
  const compact = computePetLayout(contract, "product", "", {}, { bubbleHeight: 128, inputHeight: 52 });
  const expandedTwo = computePetLayout(contract, "product", "", {}, { bubbleHeight: 128, inputHeight: 124 });
  const expandedThree = computePetLayout(contract, "product", "", {}, { bubbleHeight: 128, inputHeight: 148 });
  assert.deepEqual(expandedTwo.bubbleRect, compact.bubbleRect);
  assert.deepEqual(expandedThree.bubbleRect, compact.bubbleRect);
  assert.equal(expandedTwo.inputRect[1], compact.inputRect[1]);
  assert.equal(expandedThree.inputRect[1], compact.inputRect[1]);
  assert.deepEqual(
    [compact.inputRect[3], expandedTwo.inputRect[3], expandedThree.inputRect[3]],
    [52, 124, 148],
  );
});

test("message-following bubble height may grow past its configured starting height", () => {
  const fixed = computePetLayout(
    contract,
    "product",
    "",
    { bubbleMaxHeight: 122 },
    { bubbleHeight: 220, inputHeight: 52 },
  );
  const expanded = computePetLayout(
    contract,
    "product",
    "",
    { bubbleMaxHeight: 122 },
    { bubbleHeight: 720, bubbleHeightMaximum: 1374, inputHeight: 52 },
  );
  const viewportLimited = computePetLayout(
    contract,
    "product",
    "",
    { bubbleMaxHeight: 122 },
    { bubbleHeight: 1200, bubbleHeightMaximum: 1374, inputHeight: 52 },
  );

  assert.equal(fixed.bubbleRect[3], 122);
  assert.equal(expanded.bubbleRect[3], 720);
  assert.equal(expanded.bubbleRect[1] + expanded.bubbleRect[3], fixed.bubbleRect[1] + fixed.bubbleRect[3]);
  assert.deepEqual(viewportLimited.bubbleRect, [130, 0, 640, 808]);
});

test("maximum layout offsets reserve room for three composer rows", () => {
  const adjustments = { controlPanelVerticalOffset: -200, inputBarOffset: 200 };
  const compact = computePetLayout(
    contract,
    "product",
    "",
    adjustments,
    { bubbleHeight: 128, inputHeight: 52 },
  );
  const expanded = computePetLayout(
    contract,
    "product",
    "",
    adjustments,
    { bubbleHeight: 128, inputHeight: 148 },
  );
  assert.deepEqual(expanded.bubbleRect, compact.bubbleRect);
  assert.equal(expanded.inputRect[1], compact.inputRect[1]);
  assert.equal(compact.bubbleRect[1] + compact.bubbleRect[3], 1008);
  assert.equal(compact.inputRect[1], 1218);
  assert.ok(expanded.inputRect[1] + expanded.inputRect[3] <= contract.viewport.windowSize[1]);
});

test("the product layout exposes deterministic ordered hit regions", () => {
  assert.ok(hitRegions, "hit-region module must exist");
  const model = hitRegions.computeHitRegions(computePetLayout(contract));
  assert.equal(model.state, "product");
  assert.deepEqual(model.interactive, [[130, 818, 640, 52], [730, 690, 30, 30]]);
  assert.deepEqual(model.drag, [[150, 328, 600, 656], [130, 680, 640, 128]]);
  assert.deepEqual(model.neutral, []);
});

test("transparent complement and half-open region boundaries are explicit", () => {
  assert.ok(hitRegions, "hit-region module must exist");
  const model = hitRegions.computeHitRegions(computePetLayout(contract));
  assert.equal(hitRegions.classifyHitPoint(model, [0, 0]), "transparent");
  assert.equal(hitRegions.classifyHitPoint(model, [426, 436]), "drag");
  assert.equal(hitRegions.classifyHitPoint(model, [142, 716]), "drag");
  assert.equal(hitRegions.classifyHitPoint(model, [749, 983]), "drag");
  assert.equal(hitRegions.classifyHitPoint(model, [750, 984]), "transparent");
});

test("scaled portrait head remains in the frontend drag region", () => {
  const model = hitRegions.computeHitRegions(computePetLayout(contract), {
    portraitSourceSize: [600, 656],
    portraitScalePercent: 150,
  });
  assert.deepEqual(model.drag[0], [0, 0, 900, 984]);
  assert.equal(hitRegions.classifyHitPoint(model, [450, 64]), "drag");
  assert.equal(
    hitRegions.shouldStartNativeDrag({
      hitKind: hitRegions.classifyHitPoint(model, [450, 64]),
      button: 0,
      isPrimary: true,
    }),
    true,
  );
});

test("portrait hit region follows contain geometry before appearance scaling", () => {
  const layout = computePetLayout(contract);
  const tall = hitRegions.computeHitRegions(layout, {
    portraitSourceSize: [400, 800],
    portraitScalePercent: 100,
  });
  const wide = hitRegions.computeHitRegions(layout, {
    portraitSourceSize: [1200, 300],
    portraitScalePercent: 100,
  });
  assert.deepEqual(tall.drag[0], [286, 328, 328, 656]);
  assert.deepEqual(wide.drag[0], [150, 834, 600, 150]);
});

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

test("repeated product computation is stateless", () => {
  assert.ok(hitRegions, "hit-region module must exist");
  const states = ["product", "product", "product", "product"];
  const models = states.map((state) =>
    hitRegions.computeHitRegions(computePetLayout(contract, state)),
  );
  assert.deepEqual(models.map((model) => model.state), states);
  assert.deepEqual(models.at(-1).interactive.at(-1), [730, 690, 30, 30]);
});
