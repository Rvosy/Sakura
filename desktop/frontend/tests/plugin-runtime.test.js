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
      installId: "pi_0123456789abcdef01234567",
      pluginId: "fixture_plugin",
      name: "Fixture Plugin",
      version: "1.0.0",
      author: "Sakura Tests",
      description: "Fixture",
      enabled: true,
      required: false,
      supported: true,
      source: "bundled",
      canUninstall: false,
      provides: ["fixture.service"],
      requires: ["sakura.host.settings"],
      missingServices: [],
      state: "active",
      reasonCode: "ACTIVE",
      sections: [{
        sectionId: "general",
        title: "General",
        surface: null,
        reasonCode: "READY",
        fields: [{
          key: "label", label: "Label", type: "string", default: "fixture", description: "",
          options: [], minimum: null, maximum: null, step: null, maxLength: null, required: false,
          placement: "row", actionIds: [], enabledWhen: null, readonly: false, copyable: false, restartRequired: false, value: "fixture",
        }, {
          key: "running", label: "Running", type: "readonly", default: null, description: "",
          options: [], minimum: null, maximum: null, step: null, maxLength: null, required: false,
          placement: "row", actionIds: [], enabledWhen: null, readonly: true, copyable: false, restartRequired: false, value: "ready",
        }],
        values: { label: "fixture", running: "ready" },
        actions: [{ actionId: "reset", label: "Reset", description: "", danger: false }],
        collections: [],
      }],
    }],
    windowGeneration: 7,
    coreGenerationId,
  };
}

function saveResult(changePlan = "applied", applicationState = "applied") {
  return {
    saved: true,
    pluginId: "fixture_plugin",
    sectionId: "general",
    changePlan,
    applicationState,
    applicationReasonCode: applicationState === "applied" ? "READY" : "CORE_RESTART_REQUIRED",
  };
}

function activitySnapshot(state) {
  const current = snapshot();
  const status = { state, label: state, message: `${state} detail` };
  current.plugins[0].sections[0].fields[1] = {
    key: "running", label: "Running", type: "status", default: status, description: "",
    options: [], minimum: null, maximum: null, step: null, maxLength: null, required: false,
    placement: "section_header", actionIds: [], enabledWhen: null, readonly: true, copyable: false,
    restartRequired: false, value: status,
  };
  current.plugins[0].sections[0].values.running = status;
  return current;
}



test("WP-4-04 plugin enable save uses the applied snapshot while the Core is rebinding", async () => {
  const calls = [];
  const applied = [];
  let draft = { enabledById: {}, settingsById: {} };
  const controller = createPluginController({
    invoke: async (command, args) => {
      calls.push([command, args]);
      if (command === "settings_plugins_enabled_set") {
        const next = snapshot();
        next.plugins[0].enabled = false;
        next.plugins[0].state = "disabled";
        next.plugins[0].reasonCode = "PLUGIN_DISABLED";
        return { ...next, managementAction: "enabled_changed",
          installId: next.plugins[0].installId, pluginId: "fixture_plugin", desiredSaved: true,
          applicationState: "applied", applicationReasonCode: "READY" };
      }
      if (command === "settings_plugins_get") throw new Error("SETTINGS_CORE_UNAVAILABLE");
      throw new Error("unexpected call");
    },
    applySnapshot: (next, options) => {
      applied.push([next, options]);
      if (!options.preserveDraft) draft = { enabledById: {}, settingsById: {} };
    },
    readDraft: () => draft,
    onDirty: () => {},
    wait: async () => {},
  });
  controller.initialize(snapshot());
  draft = { enabledById: { fixture_plugin: false }, settingsById: {} };
  const result = await controller.save();
  assert.deepEqual(calls[0], ["settings_plugins_enabled_set", {
    windowGeneration: 7,
    coreGenerationId: "generation-a",
    revision: "0123456789abcdef",
    installId: "pi_0123456789abcdef01234567",
    enabled: false,
  }]);
  assert.equal(controller.snapshot().coreGenerationId, "generation-a");
  assert.equal(controller.snapshot().plugins[0].enabled, false);
  assert.equal(result.applicationState, "applied");
  assert.deepEqual(calls.map(([command]) => command), ["settings_plugins_enabled_set"]);
  assert.deepEqual(controller.draft(), { enabledById: {}, settingsById: {} });
  assert.equal(applied.length, 2);
  assert.deepEqual(applied[1][1], { preserveDraft: false, draft: null });
});

test("unchanged plugin polling does not reapply the snapshot or repaint settings", async () => {
  let applied = 0;
  let next = snapshot();
  const controller = createPluginController({
    invoke: async () => next,
    applySnapshot: () => { applied += 1; },
    readDraft: () => ({ enabledById: {}, settingsById: {} }),
    onDirty: () => {},
  });
  controller.initialize(snapshot());

  await controller.refreshCurrent();
  assert.equal(applied, 1);

  next = snapshot();
  next.state = "active";
  next.reasonCode = "ACTIVE";
  await controller.refreshCurrent();
  assert.equal(applied, 2);
});


test("concurrent plugin polling shares one settings read", async () => {
  let calls = 0;
  let resolveRead;
  const read = new Promise((resolve) => { resolveRead = resolve; });
  const controller = createPluginController({
    invoke: async (command) => {
      assert.equal(command, "settings_plugins_get");
      calls += 1;
      return read;
    },
    applySnapshot: () => {},
    readDraft: () => ({ enabledById: {}, settingsById: {} }),
    onDirty: () => {},
  });
  controller.initialize(snapshot());

  const first = controller.refreshCurrent();
  const second = controller.refreshCurrent();
  resolveRead(snapshot());
  await Promise.all([first, second]);

  assert.equal(calls, 1);
});

test("failed plugin polling rejects once and a later poll can recover", async () => {
  let calls = 0;
  let applied = 0;
  const controller = createPluginController({
    invoke: async () => {
      calls += 1;
      if (calls === 1) throw new Error("temporarily unavailable");
      return activitySnapshot("ready");
    },
    applySnapshot: () => { applied += 1; },
    readDraft: () => ({ enabledById: {}, settingsById: {} }),
    onDirty: () => {},
  });
  controller.initialize(activitySnapshot("working"));

  await assert.rejects(() => controller.refreshCurrent(), /temporarily unavailable/);
  assert.equal(calls, 1);
  assert.equal(applied, 1);

  await controller.refreshCurrent();
  assert.equal(calls, 2);
  assert.equal(applied, 2);
});


test("Local plugin install and uninstall preserve the draft and validate identity", async () => {
  const calls = [];
  let draft = { enabledById: { fixture_plugin: false }, settingsById: {} };
  const installed = snapshot();
  installed.revision = "1111111111111111";
  installed.plugins.push({
    ...installed.plugins[0],
    installId: "pi_111111111111111111111111",
    pluginId: "com.example.local",
    name: "Local",
    enabled: false,
    state: "disabled",
    reasonCode: "PLUGIN_DISABLED",
    source: "user",
    canUninstall: true,
    sections: [],
  });
  const controller = createPluginController({
    invoke: async (command, args) => {
      calls.push([command, args]);
      if (command === "settings_plugins_install") {
        return { ...installed, managementAction: "installed",
          installId: "pi_111111111111111111111111", pluginId: "com.example.local" };
      }
      return { ...snapshot(), managementAction: "uninstalled",
        installId: "pi_111111111111111111111111", pluginId: "com.example.local" };
    },
    applySnapshot: () => {},
    readDraft: () => draft,
    onDirty: () => {},
  });
  controller.initialize(snapshot());

  const installResult = await controller.install("zip");
  assert.equal(installResult.pluginId, "com.example.local");
  assert.deepEqual(calls[0], ["settings_plugins_install", {
    windowGeneration: 7,
    coreGenerationId: "generation-a",
    revision: "0123456789abcdef",
    sourceKind: "zip",
  }]);

  draft = { enabledById: {}, settingsById: {} };
  await controller.uninstall("pi_111111111111111111111111");
  assert.deepEqual(calls[1][1], {
    windowGeneration: 7,
    coreGenerationId: "generation-a",
    revision: "1111111111111111",
    installId: "pi_111111111111111111111111",
  });
});

test("Plugin uninstall cleanup failure refreshes the committed snapshot", async () => {
  const current = snapshot();
  current.revision = "1111111111111111";
  current.plugins.push({
    ...current.plugins[0], installId: "pi_111111111111111111111111",
    pluginId: "com.example.local", name: "Local", enabled: false,
    state: "disabled", reasonCode: "PLUGIN_DISABLED", source: "user", canUninstall: true,
    sections: [],
  });
  const refreshed = snapshot();
  refreshed.revision = "2222222222222222";
  const calls = [];
  const controller = createPluginController({
    invoke: async (command) => {
      calls.push(command);
      if (command === "settings_plugins_uninstall") throw new Error("PLUGIN_UNINSTALL_CLEANUP_FAILED");
      if (command === "settings_plugins_get") return refreshed;
      throw new Error("unexpected call");
    },
    applySnapshot: () => {},
    readDraft: () => ({ enabledById: {}, settingsById: {} }),
    onDirty: () => {},
  });
  controller.initialize(current);

  await assert.rejects(
    () => controller.uninstall("pi_111111111111111111111111"),
    /PLUGIN_UNINSTALL_CLEANUP_FAILED/,
  );
  assert.deepEqual(calls, ["settings_plugins_uninstall", "settings_plugins_get"]);
  assert.equal(controller.snapshot().revision, "2222222222222222");
  assert.equal(controller.snapshot().plugins.some((plugin) => plugin.source === "user"), false);
});

test("Plugin install revision conflict after picker refreshes the current revision", async () => {
  const refreshed = snapshot();
  refreshed.revision = "3333333333333333";
  const calls = [];
  const controller = createPluginController({
    invoke: async (command) => {
      calls.push(command);
      if (command === "settings_plugins_install") throw new Error("CONFIG_REVISION_CONFLICT");
      if (command === "settings_plugins_get") return refreshed;
      throw new Error("unexpected call");
    },
    applySnapshot: () => {},
    readDraft: () => ({ enabledById: {}, settingsById: {} }),
    onDirty: () => {},
  });
  controller.initialize(snapshot());

  await assert.rejects(() => controller.install("folder"), /CONFIG_REVISION_CONFLICT/);
  assert.deepEqual(calls, ["settings_plugins_install", "settings_plugins_get"]);
  assert.equal(controller.snapshot().revision, "3333333333333333");
});

test("Plugin management refresh failure preserves the original error", async () => {
  const current = snapshot();
  current.plugins.push({
    ...current.plugins[0], installId: "pi_111111111111111111111111",
    pluginId: "com.example.local", name: "Local", enabled: false,
    state: "disabled", reasonCode: "PLUGIN_DISABLED", source: "user", canUninstall: true,
    sections: [],
  });
  const managementError = new Error("PLUGIN_UNINSTALL_CLEANUP_FAILED");
  const calls = [];
  const controller = createPluginController({
    invoke: async (command) => {
      calls.push(command);
      if (command === "settings_plugins_uninstall") throw managementError;
      throw new Error("refresh unavailable");
    },
    applySnapshot: () => {},
    readDraft: () => ({ enabledById: {}, settingsById: {} }),
    onDirty: () => {},
    wait: async () => { throw new Error("stop refresh retry"); },
  });
  controller.initialize(current);

  let caught;
  try {
    await controller.uninstall("pi_111111111111111111111111");
  } catch (error) {
    caught = error;
  }
  assert.equal(caught, managementError);
  assert.deepEqual(calls, ["settings_plugins_uninstall", "settings_plugins_get"]);
});

test("Cancelled plugin picker does not mutate the current snapshot", async () => {
  const controller = createPluginController({
    invoke: async () => ({ cancelled: true }),
    applySnapshot: () => {},
    readDraft: () => ({ enabledById: {}, settingsById: {} }),
    onDirty: () => {},
  });
  controller.initialize(snapshot());
  assert.equal(await controller.install("folder"), null);
  assert.equal(controller.snapshot().revision, "0123456789abcdef");
});

test("Plugin management rejects mismatched actions and bundled uninstall", async () => {
  const invalid = createPluginController({
    invoke: async () => ({
      ...snapshot(), managementAction: "uninstalled", pluginId: "fixture_plugin",
      installId: "pi_0123456789abcdef01234567",
    }),
    applySnapshot: () => {},
    readDraft: () => ({ enabledById: {}, settingsById: {} }),
    onDirty: () => {},
  });
  invalid.initialize(snapshot());
  await assert.rejects(() => invalid.install("zip"), /PLUGIN_MANAGEMENT_RESPONSE_INVALID/);
  await assert.rejects(() => invalid.uninstall("pi_0123456789abcdef01234567"), /PLUGIN_UNINSTALL_REQUEST_INVALID/);
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




test("Plugin API v3 applied settings refresh without changing the Core generation", async () => {
  const calls = [];
  const active = snapshot();
  active.plugins[0].state = "active";
  active.plugins[0].reasonCode = "ACTIVE";
  const controller = createPluginController({
    invoke: async (command, args) => {
      calls.push([command, args]);
      if (command === "settings_plugins_save") return saveResult("applied", "applied");
      if (command === "settings_plugins_get") return active;
      throw new Error("unexpected call");
    },
    applySnapshot: () => {},
    readDraft: () => ({ enabledById: {}, settingsById: { fixture_plugin: { general: { label: "v3" } } } }),
    onDirty: () => {},
  });
  controller.initialize(active);

  const result = await controller.save();

  assert.equal(result.coreGenerationId, "generation-a");
  assert.equal(result.changePlan, "applied");
  assert.deepEqual(calls.map(([command]) => command), ["settings_plugins_save", "settings_plugins_get"]);
});

test("Plugin API v3 restart-required config is applied by local plugin reload", async () => {
  const calls = [];
  const active = snapshot();
  active.plugins[0].state = "active";
  active.plugins[0].reasonCode = "ACTIVE";
  const controller = createPluginController({
    invoke: async (command, args) => {
      calls.push([command, args]);
      if (command === "settings_plugins_save") {
        return {
          saved: true,
          pluginId: "fixture_plugin",
          sectionId: "general",
          changePlan: "applied",
          applicationState: "applied",
          applicationReasonCode: "READY",
        };
      }
      if (command === "settings_plugins_get") return active;
      throw new Error("unexpected call");
    },
    applySnapshot: () => {},
    readDraft: () => ({ enabledById: {}, settingsById: { fixture_plugin: { general: { label: "v3" } } } }),
    onDirty: () => {},
  });
  controller.initialize(active);

  const result = await controller.save();

  assert.equal(controller.snapshot().coreGenerationId, "generation-a");
  assert.equal(result.applicationState, "applied");
  assert.deepEqual(calls.map(([command]) => command), ["settings_plugins_save", "settings_plugins_get"]);
});
