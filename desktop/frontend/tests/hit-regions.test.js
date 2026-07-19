import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { computePetLayout, PRESENTATION_STATES, validateLayoutContract } from "../pet/layout.js";

const hitRegions = await import("../pet/hit-regions.js").catch(() => null);
const contract = validateLayoutContract(
  JSON.parse(await readFile(new URL("../pet/layout-contract.json", import.meta.url), "utf8")),
);

test("all presentation states expose deterministic ordered hit regions", () => {
  assert.ok(hitRegions, "hit-region module must exist");
  for (const state of PRESENTATION_STATES) {
    const model = hitRegions.computeHitRegions(computePetLayout(contract, state));
    assert.equal(model.state, state);
    assert.equal(model.drag.length, state === "idle" ? 1 : 2);
    assert.deepEqual(model.drag[0], [360, 332, 240, 336]);
    assert.equal(model.interactive.length, state === "idle" || state === "bubble" ? 1 : 2);
    assert.deepEqual(model.neutral, []);
  }
});

test("transparent complement and half-open region boundaries are explicit", () => {
  assert.ok(hitRegions, "hit-region module must exist");
  const model = hitRegions.computeHitRegions(computePetLayout(contract, "idle"));
  assert.equal(hitRegions.classifyHitPoint(model, [0, 0]), "transparent");
  assert.equal(hitRegions.classifyHitPoint(model, [360, 332]), "drag");
  assert.equal(hitRegions.classifyHitPoint(model, [599, 667]), "drag");
  assert.equal(hitRegions.classifyHitPoint(model, [600, 668]), "transparent");
});

test("interactive controls win over an overlapping portrait drag region", () => {
  assert.ok(hitRegions, "hit-region module must exist");
  const idle = hitRegions.computeHitRegions(computePetLayout(contract, "idle"));
  assert.equal(hitRegions.classifyHitPoint(idle, [400, 640]), "interactive");

  const composer = hitRegions.computeHitRegions(computePetLayout(contract, "composer"));
  assert.equal(hitRegions.classifyHitPoint(composer, [200, 560]), "interactive");
  assert.notEqual(hitRegions.classifyHitPoint(composer, [200, 560]), "drag");
  assert.equal(hitRegions.classifyHitPoint(composer, [300, 420]), "drag");
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

test("invalid, ambiguous, or out-of-envelope rectangles fail closed", () => {
  assert.ok(hitRegions, "hit-region module must exist");
  assert.throws(
    () => hitRegions.computeHitRegions({ state: "idle", windowSize: [816, 680] }),
    /portraitRect/,
  );
  const layout = { ...computePetLayout(contract, "idle"), controlsRect: [810, 670, 20, 20] };
  assert.throws(() => hitRegions.computeHitRegions(layout), /controlsRect/);
});

test("rapid state computation is stateless and cannot retain an older region model", () => {
  assert.ok(hitRegions, "hit-region module must exist");
  const states = ["bubble", "idle", "composer", "expanded"];
  const models = states.map((state) =>
    hitRegions.computeHitRegions(computePetLayout(contract, state)),
  );
  assert.deepEqual(models.map((model) => model.state), states);
  assert.deepEqual(models.at(-1).interactive.at(-1), [8, 630, 193, 38]);
});
