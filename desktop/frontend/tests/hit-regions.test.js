import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { computePetLayout, validateLayoutContract } from "../pet/layout.js";

const hitRegions = await import("../pet/hit-regions.js").catch(() => null);
const contract = validateLayoutContract(
  JSON.parse(await readFile(new URL("../pet/layout-contract.json", import.meta.url), "utf8")),
);

test("the product layout exposes deterministic ordered hit regions", () => {
  assert.ok(hitRegions, "hit-region module must exist");
  const model = hitRegions.computeHitRegions(computePetLayout(contract));
  assert.equal(model.state, "product");
  assert.deepEqual(model.interactive, [[88, 502, 640, 52], [688, 374, 30, 30]]);
  assert.deepEqual(model.drag, [[108, 12, 600, 656], [88, 364, 640, 128]]);
  assert.deepEqual(model.neutral, []);
});

test("transparent complement and half-open region boundaries are explicit", () => {
  assert.ok(hitRegions, "hit-region module must exist");
  const model = hitRegions.computeHitRegions(computePetLayout(contract));
  assert.equal(hitRegions.classifyHitPoint(model, [0, 0]), "transparent");
  assert.equal(hitRegions.classifyHitPoint(model, [384, 120]), "drag");
  assert.equal(hitRegions.classifyHitPoint(model, [707, 667]), "drag");
  assert.equal(hitRegions.classifyHitPoint(model, [708, 668]), "transparent");
});

test("interactive controls win over an overlapping portrait drag region", () => {
  assert.ok(hitRegions, "hit-region module must exist");
  const product = hitRegions.computeHitRegions(computePetLayout(contract));
  assert.equal(hitRegions.classifyHitPoint(product, [700, 380]), "interactive");
  assert.equal(hitRegions.classifyHitPoint(product, [200, 520]), "interactive");
  assert.notEqual(hitRegions.classifyHitPoint(product, [200, 520]), "drag");
  assert.equal(hitRegions.classifyHitPoint(product, [300, 200]), "drag");
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
  const hitKind = hitRegions.classifyPointerHit({
    model,
    point: [200, 400],
    interactiveTarget: true,
  });
  assert.equal(hitKind, "interactive");
  assert.equal(hitRegions.shouldStartNativeDrag({ hitKind, button: 0, isPrimary: true }), false);
});

test("invalid, ambiguous, or out-of-envelope rectangles fail closed", () => {
  assert.ok(hitRegions, "hit-region module must exist");
  assert.throws(
    () => hitRegions.computeHitRegions({ state: "product", windowSize: [816, 680] }),
    /portraitRect/,
  );
  const layout = { ...computePetLayout(contract), controlsRect: [810, 670, 20, 20] };
  assert.throws(() => hitRegions.computeHitRegions(layout), /controlsRect/);
});

test("repeated product computation is stateless", () => {
  assert.ok(hitRegions, "hit-region module must exist");
  const states = ["product", "product", "product", "product"];
  const models = states.map((state) =>
    hitRegions.computeHitRegions(computePetLayout(contract, state)),
  );
  assert.deepEqual(models.map((model) => model.state), states);
  assert.deepEqual(models.at(-1).interactive.at(-1), [688, 374, 30, 30]);
});
