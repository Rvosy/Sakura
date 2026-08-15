import assert from "node:assert/strict";
import test from "node:test";

import {
  inputVisualEffectModes,
  validateCapabilityManifest,
} from "../settings/capability-shell.js";

function manifest({ gaussian = true, liquid = true } = {}) {
  return validateCapabilityManifest({
    schemaVersion: 2,
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
