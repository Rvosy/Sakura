import assert from "node:assert/strict";
import test from "node:test";

import { validateCapabilityManifest } from "../settings/capability-shell.js";

function manifest(overrides = {}) {
  return {
    schemaVersion: 1,
    windowGeneration: 1,
    availableSections: [],
    readOnlySections: [],
    unavailableReasons: {
      character: "该设置能力尚未迁移到 Runtime v2",
    },
    ...overrides,
  };
}

test("capability manifest accepts the shell-only WP-3U-01 contract", () => {
  const value = validateCapabilityManifest(manifest({ windowGeneration: 9 }));
  assert.equal(value.windowGeneration, 9);
  assert.deepEqual(value.availableSections, []);
  assert.deepEqual(value.readOnlySections, []);
});

test("capability manifest rejects invalid generations, sections, and sensitive fields", () => {
  assert.throws(() => validateCapabilityManifest(manifest({ windowGeneration: 0 })), /generation/);
  assert.throws(() => validateCapabilityManifest(manifest({ availableSections: [""] })), /sections/);
  assert.throws(() => validateCapabilityManifest({ ...manifest(), apiKey: "forbidden" }), /sensitive/);
});

test("capability manifest copies section collections instead of trusting mutable host data", () => {
  const source = manifest({ availableSections: ["character", "character"] });
  const value = validateCapabilityManifest(source);
  source.availableSections.push("providers");
  assert.deepEqual(value.availableSections, ["character"]);
  assert.equal(Object.isFrozen(value.availableSections), true);
});

test("capability schema v2 fails unknown feature states safe and exposes migrated features", () => {
  const value = validateCapabilityManifest({
    schemaVersion: 2,
    windowGeneration: 11,
    sections: {
      providers: {
        status: "available",
        features: {
          "providers.manage": "available",
          "providers.credentials": "future-state",
        },
      },
      model: { status: "mystery", features: {} },
    },
    unavailableReasons: { "providers.credentials": "不可用" },
  });
  assert.equal(value.sections.providers.features["providers.manage"], "available");
  assert.equal(value.sections.providers.features["providers.credentials"], "unavailable");
  assert.equal(value.sections.model.status, "unavailable");
  assert.deepEqual(value.availableSections, ["providers"]);
});
