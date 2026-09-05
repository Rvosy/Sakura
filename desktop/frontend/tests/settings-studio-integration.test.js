import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import test from "node:test";

import * as characterRuntime from "../settings/character-switch-runtime.js";
import { normalizeCharacterSettingsSnapshot } from "../settings/root-settings-runtime.js";

const source = readFileSync(new URL("../settings/settings.js", import.meta.url), "utf8");

// Settings starts its window on import. Run its actual handlers with an isolated
// DOM/IPC boundary so these tests never open a window or write user settings.
function settingsHandlers(names, globals) {
  const context = vm.createContext({ ...characterRuntime, normalizeCharacterSettingsSnapshot, ...globals });
  for (const name of names) {
    const start = source.search(new RegExp(`^(?:async )?function ${name}\\(`, "m"));
    assert.notEqual(start, -1, `missing Settings handler ${name}`);
    const end = source.indexOf("\n}", start) + 2;
    vm.runInContext(source.slice(start, end), context, { filename: `settings.js:${name}` });
  }
  return context;
}

function control() {
  return {
    disabled: false, value: "", textContent: "", children: [],
    classList: { toggle() {}, contains: () => false },
    append(item) { this.children.push(item); },
    setAttribute() {}, removeAttribute() {},
  };
}

function catalog(ids = ["alpha", "beta"]) {
  return {
    schemaVersion: 1, revision: 1, currentCharacterId: "alpha",
    characters: ids.map((id) => ({ id, displayName: id, hasVoice: true, hasExportableVoice: true })),
  };
}

function characterSettings() {
  const fields = Object.fromEntries([
    "saveButton", "applyButton", "characterSelect", "characterImportButton", "ttsVoiceImportButton",
    "characterExportButton", "characterEditorButton", "characterArchiveHint",
  ].map((key) => [key, control()]));
  fields.pages = Object.fromEntries(["character", "appearance", "voice", "memory"].map((key) => [key, control()]));
  const calls = [];
  let dirty = true;
  const context = settingsHandlers([
    "syncCharacterArchiveState", "selectedCharacter", "pendingRuntimeCharacterId", "setCharacterArchiveBusy",
    "runCharacterArchiveAction", "launchCharacterStudio", "refreshDirty", "renderCharacters",
    "applyRuntimeCharacterSnapshot", "refreshRuntimeCharacterCatalog", "rebindSettingsAfterCharacterSwitch",
  ], {
    fields, document: { body: control(), createElement: control },
    request: { character: normalizeCharacterSettingsSnapshot(catalog()).character },
    runtimeSettingsHost: true, runtimeCharacterSnapshot: catalog(), runtimeCharacterDraftId: "alpha",
    characterArchiveBusy: false, characterSwitching: false, submissionBusy: false,
    characterCatalogRefreshRevision: 0, memoryState: {},
    currentCharacterHasDrafts: () => dirty, computeDirty: () => dirty,
    refreshSelect() {}, renderMemorySurface() {}, renderMemoryPage() {},
    setError(message) { assert.equal(message, "", message); },
    invoke: async (command, args) => { calls.push([command, args]); },
    rootSettingsClient: { charactersGet: async () => catalog() },
    runtimeProviderModelController: null, runtimeScreenAwarenessController: null,
    runtimeAppearanceController: null, runtimeToolsController: null,
    runtimePluginController: null, runtimeVoiceController: null,
  });
  context.applyRuntimeCharacterSnapshot(catalog());
  return { context, fields, calls, markSaved: () => { dirty = false; } };
}

test("unsaved layout and pending character selection both allow opening Studio without saving", async () => {
  const { context, fields, calls, markSaved } = characterSettings();
  for (const characterId of ["alpha", "beta"]) {
    context.runtimeCharacterDraftId = characterId;
    fields.characterSelect.value = characterId;
    context.refreshDirty();
    assert.equal(fields.characterEditorButton.disabled, false);
    assert.equal(fields.ttsVoiceImportButton.disabled, true);
    await context.launchCharacterStudio();
    assert.equal(calls.at(-1)[0], "open_character_studio");
    assert.equal(calls.at(-1)[1].characterId, characterId);
    assert.equal(context.runtimeCharacterSnapshot.currentCharacterId, "alpha");
    assert.equal(fields.characterEditorButton.disabled, false);
  }
  assert.deepEqual(calls.map(([command]) => command), ["open_character_studio", "open_character_studio"]);

  context.runtimeCharacterDraftId = "alpha";
  fields.characterSelect.value = "alpha";
  markSaved();
  context.refreshDirty();
  assert.equal(fields.characterEditorButton.disabled, false);
  assert.equal(fields.ttsVoiceImportButton.disabled, false, "saving updates draft-dependent controls without reopening Settings");
  context.characterSwitching = true;
  context.syncCharacterArchiveState();
  assert.equal(fields.characterEditorButton.disabled, true, "a live Core switch still locks the entry");
});

test("Studio catalog publication retains a valid pending selection but drops a removed target", async () => {
  const { context, fields } = characterSettings();
  context.runtimeCharacterDraftId = "beta";
  fields.characterSelect.value = "beta";
  await context.refreshRuntimeCharacterCatalog({});
  assert.equal(fields.characterSelect.value, "beta");
  assert.equal(context.pendingRuntimeCharacterId(), "beta");
  context.rootSettingsClient.charactersGet = async () => catalog(["alpha"]);
  await context.refreshRuntimeCharacterCatalog({});
  assert.equal(fields.characterSelect.value, "alpha");
  assert.equal(context.pendingRuntimeCharacterId(), null);
});

test("current-character publication refreshes the bound controllers and unlocks Settings with drafts intact", async () => {
  const { context, fields } = characterSettings();
  context.runtimeCharacterDraftId = "beta";
  fields.characterSelect.value = "beta";
  const refreshed = [];
  context.invoke = async () => ({
    supervisor: { generationId: "generation-b" },
    snapshot: { generationId: "generation-b", readiness: "ready" },
    characterPresentation: { generationId: "generation-b", characterId: "alpha" },
  });
  context.runtimeAppearanceController = {
    rebindGeneration: async (generationId) => { refreshed.push(generationId); },
  };
  context.runtimeVoiceController = {
    refreshCurrent: async ({ preserveDraft }) => { assert.equal(preserveDraft, true); refreshed.push("voice"); },
  };
  await context.refreshRuntimeCharacterCatalog({ generationId: "generation-b" });
  assert.deepEqual(refreshed, ["generation-b", "voice"]);
  assert.equal(fields.characterSelect.value, "beta");
  assert.equal(context.currentCharacterHasDrafts(), true);
  assert.equal(context.characterSwitching, false);
  assert.equal(context.memoryState.rebinding, false);
  assert.equal(fields.characterEditorButton.disabled, false);
});

test("plugin refresh retains Memory editors and detaches results from the old generation", async () => {
  const pluginSnapshot = { plugins: [{
    installId: "memory", pluginId: "com.example.memory", sections: [{
      sectionId: "archive", surface: "memory", values: {}, fields: [], actions: [],
      collections: [{ collectionId: "entries", pageSize: 20, fields: [], filters: [], columns: [] }],
    }],
  }] };
  const states = new Map();
  let resolveOldQuery;
  let queries = 0;
  const context = settingsHandlers([
    "applyRuntimePluginSnapshot", "pluginCollectionKey", "pluginCollectionRuntimeState", "queryPluginCollection", "clonePlain",
  ], {
    request: {}, pluginCollectionState: states, pluginState: {},
    window: { clearTimeout() {}, setTimeout() { assert.fail("stale query scheduled work"); } },
    initializePluginState() {},
    renderPluginPage() {}, renderMemorySurface() {}, renderAboutComponents() {},
    characterSwitching: false, memoryState: {}, pendingRuntimeCharacterId: () => null,
    projectPluginActivity: () => ({}), memoryActivityBlocksCollection: () => false,
    runtimePluginController: { collection() {
      queries += 1;
      return queries === 1 ? new Promise((resolve) => { resolveOldQuery = resolve; })
        : Promise.resolve({ items: [{ itemId: "fresh" }], total: 1, nextCursor: null });
    } },
  });
  context.applyRuntimePluginSnapshot(pluginSnapshot);
  const oldPlugin = context.request.plugins.items[0];
  const oldSection = oldPlugin.settings[0];
  const oldCollection = oldSection.collections[0];
  const oldState = context.pluginCollectionRuntimeState(oldPlugin, oldSection, oldCollection);
  oldState.editor = { itemId: "note", values: { content: "未保存的记忆" } };
  oldState.search = "旅行";
  const pending = context.queryPluginCollection(oldPlugin, oldSection, oldCollection, { render: false });

  context.applyRuntimePluginSnapshot(pluginSnapshot, { preserveDraft: true });
  const newPlugin = context.request.plugins.items[0];
  const newSection = newPlugin.settings[0];
  const newCollection = newSection.collections[0];
  const newState = context.pluginCollectionRuntimeState(newPlugin, newSection, newCollection);
  assert.notEqual(newState, oldState);
  assert.equal(JSON.stringify(newState.editor), JSON.stringify(oldState.editor));
  assert.equal(newState.search, "旅行");
  resolveOldQuery({ items: [{ itemId: "stale" }], total: 1, nextCursor: null });
  await pending;
  assert.equal(newState.items.length, 0);
  await context.queryPluginCollection(oldPlugin, oldSection, oldCollection);
  assert.equal(queries, 1, "old scheduled callbacks cannot issue new queries");
  await context.queryPluginCollection(newPlugin, newSection, newCollection, { render: false });
  assert.equal(newState.items[0].itemId, "fresh");
  assert.equal(newState.editor.values.content, "未保存的记忆");

  newState.editor = null;
  context.applyRuntimePluginSnapshot(pluginSnapshot, { preserveDraft: true });
  assert.equal([...states.values()][0].editor, null, "a clean collection must not acquire a phantom draft");

  context.applyRuntimePluginSnapshot(pluginSnapshot);
  assert.equal(states.size, 0, "an explicit discard still clears collection drafts");
});
