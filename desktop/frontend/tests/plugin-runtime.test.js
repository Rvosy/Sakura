import assert from "node:assert/strict";
import test from "node:test";

import {
  createPluginController,
  validatePluginSnapshot,
} from "../settings/plugin-runtime.js";

function snapshot(coreGenerationId = "generation-a") {
  return {
    schemaVersion: 1,
    revision: "0123456789abcdef",
    state: "ready",
    reasonCode: "READY",
    plugins: [{
      pluginId: "fixture_plugin",
      name: "Fixture Plugin",
      version: "1.0.0",
      author: "Sakura Tests",
      description: "Fixture",
      enabled: true,
      required: false,
      supported: true,
      state: "ready",
      reasonCode: "READY",
      permissions: ["tool"],
      unavailable: [],
      sections: [],
    }],
    windowGeneration: 7,
    coreGenerationId,
  };
}

test("WP-4-04 plugin snapshots are exact and do not expose entry or paths", () => {
  assert.equal(validatePluginSnapshot(snapshot()).plugins[0].pluginId, "fixture_plugin");
  assert.throws(() => validatePluginSnapshot({ ...snapshot(), entry: "private.module:Plugin" }));
  assert.throws(() => validatePluginSnapshot({ ...snapshot(), plugins: [{
    ...snapshot().plugins[0], pluginRoot: "/private/root",
  }] }));
});

test("WP-4-04 plugin save rebinds to the new Core generation", async () => {
  let restarted = false;
  const calls = [];
  const controller = createPluginController({
    invoke: async (command, args) => {
      calls.push([command, args]);
      if (command === "settings_plugins_save") {
        restarted = true;
        return { changePlan: "core_restart_required" };
      }
      if (command === "settings_plugins_get" && restarted) return snapshot("generation-b");
      throw new Error("unexpected call");
    },
    applySnapshot: () => {},
    readDraft: () => ({ enabledById: { fixture_plugin: false }, settingsById: {} }),
    onDirty: () => {},
    wait: async () => {},
  });
  controller.initialize(snapshot());
  await controller.save();
  assert.deepEqual(calls[0], ["settings_plugins_save", {
    windowGeneration: 7,
    coreGenerationId: "generation-a",
    revision: "0123456789abcdef",
    settings: { enabledById: { fixture_plugin: false }, settingsById: {} },
  }]);
  assert.equal(controller.snapshot().coreGenerationId, "generation-b");
});

test("WP-4-04 failed plugin save preserves the page draft", async () => {
  let applied = 0;
  const draft = { enabledById: { fixture_plugin: false }, settingsById: {} };
  const controller = createPluginController({
    invoke: async () => { throw new Error("CONFIG_SAVE_FAILED"); },
    applySnapshot: () => { applied += 1; },
    readDraft: () => draft,
    onDirty: () => {},
  });
  controller.initialize(snapshot());
  await assert.rejects(() => controller.save(), /CONFIG_SAVE_FAILED/);
  assert.equal(applied, 1);
  assert.deepEqual(controller.draft(), draft);
});

test("WP-4-04 plugin actions validate outbound identity and exact bounded results", async () => {
  const controller = createPluginController({
    invoke: async () => ({ values: { label: "reset" }, message: "done" }),
    applySnapshot: () => {},
    readDraft: () => ({ enabledById: {}, settingsById: {} }),
    onDirty: () => {},
  });
  controller.initialize(snapshot());
  assert.deepEqual(await controller.action({
    pluginId: "fixture_plugin", sectionId: "general", actionId: "reset", values: { label: "x" },
  }), { values: { label: "reset" }, message: "done" });
  await assert.rejects(() => controller.action({
    pluginId: "bad/id", sectionId: "general", actionId: "reset", values: {},
  }), /PLUGIN_SETTINGS_ACTION_INVALID/);
});
