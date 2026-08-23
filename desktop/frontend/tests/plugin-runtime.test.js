import assert from "node:assert/strict";
import test from "node:test";

import {
  createPluginController,
  validatePluginSnapshot,
} from "../settings/plugin-runtime.js";

function snapshot(coreGenerationId = "generation-a") {
  return {
    schemaVersion: 2,
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
          placement: "row", actionIds: [], readonly: false, copyable: false, restartRequired: false, value: "fixture",
        }, {
          key: "running", label: "Running", type: "readonly", default: null, description: "",
          options: [], minimum: null, maximum: null, step: null, maxLength: null, required: false,
          placement: "row", actionIds: [], readonly: true, copyable: false, restartRequired: false, value: "ready",
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

test("WP-4-04 plugin snapshots are exact and do not expose entry or paths", () => {
  assert.equal(validatePluginSnapshot(snapshot()).plugins[0].pluginId, "fixture_plugin");
  assert.throws(() => validatePluginSnapshot({ ...snapshot(), schemaVersion: 1 }));
  assert.throws(() => validatePluginSnapshot({ ...snapshot(), entry: "private.module:Plugin" }));
  assert.throws(() => validatePluginSnapshot({ ...snapshot(), plugins: [{
    ...snapshot().plugins[0], pluginRoot: "/private/root",
  }] }));
});

test("Plugin status and resource fields validate bounded semantic state", () => {
  const current = snapshot();
  const section = current.plugins[0].sections[0];
  section.actions.push(
    { actionId: "download", label: "Download", description: "", danger: false },
    { actionId: "cancel", label: "Cancel", description: "", danger: false },
  );
  const statusValue = { state: "ready", label: "Running", message: "" };
  const resourceValue = {
    subtitle: "sentence-transformers/all-MiniLM-L6-v2",
    ready: false,
    taskState: "running",
    message: "Downloading",
    detail: "Model files",
    progress: 55,
    availableActionIds: ["cancel"],
  };
  section.fields.push({
    key: "health", label: "Health", type: "status", default: statusValue, description: "",
    options: [], minimum: null, maximum: null, step: null, maxLength: null,
    placement: "section_header", actionIds: [], required: false, readonly: true,
    copyable: false, restartRequired: false, value: statusValue,
  }, {
    key: "model", label: "Model", type: "resource", default: resourceValue, description: "",
    options: [], minimum: null, maximum: null, step: null, maxLength: null,
    placement: "row", actionIds: ["download", "cancel"], required: false, readonly: true,
    copyable: false, restartRequired: false, value: resourceValue,
  });
  section.values.health = statusValue;
  section.values.model = resourceValue;
  assert.equal(validatePluginSnapshot(current).plugins[0].sections[0].fields[3].type, "resource");

  const invalidProgress = structuredClone(current);
  invalidProgress.plugins[0].sections[0].fields[3].value.progress = 101;
  assert.throws(() => validatePluginSnapshot(invalidProgress));
  const unknownAction = structuredClone(current);
  unknownAction.plugins[0].sections[0].fields[3].value.availableActionIds = ["unknown"];
  assert.throws(() => validatePluginSnapshot(unknownAction));

  const duplicateAction = structuredClone(current);
  duplicateAction.plugins[0].sections[0].fields[3].value.availableActionIds = ["cancel", "cancel"];
  duplicateAction.plugins[0].sections[0].values.model.availableActionIds = ["cancel", "cancel"];
  assert.throws(() => validatePluginSnapshot(duplicateAction));

  const inconsistentValue = structuredClone(current);
  inconsistentValue.plugins[0].sections[0].values.model = {
    ...inconsistentValue.plugins[0].sections[0].values.model,
    progress: 54,
  };
  assert.throws(() => validatePluginSnapshot(inconsistentValue));
});

test("WP-4-04 plugin save refreshes without changing the Core generation", async () => {
  const calls = [];
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
      if (command === "settings_plugins_get") return snapshot();
      throw new Error("unexpected call");
    },
    applySnapshot: () => {},
    readDraft: () => ({ enabledById: { fixture_plugin: false }, settingsById: {} }),
    onDirty: () => {},
    wait: async () => {},
  });
  controller.initialize(snapshot());
  await controller.save();
  assert.deepEqual(calls[0], ["settings_plugins_enabled_set", {
    windowGeneration: 7,
    coreGenerationId: "generation-a",
    revision: "0123456789abcdef",
    installId: "pi_0123456789abcdef01234567",
    enabled: false,
  }]);
  assert.equal(controller.snapshot().coreGenerationId, "generation-a");
  assert.deepEqual(calls.map(([command]) => command), ["settings_plugins_enabled_set", "settings_plugins_get"]);
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

test("Plugin settings reject the removed Core restart change plan", async () => {
  const controller = createPluginController({
    invoke: async () => saveResult("core_restart_required", "restart_required"),
    applySnapshot: () => {},
    readDraft: () => ({ enabledById: {}, settingsById: {
      fixture_plugin: { general: { label: "changed" } },
    } }),
    onDirty: () => {},
  });
  controller.initialize(snapshot());
  await assert.rejects(() => controller.save(), /PLUGIN_SETTINGS_CHANGE_PLAN_INVALID/);
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

test("WP-4-04 plugin actions validate outbound identity and exact bounded results", async () => {
  const calls = [];
  const controller = createPluginController({
    invoke: async (command, args) => {
      calls.push([command, args]);
      return { values: { running: "ready" }, message: "done" };
    },
    applySnapshot: () => {},
    readDraft: () => ({ enabledById: {}, settingsById: {} }),
    onDirty: () => {},
  });
  controller.initialize(snapshot());
  assert.deepEqual(await controller.action({
    pluginId: "fixture_plugin", sectionId: "general", actionId: "reset",
    values: { label: "x", running: "stale client status" },
  }), { values: { running: "ready" }, message: "done" });
  assert.deepEqual(calls[0][1].values, { label: "x" });
  await assert.rejects(() => controller.action({
    pluginId: "bad/id", sectionId: "general", actionId: "reset", values: {},
  }), /PLUGIN_SETTINGS_ACTION_INVALID/);
});

test("WP-4-04 plugin save excludes readonly status values from the worker request", async () => {
  const calls = [];
  const controller = createPluginController({
    invoke: async (command, args) => {
      calls.push([command, args]);
      if (command === "settings_plugins_save") return saveResult();
      return snapshot("generation-b");
    },
    applySnapshot: () => {},
    readDraft: () => ({
      enabledById: {},
      settingsById: { fixture_plugin: { general: { label: "changed", running: "stale" } } },
    }),
    onDirty: () => {},
    wait: async () => {},
  });
  controller.initialize(snapshot());
  await controller.save();
  assert.deepEqual(calls[0][1], {
    windowGeneration: 7,
    coreGenerationId: "generation-a",
    pluginId: "fixture_plugin",
    sectionId: "general",
    values: { label: "changed" },
  });
});

test("Plugin collections use bounded generic CRUD requests and exact results", async () => {
  const current = snapshot();
  current.plugins[0].sections[0].collections = [{
    collectionId: "entries",
    title: "Entries",
    description: "Fixture rows",
    columns: [{ key: "content", label: "Content", type: "string", maxLength: 16_384 }],
    fields: [{
      key: "content", label: "Content", type: "string", default: null, description: "", options: [],
      minimum: null, maximum: null, step: null, maxLength: 16_384, required: true, readonly: false, copyable: false,
      placement: "row", actionIds: [], restartRequired: false,
    }],
    filters: [],
    searchable: true,
    pageSize: 25,
    canCreate: true,
    canUpdate: true,
    canDelete: true,
    deleteConfirmation: "Delete this row?",
  }];
  const calls = [];
  const controller = createPluginController({
    invoke: async (command, args) => {
      calls.push([command, args]);
      if (args.operation === "query") {
        return { items: [{ itemId: "one", values: { content: "hello" } }], nextCursor: null, total: 1 };
      }
      if (args.operation === "create") return { itemId: "two", values: { content: args.payload.values.content } };
      if (args.operation === "update") return { itemId: args.payload.itemId, values: args.payload.values };
      return { deleted: true };
    },
    applySnapshot: () => {},
    readDraft: () => ({ enabledById: {}, settingsById: {} }),
    onDirty: () => {},
  });
  controller.initialize(current);
  assert.equal((await controller.collection({
    operation: "query", pluginId: "fixture_plugin", sectionId: "general", collectionId: "entries",
    cursor: null, limit: 25, search: "hello", filters: {},
  })).total, 1);
  assert.equal((await controller.collection({
    operation: "create", pluginId: "fixture_plugin", sectionId: "general", collectionId: "entries",
    values: { content: "new" },
  })).itemId, "two");
  assert.equal(calls[0][0], "settings_plugins_collection");
  assert.deepEqual(calls[0][1], {
    windowGeneration: 7,
    coreGenerationId: "generation-a",
    operation: "query",
    pluginId: "fixture_plugin",
    sectionId: "general",
    collectionId: "entries",
    payload: { cursor: null, limit: 25, search: "hello", filters: {} },
  });
  await assert.rejects(() => controller.collection({
    operation: "query", pluginId: "fixture_plugin", sectionId: "general", collectionId: "entries",
    limit: 101, search: "", filters: {},
  }), /PLUGIN_COLLECTION_REQUEST_INVALID/);
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

test("Plugin API v3 restart-required config is applied after the Worker rebuild", async () => {
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
