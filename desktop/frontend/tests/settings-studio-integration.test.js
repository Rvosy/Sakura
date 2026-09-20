import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import vm from "node:vm";

import { createCharacterSettingsFeature } from "../settings/character-settings.js";
import { RUNTIME_THEME_FIELDS } from "../core/theme-runtime.js";
import { featureFixture, snapshot as pluginSnapshot, queryResult } from "./fixtures/plugin-settings-fixture.js";

const settle = () => new Promise((resolve) => setImmediate(resolve));
const settingsSource = await readFile(new URL("../settings/settings.js", import.meta.url), "utf8");
const saveSettingsSource = settingsSource.slice(settingsSource.indexOf("async function saveRuntimeSettings("),
  settingsSource.indexOf("\nfunction collectThemeSettings("));
const submitButtonsSource = settingsSource.slice(settingsSource.indexOf('fields.saveButton.addEventListener("click"'),
  settingsSource.indexOf('fields.cancelButton.addEventListener("click"'));

// Only the DOM boundary is replaced. Catalog, archive, preview, and generation
// handling run through the imported production feature and typed clients.
function characterDocument() {
  class Element {
    constructor(tagName = "div") {
      this.tagName = tagName;
      this.children = [];
      this.parentElement = null;
      this.attributes = new Map();
      this.listeners = new Map();
      this.disabled = false;
      this.inert = false;
      this.value = "";
      this.className = "";
      this.classList = {
        contains: (name) => this.className.split(/\s+/).includes(name),
        toggle: (name, enabled) => {
          const classes = new Set(this.className.split(/\s+/).filter(Boolean));
          if (enabled) classes.add(name); else classes.delete(name);
          this.className = [...classes].join(" ");
        },
      };
    }
    get textContent() { return (this.text || "") + this.children.map((child) => child.textContent).join(""); }
    set textContent(value) { this.children.slice().forEach((child) => child.remove()); this.text = String(value); }
    append(...children) {
      for (const child of children) { child.remove(); child.parentElement = this; this.children.push(child); }
    }
    remove() {
      if (this.parentElement) this.parentElement.children.splice(this.parentElement.children.indexOf(this), 1);
      this.parentElement = null;
    }
    setAttribute(name, value) { this.attributes.set(name, String(value)); }
    getAttribute(name) { return this.attributes.get(name) ?? null; }
    removeAttribute(name) { this.attributes.delete(name); }
    addEventListener(type, listener) {
      if (!this.listeners.has(type)) this.listeners.set(type, new Set());
      this.listeners.get(type).add(listener);
    }
    removeEventListener(type, listener) { this.listeners.get(type)?.delete(listener); }
    async fire(type, detail = {}) {
      const event = { target: this, ...detail };
      await Promise.all([...this.listeners.get(type) || []].map((listener) => listener(event)));
      await settle();
    }
    async click() { if (!this.disabled) await this.fire("click"); }
    focus() { document.activeElement = this; }
    querySelectorAll(selector) {
      const descendants = this.children.flatMap((child) => [child, ...child.querySelectorAll("*")]);
      if (selector === "*") return descendants;
      return descendants.filter((child) => {
        if (selector.startsWith(".")) return child.classList.contains(selector.slice(1));
        if (selector === "button:not(:disabled)") return child.tagName === "button" && !child.disabled;
        return child.tagName === selector;
      });
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  }
  const document = new Element("document");
  document.createElement = (tagName) => new Element(tagName);
  document.body = new Element("body");
  document.append(document.body);
  const fields = Object.fromEntries([
    "saveButton", "applyButton", "characterSelect", "characterImportButton", "ttsVoiceImportButton",
    "characterExportButton", "characterEditorButton", "characterArchiveHint",
    "page-character", "page-appearance", "page-voice", "page-memory", "page-model",
  ].map((id) => [id, new Element()]));
  document.body.append(...Object.values(fields));
  document.getElementById = (id) => fields[id] || null;
  return { document, fields };
}

function catalog(ids = ["alpha", "beta", "gamma"], currentCharacterId = "alpha") {
  return {
    schemaVersion: 1, revision: 1, currentCharacterId,
    characters: ids.map((id) => ({ id, displayName: id, hasVoice: true, hasExportableVoice: true })),
  };
}

function lifecycle(generationId = "generation-a", generationNumber = 1, characterId = "alpha") {
  return {
    supervisor: { generationId, generationNumber },
    snapshot: { generationId, readiness: "ready" },
    characterPresentation: { generationId, characterId },
  };
}

function visualPreview({ characterId, revision }, color = "#123456") {
  return {
    schemaVersion: 1, windowGeneration: 7, revision,
    presentation: { generationId: "generation-a", characterId },
    appearance: { coreGenerationId: "generation-a", characterId,
      values: { themeTokens: Object.fromEntries(Object.keys(RUNTIME_THEME_FIELDS).map((key) => [key, color])) } },
  };
}

async function characterSettings(options = {}) {
  const { document, fields } = characterDocument();
  const calls = [], errors = [], previews = [], transitions = [];
  const state = { catalog: catalog(), lifecycle: lifecycle(), dirty: false, submitting: false };
  const handlers = {
    settings_characters_get: () => state.catalog,
    runtime_lifecycle_snapshot: () => state.lifecycle,
    settings_character_visual_preview: (args) => visualPreview(args),
    open_character_studio: () => {},
    ...options.handlers,
  };
  const feature = createCharacterSettingsFeature({
    document,
    window: Object.assign(new EventTarget(), { setTimeout: (callback) => setTimeout(callback, 0) }),
    invoke: async (command, args) => {
      calls.push([command, args]);
      assert.ok(handlers[command], `unexpected command ${command}`);
      return handlers[command](args);
    },
    onDirty: () => feature.syncControls(),
    onError: (message) => { if (message) errors.push(message); },
    notify() {}, enhanceSelect() {}, refreshSelect() {},
    disableRuntimeControl: (control) => {
      control.disabled = true;
      control.setAttribute("aria-disabled", "true");
    },
    hasCharacterDrafts: () => state.dirty,
    isSubmitting: () => state.submitting,
    applyPreviewTheme: (theme) => previews.push(theme),
    rebindSettings: async (generationId) => { transitions.push(["rebind", generationId]); },
    clearCharacterState: () => transitions.push(["clear"]),
    renderPluginCollections() {},
    ...options.feature,
  });
  if (options.initialize !== false) await feature.initialize();
  calls.length = 0;
  return { feature, fields, document, calls, errors, previews, transitions, state, handlers };
}

async function select(fixture, id) {
  fixture.fields.characterSelect.value = id;
  await fixture.fields.characterSelect.fire("change");
}

test("unsaved layout and pending character selection both allow opening Studio without saving", async () => {
  const fixture = await characterSettings();
  const { feature, fields, calls, state } = fixture;
  state.dirty = true;
  feature.syncControls();
  assert.equal(fields.characterEditorButton.disabled, false);
  assert.equal(fields.ttsVoiceImportButton.disabled, true);
  await fields.characterEditorButton.click();
  assert.deepEqual(calls.at(-1), ["open_character_studio", { characterId: "alpha" }]);
  assert.equal(state.dirty, true);
  state.dirty = false;
  feature.syncControls();
  assert.equal(fields.ttsVoiceImportButton.disabled, false, "saving updates the existing controls");
  await select(fixture, "beta");
  assert.equal(feature.pendingCharacterId(), "beta");
  assert.equal(fields.characterEditorButton.disabled, false);
  assert.equal(fields.ttsVoiceImportButton.disabled, true);
  assert.equal(fields["page-appearance"].inert, true);
  assert.equal(fields["page-model"].inert, false);
  await fields.characterEditorButton.click();
  assert.deepEqual(calls.at(-1), ["open_character_studio", { characterId: "beta" }]);
  assert.equal(feature.currentCharacterId(), "alpha");
  assert.equal(feature.isDirty(), true);
  assert.equal(calls.some(([command]) => /_select$|_save$/.test(command)), false);
  feature.dispose();
});

test("Studio catalog publication retains a valid pending selection but drops a removed target", async () => {
  const fixture = await characterSettings();
  const { feature, fields, state, calls } = fixture;
  await select(fixture, "beta");
  feature.applyAppearancePresentation({ characterId: "alpha", displayName: "Updated alpha" }, {}, { text_color: "#112233" });
  assert.equal(fields.characterSelect.children[0].textContent, "Updated alpha");
  assert.equal(feature.pendingCharacterId(), "beta");
  await feature.refreshCatalog({});
  assert.equal(fields.characterSelect.value, "beta");
  assert.equal(feature.pendingCharacterId(), "beta");
  state.catalog = catalog(["alpha"]);
  await feature.refreshCatalog({});
  assert.equal(fields.characterSelect.value, "alpha");
  assert.equal(feature.pendingCharacterId(), null);
  assert.deepEqual(fields.characterSelect.children.map((option) => option.value), ["alpha"]);
  assert.equal(calls.some(([command]) => command === "runtime_lifecycle_snapshot"), false);
  feature.dispose();
});

test("current-character publication rebinds only the announced ready generation and retains drafts", async () => {
  let releaseRebind;
  const rebinds = [];
  const fixture = await characterSettings({ feature: {
    rebindSettings: (generationId) => {
      rebinds.push(generationId);
      return new Promise((resolve) => { releaseRebind = resolve; });
    },
  } });
  const { feature, fields, state, calls, transitions } = fixture;
  await select(fixture, "beta");
  state.dirty = true;
  state.lifecycle = lifecycle("generation-b", 2);
  calls.length = 0;
  const publication = feature.refreshCatalog({ generationId: "generation-b" });
  await settle();
  assert.deepEqual(rebinds, ["generation-b"]);
  assert.equal(feature.isSwitching(), true);
  assert.equal(feature.isTransitioning(), true);
  assert.equal(fields.characterEditorButton.disabled, true);
  assert.equal(fields.saveButton.disabled, true);
  assert.equal(fields["page-model"].inert, false);
  releaseRebind();
  await publication;
  assert.deepEqual(calls.map(([command]) => command), ["runtime_lifecycle_snapshot", "settings_characters_get"]);
  assert.equal(fields.characterSelect.value, "beta");
  assert.equal(state.dirty, true);
  assert.equal(feature.isSwitching(), false);
  assert.equal(feature.isTransitioning(), false);
  assert.equal(fields.characterEditorButton.disabled, false);
  assert.deepEqual(transitions, []);
  calls.length = 0;
  await feature.refreshCatalog({ generationId: "generation-a" });
  assert.deepEqual(rebinds, ["generation-b"], "an obsolete publication cannot rebind the current generation");
  assert.deepEqual(calls.map(([command]) => command), ["runtime_lifecycle_snapshot"]);
  assert.equal(feature.pendingCharacterId(), "beta");
  feature.dispose();
});

for (const [surface, scope] of [[null, "character"], ["memory", "character"], [null, "global"], ["memory", "global"]]) {
  test(`character changes respect ${scope} data ownership on the ${surface || "plugin"} surface`, async () => {
    function snapshotFor(generation) {
      const value = pluginSnapshot(generation);
      value.plugins[0].sections[1].surface = surface;
      value.plugins[0].sections[1].collections[0].scope = scope;
      return value;
    }
    let nextPlugins = snapshotFor("generation-a");
    let characters;
    const plugins = featureFixture(async (command, args) => {
      if (command === "settings_plugins_get") return nextPlugins;
      assert.equal(args.operation, "query", "a role change must never submit a collection draft");
      return queryResult("saved-note");
    }, {
      isCharacterTransitioning: () => characters?.feature.isTransitioning() || false,
      hasPendingCharacterSelection: () => Boolean(characters?.feature.pendingCharacterId()),
    });
    plugins.feature.initialize(nextPlugins);
    if (surface === null) await plugins.openSettings();
    await plugins.runTimers(0);
    await plugins.document.querySelector(surface === "memory" ? ".memory-add-button" : ".plugin-collection-head button").fire("click");
    const editor = plugins.document.querySelector(surface === "memory" ? ".memory-editor-overlay textarea" : ".plugin-collection-editor textarea");
    editor.value = "draft belonging to alpha";
    await editor.fire("input");
    characters = await characterSettings({ feature: {
      hasCharacterDrafts: () => plugins.feature.characterCollectionDraftCount() > 0,
      clearCharacterState: () => plugins.feature.clearCharacterState(),
      invalidateCollectionRequests: () => plugins.feature.invalidateCollectionRequests(),
      renderPluginCollections: () => plugins.feature.renderCollections(),
      rebindSettings: async (generation) => {
        nextPlugins = snapshotFor(generation);
        await plugins.feature.refreshCurrent();
      },
    } });
    try {
      await select(characters, "beta");
      assert.equal(characters.feature.pendingCharacterId(), scope === "character" ? null : "beta",
        "only edits owned by the current character block choosing another character");
      if (scope === "global") {
        assert.equal(characters.fields["page-memory"].inert, false,
          "the Memory page must not disable global collections during a pending choice");
        assert.equal(plugins.document.querySelector(surface === "memory" ? ".memory-archive" : ".plugin-collection").inert, false);
      }
      await select(characters, "alpha");
      await characters.feature.refreshCatalog({});
      assert.equal(plugins.feature.hasCollectionDrafts(), true, "same-character catalog changes keep unfinished editing");
      characters.state.lifecycle = lifecycle("generation-b", 2, "alpha");
      await characters.feature.refreshCatalog({ generationId: "generation-b" });
      assert.equal(plugins.feature.hasCollectionDrafts(), true, "same-character restarts keep unfinished editing");

      // A catalog-only notification may learn the new role before its ready
      // generation arrives. It must not hide the old editor's ownership.
      characters.state.catalog = catalog(["alpha", "beta"], "beta");
      await characters.feature.refreshCatalog({});
      characters.state.lifecycle = lifecycle("generation-c", 3, "beta");
      characters.state.catalog = catalog(["alpha", "beta"], "beta");
      await characters.feature.refreshCatalog({ generationId: "generation-c" });
      assert.equal(characters.feature.currentCharacterId(), "beta");
      assert.equal(plugins.feature.hasCollectionDrafts(), scope === "global",
        "character drafts are discarded while global drafts survive a character change");
      await plugins.runTimers(0);
      if (scope === "global") {
        if (surface === null) await plugins.openSettings();
        assert.equal(plugins.document.querySelector(surface === "memory" ? ".memory-editor-overlay textarea" : ".plugin-collection-editor textarea").value,
          "draft belonging to alpha");
      } else {
        assert.equal(plugins.document.querySelector(".memory-editor-overlay"), null);
        assert.equal(plugins.document.querySelector(".plugin-collection-editor"), null);
      }
    } finally {
      characters.feature.dispose();
      plugins.feature.dispose();
    }
  });
}

for (const transition of ["same-core-roundtrip", "same-character-refresh", "core-restart"]) {
  test(`${transition} retains Collection drafts but detaches in-flight writes and old save callbacks`, async () => {
    let generation = "generation-a", characters, completeWrite;
    const writes = [];
    const snapshotFor = () => {
      const value = pluginSnapshot(generation);
      value.plugins[0].sections[1].collections[0].scope = transition === "same-core-roundtrip" ? "global" : "character";
      return value;
    };
    const plugins = featureFixture(async (command, args) => {
      if (command === "settings_plugins_get") return snapshotFor();
      if (args.operation === "query") return queryResult("note", "stored note");
      writes.push(args);
      return new Promise((resolve) => { completeWrite = () => resolve({ itemId: "note", values: args.payload.values }); });
    }, {
      isCharacterTransitioning: () => characters?.feature.isTransitioning() || false,
      hasPendingCharacterSelection: () => Boolean(characters?.feature.pendingCharacterId()),
    });
    plugins.feature.initialize(snapshotFor());
    characters = await characterSettings({ feature: {
      hasCharacterDrafts: () => plugins.feature.characterCollectionDraftCount() > 0,
      clearCharacterState: () => plugins.feature.clearCharacterState(),
      invalidateCollectionRequests: () => plugins.feature.invalidateCollectionRequests(),
      renderPluginCollections: () => plugins.feature.renderCollections(),
      rebindSettings: async () => plugins.feature.refreshCurrent(),
    } });
    try {
      await plugins.runTimers(0);
      await plugins.document.querySelector(".memory-record-card").fire("dblclick");
      const input = plugins.document.querySelector(".memory-editor-overlay textarea");
      input.value = "unfinished note";
      await input.fire("input");
      const oldSave = plugins.document.querySelector('[data-memory-action="save"]');
      const saving = oldSave.fire("click");
      await settle();
      assert.equal(writes.length, 1);
      if (transition === "same-core-roundtrip") {
        for (const characterId of ["beta", "alpha"]) {
          await select(characters, characterId);
          characters.handlers.settings_character_select = () => {
            characters.state.catalog = catalog(["alpha", "beta"], characterId);
            characters.state.lifecycle = lifecycle(generation, 1, characterId);
            return { schemaVersion: 1, previousCoreGenerationId: generation, restartState: "not_required",
              characterChanged: true, targetCharacterId: characterId, snapshot: characters.state.catalog };
          };
          await characters.feature.commit();
          assert.equal(characters.feature.currentCharacterId(), characterId);
        }
      } else {
        if (transition === "core-restart") generation = "generation-b";
        characters.state.lifecycle = lifecycle(generation, transition === "core-restart" ? 2 : 1, "alpha");
        await characters.feature.refreshCatalog({ generationId: generation });
      }
      completeWrite();
      await saving;
      await oldSave.fire("click");
      assert.equal(writes.length, 1, "neither a rebind nor a detached button may replay the write");
      assert.equal(plugins.feature.hasCollectionDrafts(), true);
      assert.equal(plugins.document.querySelector(".memory-editor-overlay textarea")?.value, "unfinished note");
      assert.deepEqual(characters.errors, []);
    } finally {
      completeWrite?.();
      characters.feature.dispose();
      plugins.feature.dispose();
    }
  });
}

test("Apply commits a character and ordinary settings while retaining global records; Save and close still protects them", async () => {
  let generation = "generation-a", label = "fixture", characters;
  const writes = [];
  const snapshotFor = () => {
    const value = pluginSnapshot(generation, label);
    value.plugins[0].sections[1].surface = null;
    value.plugins[0].sections[1].collections[0].scope = "global";
    return value;
  };
  const plugins = featureFixture(async (command, args) => {
    if (command === "settings_plugins_get") return snapshotFor();
    if (command === "settings_plugins_save") {
      writes.push(args);
      label = args.values.label;
      return { saved: true, pluginId: "fixture_plugin", sectionId: "general", changePlan: "applied",
        applicationState: "applied", applicationReasonCode: "READY" };
    }
    assert.equal(args.operation, "query", "Apply cannot silently save Collection records");
    return queryResult("global-note");
  }, {
    isCharacterTransitioning: () => characters?.feature.isTransitioning() || false,
    hasPendingCharacterSelection: () => Boolean(characters?.feature.pendingCharacterId()),
  });
  plugins.feature.initialize(snapshotFor());
  characters = await characterSettings({ feature: {
    hasCharacterDrafts: () => plugins.feature.characterCollectionDraftCount() > 0,
    clearCharacterState: () => plugins.feature.clearCharacterState(),
      invalidateCollectionRequests: () => plugins.feature.invalidateCollectionRequests(),
    renderPluginCollections: () => plugins.feature.renderCollections(),
    rebindSettings: async (nextGeneration) => {
      generation = nextGeneration;
      await plugins.feature.refreshCurrent();
    },
  } });
  const { fields, state, handlers } = characters;
  const errors = [], notices = [];
  let closed = false;
  // Run the shipping aggregate-save and button callbacks with actual character
  // and plugin features. Only unrelated settings controllers and native close
  // are replaced here.
  vm.runInNewContext(`${saveSettingsSource}\n${submitButtonsSource}`, {
    fields, document: plugins.document, runtimePluginController: plugins.feature, runtimeCharacterFeature: characters.feature,
    runtimeAsrController: null, runtimeAppearanceController: null, runtimeScreenAwarenessController: null,
    runtimeProviderFeature: null, runtimeChatTimingController: null, runtimeBubbleAutoHideController: null,
    runtimeAutostartController: null, runtimeToolsController: null, runtimeVoiceController: null,
    refreshRuntimeVoiceCurrent: async () => {}, setError: (error) => { if (error) errors.push(error); },
    setSubmissionBusy: (value) => { state.submitting = value; characters.feature.syncControls(); },
    notify: (message) => notices.push(message), closeSettingsWindow: async () => { closed = true; },
    bypassCloseGuard: false,
  });
  try {
    await plugins.openSettings();
    const setting = plugins.document.querySelector(".plugin-settings-dialog .form-row input");
    setting.value = "updated setting";
    await setting.fire("input");
    await plugins.runTimers(0);
    await plugins.document.querySelector(".plugin-collection-table tbody tr").fire("click");
    const input = plugins.document.querySelector(".plugin-collection-editor textarea");
    input.value = "unsaved global record";
    await input.fire("input");
    await plugins.document.querySelector(".plugin-settings-dialog form").fire("submit");
    await select(characters, "beta");
    handlers.settings_character_select = () => {
      state.catalog = catalog(["alpha", "beta"], "beta");
      state.lifecycle = lifecycle("generation-b", 2, "beta");
      return { schemaVersion: 1, previousCoreGenerationId: "generation-a", restartState: "requested",
        targetCharacterId: "beta", snapshot: state.catalog };
    };
    await fields.applyButton.click();
    assert.deepEqual(errors, []);
    assert.equal(characters.feature.currentCharacterId(), "beta");
    assert.equal(writes.length, 1);
    assert.equal(writes[0].values.label, "updated setting");
    assert.equal(plugins.feature.hasCollectionDrafts(), true);
    assert.equal(closed, false);
    await plugins.openSettings();
    assert.equal(plugins.document.querySelector(".plugin-collection-editor textarea").value, "unsaved global record");
    assert.equal(plugins.document.querySelector(".plugin-settings-dialog .form-row input").value, "updated setting");

    const noticeCount = notices.length;
    await fields.saveButton.click();
    assert.equal(closed, false, "Save and close cannot silently discard the retained record");
    assert.match(errors.at(-1), /集合记录/);
    assert.equal(notices.length, noticeCount, "a rejected save does not report success");
    assert.equal(writes.length, 1, "the ordinary field was already saved by Apply");
  } finally {
    characters.feature.dispose();
    plugins.feature.dispose();
  }
});

test("Apply rejects disabling the owner of an edited global record before the snapshot removes its sections", async () => {
  const data = pluginSnapshot();
  data.plugins[0].sections[1].surface = null;
  data.plugins[0].sections[1].collections[0].scope = "global";
  const writes = [];
  const plugins = featureFixture(async (command, args) => {
    if (command === "settings_plugins_enabled_set") {
      writes.push(args);
      const disabled = { ...data.plugins[0], enabled: false, state: "disabled", reasonCode: "PLUGIN_DISABLED", sections: [] };
      return { ...data, plugins: [disabled], managementAction: "enabled_changed", installId: disabled.installId,
        pluginId: disabled.pluginId, desiredSaved: true, applicationState: "applied", applicationReasonCode: "READY" };
    }
    assert.equal(args.operation, "query");
    return queryResult("global-note");
  });
  plugins.feature.initialize(data);
  const fields = characterDocument().fields;
  const errors = [];
  vm.runInNewContext(`${saveSettingsSource}\n${submitButtonsSource}`, {
    fields, document: plugins.document, runtimePluginController: plugins.feature, runtimeCharacterFeature: null,
    runtimeAsrController: null, runtimeAppearanceController: null, runtimeScreenAwarenessController: null,
    runtimeProviderFeature: null, runtimeChatTimingController: null, runtimeBubbleAutoHideController: null,
    runtimeAutostartController: null, runtimeToolsController: null, runtimeVoiceController: null,
    refreshRuntimeVoiceCurrent: async () => {}, setError: (error) => { if (error) errors.push(error); },
    setSubmissionBusy() {}, notify() {}, closeSettingsWindow: async () => {}, bypassCloseGuard: false,
  });
  try {
    await plugins.openSettings();
    await plugins.runTimers(0);
    await plugins.document.querySelector(".plugin-collection-table tbody tr").fire("click");
    const input = plugins.document.querySelector(".plugin-collection-editor textarea");
    input.value = "unsaved global record";
    await input.fire("input");
    await plugins.document.querySelector(".plugin-settings-dialog form").fire("submit");
    const toggle = plugins.document.querySelector(".plugin-enable-switch input");
    toggle.checked = false;
    await toggle.fire("change");
    await fields.applyButton.click();
    assert.deepEqual(writes, [], "the plugin must remain active until its record is saved or reverted");
    assert.match(errors.at(-1), /集合记录/);
    assert.equal(plugins.feature.hasCollectionDrafts(), true);
    await plugins.openSettings();
    assert.equal(plugins.document.querySelector(".plugin-collection-editor textarea").value, "unsaved global record");
  } finally {
    plugins.feature.dispose();
  }
});

test("an enabled incompatible plugin can be disabled but cannot be enabled again", async () => {
  const data = pluginSnapshot();
  const plugin = data.plugins[0];
  Object.assign(plugin, { supported: false, state: "failed", reasonCode: "MODEL_API_UPDATE_REQUIRED", sections: [] });
  const writes = [];
  const fixture = featureFixture(async (command, args) => {
    assert.equal(command, "settings_plugins_enabled_set");
    assert.equal(args.enabled, false);
    writes.push(args);
    const disabled = { ...plugin, enabled: false, state: "disabled" };
    return { ...data, plugins: [disabled], managementAction: "enabled_changed", installId: plugin.installId,
      pluginId: plugin.pluginId, desiredSaved: true, applicationState: "applied", applicationReasonCode: "READY" };
  });
  try {
    fixture.feature.initialize(data);
    const toggle = fixture.document.querySelector(".plugin-enable-switch input");
    assert.equal(toggle.checked, true);
    assert.equal(toggle.disabled, false);
    toggle.checked = false;
    await toggle.fire("change");
    await fixture.feature.save();
    assert.equal(writes.length, 1);
    const stopped = fixture.document.querySelector(".plugin-enable-switch input");
    assert.equal(stopped.checked, false);
    assert.equal(stopped.disabled, true);
    assert.deepEqual(fixture.errors, []);
  } finally {
    fixture.feature.dispose();
  }
});

test("same-character voice import retains a collection draft opened while importing", async () => {
  const snapshotFor = (generation) => {
    const value = pluginSnapshot(generation);
    value.plugins[0].sections[1].surface = null;
    return value;
  };
  let nextPlugins = snapshotFor("generation-a"), releaseImport;
  const plugins = featureFixture(async (command, args) => {
    if (command === "settings_plugins_get") return nextPlugins;
    assert.equal(args.operation, "query", "rebinding cannot submit the draft");
    return queryResult("saved-note");
  });
  plugins.feature.initialize(nextPlugins);
  const characters = await characterSettings({
    handlers: {
      settings_character_choose_import: () => "C:\\imports\\alpha.voice",
      settings_character_import_voice: () => new Promise((resolve) => { releaseImport = resolve; }),
    },
    feature: {
      hasCharacterDrafts: () => plugins.feature.characterCollectionDraftCount() > 0,
      clearCharacterState: () => plugins.feature.clearCharacterState(),
      invalidateCollectionRequests: () => plugins.feature.invalidateCollectionRequests(),
      rebindSettings: async (generation) => {
        nextPlugins = snapshotFor(generation);
        await plugins.feature.refreshCurrent();
      },
    },
  });
  let importing;
  try {
    importing = characters.fields.ttsVoiceImportButton.click();
    await settle();
    assert.equal(typeof releaseImport, "function", "import started before any draft existed");
    await plugins.openSettings();
    await plugins.runTimers(0);
    await plugins.document.querySelector(".plugin-collection-head button").fire("click");
    const editor = plugins.document.querySelector(".plugin-collection-editor textarea");
    editor.value = "unfinished alpha note";
    await editor.fire("input");
    characters.state.lifecycle = lifecycle("generation-b", 2, "alpha");
    releaseImport({
      schemaVersion: 1, previousCoreGenerationId: "generation-a", restartState: "requested",
      targetCharacterId: "alpha", snapshot: characters.state.catalog,
    });
    await importing;
    assert.deepEqual(characters.errors, []);
    assert.equal(plugins.feature.hasCollectionDrafts(), true);
    await plugins.openSettings();
    assert.equal(plugins.document.querySelector(".plugin-collection-editor textarea").value, "unfinished alpha note");
  } finally {
    characters.feature.dispose();
    plugins.feature.dispose();
  }
});

test("overlapping catalog-only publications cannot leave a completed rebind locked", async () => {
  let releaseRebind;
  const fixture = await characterSettings({ feature: {
    rebindSettings: () => new Promise((resolve) => { releaseRebind = resolve; }),
  } });
  try {
    fixture.state.lifecycle = lifecycle("generation-b", 2);
    const rebinding = fixture.feature.refreshCatalog({ generationId: "generation-b" });
    await settle();
    assert.equal(fixture.feature.isTransitioning(), true);
    const catalogOnly = fixture.feature.refreshCatalog({});
    await settle();
    assert.equal(fixture.feature.isTransitioning(), true, "the active rebind retains its lock");
    releaseRebind();
    await Promise.all([rebinding, catalogOnly]);
    assert.equal(fixture.feature.isTransitioning(), false);
    assert.equal(fixture.fields.saveButton.disabled, false);
    assert.equal(fixture.fields.characterSelect.disabled, false);
  } finally {
    releaseRebind?.();
    fixture.feature.dispose();
  }
});

for (const firstFinished of ["catalog", "local"]) {
  test(`${firstFinished} completion cannot release the other character transition`, async () => {
    let releaseLocal, releaseCatalog;
    const fixture = await characterSettings({ feature: {
      rebindSettings: () => new Promise((resolve) => { releaseLocal = resolve; }),
    } });
    const { feature, fields, state, handlers } = fixture;
    let local, catalogRefresh;
    try {
      await select(fixture, "beta");
      handlers.settings_character_select = () => {
        state.catalog = catalog(["alpha", "beta"], "beta");
        state.lifecycle = lifecycle("generation-b", 2, "beta");
        return {
          schemaVersion: 1, previousCoreGenerationId: "generation-a", restartState: "requested",
          targetCharacterId: "beta", snapshot: state.catalog,
        };
      };
      state.submitting = true;
      local = feature.commit();
      await settle();
      assert.equal(typeof releaseLocal, "function");
      handlers.runtime_lifecycle_snapshot = () => new Promise((resolve) => { releaseCatalog = resolve; });
      catalogRefresh = feature.refreshCatalog({ generationId: "generation-a" });
      await settle();
      assert.equal(typeof releaseCatalog, "function");

      if (firstFinished === "catalog") {
        releaseCatalog(state.lifecycle);
        await catalogRefresh;
      } else {
        releaseLocal();
        await local;
        state.submitting = false;
        feature.syncControls();
      }
      assert.equal(feature.isTransitioning(), true, "the other hand-off still blocks collection operations");
      assert.equal(fields.characterSelect.disabled, true);
      assert.equal(fields.saveButton.disabled, true);
      releaseCatalog(state.lifecycle);
      releaseLocal();
      await Promise.all([local, catalogRefresh]);
      state.submitting = false;
      feature.syncControls();
      assert.equal(feature.isTransitioning(), false);
      assert.equal(fields.characterSelect.disabled, false);
      assert.equal(fields.saveButton.disabled, false);
    } finally {
      releaseLocal?.();
      releaseCatalog?.(state.lifecycle);
      await Promise.all([local, catalogRefresh]);
      feature.dispose();
    }
  });
}

test("character drafts block changing roles and commit submits only the final unblocked selection", async () => {
  const fixture = await characterSettings();
  const { feature, fields, state, calls, handlers, transitions, errors } = fixture;
  state.dirty = true;
  await select(fixture, "beta");
  assert.equal(fields.characterSelect.value, "alpha");
  assert.equal(feature.pendingCharacterId(), null);
  assert.equal(calls.length, 0);
  assert.match(errors.at(-1), /未保存/);
  state.dirty = false;
  await select(fixture, "beta");
  await select(fixture, "gamma");
  handlers.settings_character_select = ({ characterId }) => {
    assert.equal(characterId, "gamma");
    state.catalog = catalog(["alpha", "beta", "gamma"], "gamma");
    state.lifecycle = lifecycle("generation-b", 2, "gamma");
    return {
      schemaVersion: 1, previousCoreGenerationId: "generation-a", restartState: "requested",
      targetCharacterId: "gamma", snapshot: state.catalog,
    };
  };
  state.submitting = true;
  calls.length = 0;
  await feature.commit();
  assert.deepEqual(calls.map(([command]) => command), [
    "runtime_lifecycle_snapshot", "settings_character_select", "runtime_lifecycle_snapshot", "settings_characters_get",
  ]);
  assert.deepEqual(transitions, [["clear"], ["rebind", "generation-b"]]);
  assert.equal(feature.currentCharacterId(), "gamma");
  assert.equal(feature.pendingCharacterId(), null);
  assert.equal(feature.isSwitching(), false);
  assert.equal(fields.saveButton.disabled, true, "role completion does not unlock an aggregate save still in progress");
  state.submitting = false;
  feature.syncControls();
  assert.equal(fields.saveButton.disabled, false);
  calls.length = 0;
  assert.equal(await feature.commit(), null);
  assert.deepEqual(calls, []);
  feature.dispose();
});

test("late visual previews cannot repaint a newer draft and discard exposes the restoration promise", async () => {
  const pending = new Map();
  const fixture = await characterSettings({ handlers: {
    settings_character_visual_preview: (args) => new Promise((resolve) => { pending.set(args.characterId, [args, resolve]); }),
  } });
  const { feature, fields, previews } = fixture;
  await select(fixture, "beta");
  await select(fixture, "gamma");
  const [gammaArgs, resolveGamma] = pending.get("gamma");
  resolveGamma(visualPreview(gammaArgs, "#445566"));
  await feature.waitForPreview();
  const [betaArgs, resolveBeta] = pending.get("beta");
  resolveBeta(visualPreview(betaArgs, "#aabbcc"));
  await settle();
  assert.deepEqual(previews.map((theme) => theme.primary_color), ["#445566"]);
  let restored = false;
  const discard = feature.discard().then(() => { restored = true; });
  const closeWait = feature.waitForPreview();
  await settle();
  assert.equal(restored, false);
  assert.equal(fields.characterSelect.value, "alpha");
  assert.equal(feature.isDirty(), false);
  const [alphaArgs, resolveAlpha] = pending.get("alpha");
  resolveAlpha(visualPreview(alphaArgs, "#778899"));
  await Promise.all([discard, closeWait]);
  assert.deepEqual(previews.map((theme) => theme.primary_color), ["#445566", "#778899"]);
  feature.dispose();
});

test("visual preview validation preserves the draft and reports an identity mismatch", async () => {
  const fixture = await characterSettings({ handlers: {
    settings_character_visual_preview: (args) => {
      const result = visualPreview(args);
      result.appearance.coreGenerationId = "wrong-generation";
      return result;
    },
  } });
  await select(fixture, "beta");
  assert.equal(fixture.feature.pendingCharacterId(), "beta");
  assert.equal(fixture.previews.length, 0);
  assert.match(fixture.errors.at(-1), /CHARACTER_VISUAL_PREVIEW_INVALID/);
  fixture.feature.dispose();
});

test("archive export keeps voice eligibility and the native path handoff while cancelled import does no work", async () => {
  const noVoice = catalog(["alpha"]);
  noVoice.characters[0].hasVoice = false;
  noVoice.characters[0].hasExportableVoice = false;
  const fixture = await characterSettings({ handlers: {
    settings_characters_get: () => noVoice,
    settings_character_choose_export: () => "C:\\exports\\alpha.card.char",
    settings_character_export: () => ({ schemaVersion: 1, outputPath: "C:\\exports\\alpha.card.char", message: "导出完成" }),
    settings_character_choose_import: () => null,
  } });
  const { feature, fields, document, calls } = fixture;
  const exporting = fields.characterExportButton.click();
  await settle();
  const choices = document.querySelectorAll(".export-kind-option");
  assert.deepEqual(choices.map((choice) => choice.disabled), [true, false, true]);
  await choices[1].click();
  await exporting;
  assert.deepEqual(calls, [
    ["settings_character_choose_export", { kind: "card", defaultName: "alpha.card.char" }],
    ["settings_character_export", { path: "C:\\exports\\alpha.card.char", characterId: "alpha", kind: "card" }],
  ]);
  assert.equal(document.querySelector(".confirm-overlay"), null);
  assert.equal(fields.characterEditorButton.disabled, false);
  calls.length = 0;
  await fields.characterImportButton.click();
  assert.deepEqual(calls, [["settings_character_choose_import", { kind: "character" }]]);
  assert.equal(fields.characterImportButton.disabled, false);
  feature.dispose();
});

test("preparing migrated archive controls keeps their accessible state usable", async () => {
  const { feature, fields } = await characterSettings();
  feature.prepareControls();
  for (const id of ["characterExportButton", "ttsVoiceImportButton"]) {
    assert.equal(fields[id].disabled, false);
    assert.notEqual(fields[id].getAttribute("aria-disabled"), "true");
  }
  feature.dispose();
});

test("character feature cleanup closes its export choice and removes control listeners", async () => {
  const fixture = await characterSettings();
  const { feature, fields, document, calls } = fixture;
  const exporting = fields.characterExportButton.click();
  await settle();
  assert.ok(document.querySelector(".confirm-overlay"));
  feature.dispose();
  await exporting;
  await fields.characterEditorButton.click();
  await select(fixture, "beta");
  assert.equal(document.querySelector(".confirm-overlay"), null);
  assert.equal([...document.listeners.get("keydown") || []].length, 0);
  assert.deepEqual(calls, []);
});

test("uninitialized and unavailable character catalogs retain a supported empty selection", async () => {
  const fixture = await characterSettings({ initialize: false, handlers: {
    settings_characters_get: () => { throw new Error("SETTINGS_CORE_UNAVAILABLE"); },
  } });
  const { feature, fields, errors } = fixture;
  feature.prepareControls();
  assert.equal(feature.selectedThemeDefaults(), undefined);
  assert.equal(feature.currentCharacterId(), "");
  await feature.initialize();
  assert.equal(fields.characterSelect.children.length, 0);
  assert.equal(fields.characterSelect.disabled, true);
  assert.equal(fields.characterImportButton.disabled, false);
  assert.equal(fields.characterEditorButton.disabled, true);
  assert.match(errors.at(-1), /SETTINGS_CORE_UNAVAILABLE/);
  feature.dispose();
});

test("disposing a character feature isolates pending catalog and visual preview results", async () => {
  let resolveLifecycle, resolvePreview, previewArgs;
  const fixture = await characterSettings({ handlers: {
    runtime_lifecycle_snapshot: () => new Promise((resolve) => { resolveLifecycle = resolve; }),
    settings_character_visual_preview: (args) => {
      previewArgs = args;
      return new Promise((resolve) => { resolvePreview = resolve; });
    },
  } });
  const { feature, transitions, previews, calls } = fixture;
  await select(fixture, "beta");
  const publication = feature.refreshCatalog({ generationId: "generation-b" });
  await settle();
  feature.dispose();
  resolvePreview(visualPreview(previewArgs));
  resolveLifecycle(lifecycle("generation-b", 2));
  await Promise.all([publication, feature.waitForPreview()]);
  assert.deepEqual(transitions, []);
  assert.deepEqual(previews, []);
  assert.deepEqual(calls.map(([command]) => command), ["settings_character_visual_preview", "runtime_lifecycle_snapshot"]);
});


test("character and voice imports deliver plugin hints through the settings receipt", async () => {
  for (const kind of ["character", "voice"]) {
    const notices = [];
    const fixture = await characterSettings({ handlers: {
      settings_character_choose_import: () => `/tmp/fixture.${kind === "voice" ? "voice" : "char"}`,
      [kind === "voice" ? "settings_character_import_voice" : "settings_character_import"]: () => ({
        schemaVersion: 1, snapshot: catalog(), previousCoreGenerationId: "generation-a",
        restartState: "not_required", targetCharacterId: "alpha",
        pluginRequirements: [{kind: "tts", type: "custom.voice@1", reasonCode: "PLUGIN_MISSING",
          plugins: [{id: "custom.voice", name: "示例语音插件"}], candidates: []}],
      }),
    }, feature: { notify: (message, level) => notices.push({message, level}) } });
    await fixture.fields[kind === "voice" ? "ttsVoiceImportButton" : "characterImportButton"].click();
    assert.deepEqual(fixture.errors, []);
    assert.ok(notices.some(item => item.level === "info" && item.message.includes("示例语音插件")));
    fixture.feature.dispose();
  }
});
