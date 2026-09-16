import assert from "node:assert/strict";
import test from "node:test";

import {
  inputVisualEffectModes,
  validateCapabilityManifest,
} from "../settings/capability-shell.js";
import { createInputPresentationQueue } from "../pet/input-visual-effect.js";

function manifest({ gaussian = true, liquid = true } = {}) {
  return validateCapabilityManifest({
    schemaVersion: 1,
    windowGeneration: 1,
    sections: {
      appearance: {
        status: "available",
        features: {
          "appearance.input_visual_effect": "available",
          "appearance.input_visual_effect.gaussian_blur": gaussian ? "available" : "unavailable",
          "appearance.input_visual_effect.liquid_glass": liquid ? "available" : "unavailable",
        },
      },
    },
    unavailableReasons: {
      "appearance.input_visual_effect.liquid_glass": "需要 macOS 26 或更高版本",
    },
  });
}

test("input presentation commands skip stale work and never overlap", async () => {
  let currentRevision = 1;
  let releaseFirst;
  let active = 0;
  let maximumActive = 0;
  const calls = [];
  const queue = createInputPresentationQueue({
    isCurrent: (revision) => revision === currentRevision,
    apply: async (presented) => {
      calls.push(presented);
      active += 1;
      maximumActive = Math.max(maximumActive, active);
      if (calls.length === 1) {
        await new Promise((resolve) => { releaseFirst = resolve; });
      }
      active -= 1;
    },
  });

  const hiding = queue.schedule(false, 1);
  await Promise.resolve();
  currentRevision = 2;
  const showing = queue.schedule(true, 2);
  releaseFirst();
  assert.equal(await hiding, true);
  assert.equal(await showing, true);
  assert.deepEqual(calls, [false, true]);
  assert.equal(maximumActive, 1);

  currentRevision = 3;
  const stale = queue.schedule(true, 2);
  assert.equal(await stale, false);
  assert.deepEqual(calls, [false, true]);
});

test("macOS 26 exposes both native visual modes", () => {
  const modes = inputVisualEffectModes(manifest());
  assert.deepEqual(modes.map(({ id, disabled }) => [id, disabled]), [
    ["solid", false],
    ["gaussian_blur", false],
    ["liquid_glass", false],
  ]);
});

test("older macOS locks liquid glass without locking gaussian blur", () => {
  const modes = inputVisualEffectModes(manifest({ liquid: false }));
  assert.equal(modes.find(({ id }) => id === "gaussian_blur").disabled, false);
  assert.deepEqual(
    modes.find(({ id }) => id === "liquid_glass"),
    {
      id: "liquid_glass",
      label: "液态玻璃",
      disabled: true,
      reason: "需要 macOS 26 或更高版本",
    },
  );
});

test("solid remains selectable when both native visual effects are unavailable", () => {
  const modes = inputVisualEffectModes(manifest({ gaussian: false, liquid: false }));
  assert.deepEqual(modes.map(({ id, disabled }) => [id, disabled]), [
    ["solid", false],
    ["gaussian_blur", true],
    ["liquid_glass", true],
  ]);
});

test("schema 1 rejects the retired section-list shape", () => {
  assert.throws(
    () => validateCapabilityManifest({
      schemaVersion: 1,
      windowGeneration: 1,
      availableSections: ["appearance"],
      readOnlySections: [],
      unavailableReasons: {},
    }),
    /missing settings capability field: sections/,
  );
});

test("schema 1 rejects unknown section fields instead of normalizing them", () => {
  const current = manifest();
  assert.throws(
    () => validateCapabilityManifest({
      schemaVersion: 1,
      windowGeneration: 1,
      sections: {
        appearance: {
          status: "available",
          features: {},
          available: true,
        },
      },
      unavailableReasons: current.unavailableReasons,
    }),
    /section fields/,
  );
});
