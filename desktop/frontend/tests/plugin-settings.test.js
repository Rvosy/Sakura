import assert from "node:assert/strict";
import test from "node:test";
import { executeSettingsClose } from "../settings/close-flow.js";
import { field, snapshot, featureFixture, queryResult, settle } from "./fixtures/plugin-settings-fixture.js";

for (const surface of ["memory", null]) {
  test(`an open ${surface || "plugin"} collection draft prevents silent Settings close`, async () => {
    const data = snapshot();
    data.plugins[0].sections[1].surface = surface;
    const { feature, document, dirtyNotifications } = featureFixture(async () => {
      assert.fail("a Settings save must not submit or discard an open collection editor");
    });
    feature.initialize(data);
    assert.equal(feature.hasCollectionDrafts(), false);
    if (surface !== "memory") await document.querySelector(".plugin-configure").fire("click");
    const beforeEdit = dirtyNotifications();
    const add = document.querySelector(surface === "memory" ? ".memory-add-button" : ".plugin-collection-head button");
    await add.fire("click");
    assert.equal(feature.hasCollectionDrafts(), true);
    assert.equal(feature.characterDraftCount(), surface === "memory" ? 1 : 0);
    let choices = 0;
    let closed = false;
    const decision = await executeSettingsClose({
      dirty: feature.isDirty(),
      choose: async () => { choices += 1; return "stay"; },
      save: feature.save,
      discard: feature.discard,
      close: async () => { closed = true; },
    });
    assert.equal(decision, "stay");
    assert.equal(choices, 1);
    assert.equal(closed, false);
    assert.ok(dirtyNotifications() > beforeEdit);
    await assert.rejects(() => feature.save(), /集合/);
    assert.equal(feature.isDirty(), true);
    feature.discard();
    assert.equal(feature.hasCollectionDrafts(), false);
    assert.equal(feature.isDirty(), false);
    feature.dispose();
  });
}

test("plugin feature retains Memory editors and detaches old-generation queries and callbacks", async () => {
  let nextSnapshot = snapshot();
  let resolveOldQuery;
  const queries = [];
  const fixture = featureFixture(async (command, args) => {
    if (command === "settings_plugins_get") return nextSnapshot;
    assert.equal(command, "settings_plugins_collection");
    queries.push(args);
    if (queries.length === 2) return new Promise((resolve) => { resolveOldQuery = resolve; });
    return queryResult(queries.length === 1 ? "note" : "fresh");
  });
  const { feature, document, timers, runTimers } = fixture;
  feature.initialize(nextSnapshot);
  await runTimers(0);
  const search = document.querySelector(".memory-search-input");
  search.value = "旅行";
  await search.fire("input");
  const staleCallback = [...timers.values()].find((timer) => timer.milliseconds === 220).callback;
  await document.querySelector(".memory-record-card").fire("dblclick");
  const editor = document.querySelector(".memory-editor-overlay textarea");
  editor.value = "未保存的记忆";
  await editor.fire("input");
  await runTimers(220);
  assert.equal(queries.length, 2);

  nextSnapshot = snapshot("generation-b");
  await feature.refreshCurrent();
  assert.equal(feature.characterDraftCount(), 1);
  assert.equal(document.querySelector(".memory-search-input").value, "旅行");
  assert.equal(document.querySelector(".memory-editor-overlay textarea").value, "未保存的记忆");
  resolveOldQuery(queryResult("stale"));
  await settle();
  assert.equal(document.querySelectorAll(".memory-record-card").length, 0);
  staleCallback();
  await settle();
  assert.equal(queries.length, 2, "an old scheduled callback cannot query the new generation");

  await runTimers(0);
  assert.equal(queries.length, 3);
  assert.equal(queries.at(-1).coreGenerationId, "generation-b");
  assert.equal(queries.at(-1).payload.search, "旅行");
  assert.equal(document.querySelector(".memory-record-card").dataset.itemId, "fresh");
  assert.equal(document.querySelector(".memory-editor-overlay textarea").value, "未保存的记忆");

  await document.querySelector(".memory-dialog-close").fire("click");
  nextSnapshot = snapshot("generation-c");
  await feature.refreshCurrent();
  assert.equal(feature.characterDraftCount(), 0, "refreshing a closed editor must not create a phantom draft");
  assert.equal(document.querySelector(".memory-editor-overlay"), null);
  await document.querySelector(".memory-add-button").fire("click");
  assert.equal(feature.characterDraftCount(), 1);
  feature.discard();
  assert.equal(feature.characterDraftCount(), 0);
  assert.equal(document.querySelector(".memory-editor-overlay"), null);
  feature.dispose();
  assert.equal(timers.size, 0);
  assert.deepEqual(fixture.errors, []);
});

for (const outcome of ["success", "failure"]) {
  test(`a late collection ${outcome} cannot change a rebound Memory editor`, async () => {
    let nextSnapshot = snapshot();
    let completeMutation;
    const fixture = featureFixture(async (command, args) => {
      if (command === "settings_plugins_get") return nextSnapshot;
      if (args.operation === "query") return queryResult("note");
      return new Promise((resolve, reject) => {
        completeMutation = () => outcome === "success"
          ? resolve({ itemId: "note", values: args.payload.values })
          : reject(new Error("STALE_WRITE_FAILURE"));
      });
    });
    const { feature, document, runTimers } = fixture;
    feature.initialize(nextSnapshot);
    await runTimers(0);
    await document.querySelector(".memory-record-card").fire("dblclick");
    const input = document.querySelector(".memory-editor-overlay textarea");
    input.value = "draft during restart";
    await input.fire("input");
    const saving = document.querySelector('[data-memory-action="save"]').fire("click");
    await settle();
    assert.equal(typeof completeMutation, "function");

    nextSnapshot = snapshot("generation-b");
    await feature.refreshCurrent();
    completeMutation();
    await saving;

    assert.equal(document.querySelector(".memory-editor-overlay textarea")?.value, "draft during restart");
    assert.equal(document.querySelector(".memory-dialog-error"), null);
    assert.equal(feature.isDirty(), true);
    feature.dispose();
  });
}

test("a detached collection save callback cannot submit into the next generation", async () => {
  const writes = [];
  const fixture = featureFixture(async (command, args) => {
    if (command === "settings_plugins_get") return snapshot("generation-b");
    if (args.operation === "query") return queryResult("note");
    writes.push(args);
    return { itemId: "note", values: args.payload.values };
  });
  const { feature, document, runTimers } = fixture;
  feature.initialize(snapshot());
  await runTimers(0);
  await document.querySelector(".memory-record-card").fire("dblclick");
  const oldSave = document.querySelector('[data-memory-action="save"]');
  await feature.refreshCurrent();
  await oldSave.fire("click");
  assert.deepEqual(writes, []);
  assert.equal(feature.characterDraftCount(), 1);
  feature.dispose();
});

test("plugin dialog preserves drafts across refresh, restores cancel, and saves with rebound identity", async () => {
  let nextSnapshot = snapshot();
  const saves = [];
  const ui = featureFixture(async (command, args) => {
    if (command === "settings_plugins_get") return nextSnapshot;
    assert.equal(command, "settings_plugins_save");
    saves.push(args);
    nextSnapshot = snapshot("generation-b", args.values.label);
    return { saved: true, pluginId: "fixture_plugin", sectionId: "general", changePlan: "applied",
      applicationState: "applied", applicationReasonCode: "READY" };
  });
  const { feature, document } = ui;
  feature.initialize(nextSnapshot);
  await ui.openSettings();
  const input = document.querySelector(".plugin-settings-dialog .form-row input");
  input.value = "draft";
  await input.fire("input");
  await feature.refreshCurrent();
  assert.equal(document.querySelector(".plugin-settings-dialog .form-row input"), input);
  assert.equal(input.value, "draft");
  assert.equal(feature.isDirty(), true);
  nextSnapshot = snapshot("generation-b");
  await feature.refreshCurrent();
  await settle();
  assert.equal(document.querySelector(".plugin-settings-dialog"), null);
  await ui.openSettings();
  assert.equal(document.querySelector(".plugin-settings-dialog .form-row input").value, "draft");
  await document.querySelector(".plugin-settings-dialog form").fire("submit");
  assert.equal(saves.length, 0, "Done leaves a Settings draft until the global Apply");
  await feature.save();
  assert.deepEqual(saves, [{
    windowGeneration: 7, coreGenerationId: "generation-b", pluginId: "fixture_plugin",
    sectionId: "general", values: { label: "draft" },
  }]);
  assert.equal(feature.isDirty(), false);
  await ui.openSettings();
  const savedInput = document.querySelector(".plugin-settings-dialog .form-row input");
  savedInput.value = "cancel me";
  await savedInput.fire("input");
  await document.querySelector(".plugin-dialog-close").fire("click");
  assert.equal(feature.isDirty(), false);
  await ui.openSettings();
  assert.equal(document.querySelector(".plugin-settings-dialog .form-row input").value, "draft");
  feature.dispose();
  assert.equal(document.querySelector(".plugin-settings-dialog"), null);
});

test("plugin feature owns page polling and removes mounted listeners and pending work on disposal", async () => {
  const calls = [];
  const fixture = featureFixture(async (command) => { calls.push(command); return snapshot(); });
  const { feature, document, timers, runTimers } = fixture;
  const starting = snapshot();
  starting.state = "starting";
  document.getElementById("page-plugins").classList.add("is-active");
  feature.initialize(starting);
  assert.ok([...timers.values()].some((timer) => timer.milliseconds === 1200));
  document.getElementById("page-plugins").classList.remove("is-active");
  feature.onPageChanged("general");
  assert.equal([...timers.values()].some((timer) => timer.milliseconds === 1200), false);
  document.getElementById("page-about").classList.add("is-active");
  feature.onPageChanged("about");
  await runTimers(1200);
  assert.deepEqual(calls, ["settings_plugins_get"]);
  assert.equal([...timers.values()].some((timer) => timer.milliseconds === 1200), false);

  const menu = document.getElementById("pluginInstallMenu");
  const menuButton = document.getElementById("pluginInstallMenuButton");
  await menuButton.fire("click");
  assert.equal(menu.hidden, false);
  await document.fire("pointerdown", { target: document.body });
  assert.equal(menu.hidden, true);
  feature.dispose();
  await menuButton.fire("click");
  await runTimers(0);
  assert.equal(menu.hidden, true, "disposed mount listeners no longer handle input");
  assert.equal(timers.size, 0);
  assert.equal([...document.listeners.values()].flatMap((listeners) => [...listeners]).length, 0);
  assert.deepEqual(calls, ["settings_plugins_get"], "disposal cancels queued collection queries");
});

test("character transitions clear the Memory editor and invalidate outstanding collection reads", async () => {
  let transitioning = false;
  let pendingSelection = true;
  let resolveOldQuery;
  const queries = [];
  const fixture = featureFixture(async (command, args) => {
    if (command === "settings_plugins_get") return snapshot("generation-b");
    assert.equal(command, "settings_plugins_collection");
    queries.push(args);
    if (queries.length === 1) return new Promise((resolve) => { resolveOldQuery = resolve; });
    return queryResult("new-character");
  }, {
    isMemoryTransitioning: () => transitioning,
    hasPendingCharacterSelection: () => pendingSelection,
  });
  const { feature, document, runTimers } = fixture;
  feature.initialize(snapshot());
  await runTimers(0);
  assert.equal(queries.length, 0, "a pending character choice blocks reads for the old character");
  pendingSelection = false;
  feature.renderMemorySurface();
  await document.querySelector(".memory-add-button").fire("click");
  await runTimers(0);
  assert.equal(queries.length, 1);
  assert.equal(feature.characterDraftCount(), 1);
  transitioning = true;
  feature.clearCharacterState();
  assert.equal(feature.characterDraftCount(), 0);
  assert.equal(document.querySelector(".memory-editor-overlay"), null);
  assert.equal(document.querySelector(".settings-shell").hasAttribute("inert"), false);
  resolveOldQuery(queryResult("old-character"));
  await settle();
  await runTimers(0);
  assert.equal(queries.length, 1);
  assert.equal(document.querySelector(".memory-record-card"), null);
  transitioning = false;
  await feature.refreshCurrent();
  await runTimers(0);
  assert.equal(queries.at(-1).coreGenerationId, "generation-b");
  assert.equal(document.querySelector(".memory-record-card").dataset.itemId, "new-character");
  assert.equal(feature.characterDraftCount(), 0);
  feature.dispose();
});

test("About component actions use the owning plugin section and refresh its rendered resource", async () => {
  const resourceValue = (ready) => ({
    applicability: "required", subtitle: "Local model", ready, taskState: ready ? "succeeded" : "idle",
    message: "", detail: "", progress: null, availableActionIds: ready ? [] : ["download"],
  });
  const resourceSnapshot = (ready) => {
    const next = snapshot();
    const value = resourceValue(ready);
    next.plugins[0].sections.push({
      sectionId: "components", title: "Components", surface: "about", reasonCode: "READY",
      fields: [field("model", { type: "resource", readonly: true, maxLength: null,
        default: value, value, actionIds: ["download"] })],
      values: { model: value }, collections: [],
      actions: [{ actionId: "download", label: "下载", description: "", danger: false }],
    });
    return next;
  };
  const actions = [];
  const fixture = featureFixture(async (command, args) => {
    if (command === "settings_plugins_get") return resourceSnapshot(true);
    assert.equal(command, "settings_plugins_action");
    actions.push(args);
    return { message: "已完成" };
  });
  const { feature, document } = fixture;
  feature.initialize(resourceSnapshot(false));
  assert.equal(document.querySelectorAll("#aboutComponentsList .resource-card").length, 1);
  await document.querySelector("#aboutComponentsList button").fire("click");
  assert.equal(actions.length, 0, "the overview navigates to its owning settings dialog");
  assert.ok(document.querySelector(".plugin-settings-dialog"));
  await document.querySelector(".plugin-settings-dialog .resource-card button").fire("click");
  assert.deepEqual(actions, [{
    windowGeneration: 7, coreGenerationId: "generation-a", pluginId: "fixture_plugin",
    sectionId: "components", actionId: "download", values: {},
  }]);
  assert.equal(document.querySelector(".plugin-settings-dialog .resource-card button"), null);
  assert.match(document.getElementById("aboutComponentsSummary").textContent, /^1\/1/);
  assert.deepEqual(fixture.errors, []);
  feature.dispose();
});

test("voice sections initialized after opening the dialog mount once and cancel restores their draft", async () => {
  let voice = null;
  const calls = [];
  const ui = featureFixture(async () => snapshot(), { getVoiceController: () => voice });
  ui.feature.initialize(snapshot());
  await ui.openSettings();
  voice = {
    hasPluginSections: () => true,
    pluginDraft: () => ({ pluginId: "fixture_plugin", values: { timeout: 60 } }),
    restorePluginDraft: (draft) => calls.push(["restore", draft]),
    mountPluginSections(id, container) {
      calls.push(["mount", id]);
      container.append(ui.document.createElement("input"));
    },
    unmountPluginSections: () => calls.push(["unmount"]),
  };
  ui.feature.onVoiceSectionsRendered();
  ui.feature.onVoiceSectionsRendered();
  assert.deepEqual(calls, [["mount", "fixture_plugin"]]);
  await ui.document.querySelector(".plugin-dialog-close").fire("click");
  await ui.openSettings();
  await ui.document.querySelector(".plugin-dialog-close").fire("click");
  assert.deepEqual(calls, [
    ["mount", "fixture_plugin"], ["unmount"], ["mount", "fixture_plugin"],
    ["restore", { pluginId: "fixture_plugin", values: { timeout: 60 } }], ["unmount"],
  ]);
  ui.feature.dispose();
});

test("disposing during dialog exit releases it once and ignores the late animation callback", async () => {
  const ui = featureFixture(async () => snapshot());
  ui.feature.initialize(snapshot());
  await ui.openSettings();
  const dialog = ui.document.querySelector(".plugin-settings-dialog");
  let finish;
  dialog.getAnimations = () => [{ finished: new Promise((resolve) => { finish = resolve; }) }];
  await ui.document.querySelector(".plugin-dialog-close").fire("click");
  ui.feature.dispose();
  assert.equal(ui.document.querySelector(".plugin-settings-dialog"), null);
  const notifications = ui.dirtyNotifications();
  finish();
  await settle();
  assert.equal(ui.dirtyNotifications(), notifications);
  assert.equal(ui.feature.dialogElement(), undefined);
  assert.equal(ui.timers.size, 0);
});

test("ASR provider dialog mounts shared input controls, cancels capture on close and restores microphone draft", async () => {
  const calls = [];
  const asr = {
    refresh: async () => {}, hasPluginControls: (id) => id === "fixture_plugin",
    pluginDraft: () => ({ inputDeviceId: "saved-mic" }),
    restorePluginDraft: (draft) => calls.push(["restore", draft]),
    mountPluginControls: (id, container) => {
      calls.push(["mount", id]); container.append(ui.document.createElement("select"));
    },
    cancelTest: () => calls.push(["cancel"]),
    unmountPluginControls: () => calls.push(["unmount"]),
  };
  const ui = featureFixture(async () => snapshot(), { getAsrController: () => asr });
  ui.feature.initialize(snapshot());
  await ui.openSettings();
  assert.ok(ui.document.querySelector(".plugin-dialog-asr select"));
  await ui.document.querySelector(".plugin-dialog-close").fire("click");
  assert.deepEqual(calls, [
    ["mount", "fixture_plugin"], ["cancel"], ["restore", { inputDeviceId: "saved-mic" }], ["unmount"],
  ]);
  ui.feature.dispose();
});
