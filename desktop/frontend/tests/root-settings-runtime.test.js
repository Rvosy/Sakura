import assert from "node:assert/strict";
import test from "node:test";

import {
  createRootSettingsClient,
  legacyDataImportPlanHasWork,
  normalizeCharacterSettingsSnapshot,
  normalizeStorageSettingsSnapshot,
} from "../settings/root-settings-runtime.js";

const emptyCharacters = Object.freeze({
  schemaVersion: 1,
  revision: 0,
  currentCharacterId: null,
  characters: [],
});

const defaultStorage = Object.freeze({
  schemaVersion: 1,
  userRoot: "/Users/test/Library/Application Support/Sakura",
  ttsRoot: "/Users/test/Library/Application Support/Sakura/tts",
  ttsRootSource: "default",
  ttsRootAvailable: true,
  reasonCode: null,
});

const legacyDataPlan = Object.freeze({
  schemaVersion: 1,
  selectionId: "selection-a",
  planToken: "a".repeat(32),
  sourceLabel: "Sakura-0.9.10",
  characters: [{
    characterId: "Sakura",
    history: { new: 2, identical: 1, conflicts: 0 },
    memory: { new: 1, identical: 0, conflicts: 0 },
  }],
  charactersTruncated: false,
  totals: {
    historyNew: 2,
    historyIdentical: 1,
    historyConflicts: 0,
    memoryNew: 1,
    memoryIdentical: 0,
    memoryConflicts: 0,
    recoverableErrors: 1,
  },
  conflicts: [],
  requiresConflictConfirmation: false,
});

test("quarantine-only legacy plans still require apply", () => {
  const quarantineOnly = {
    ...legacyDataPlan,
    characters: [],
    totals: {
      historyNew: 0,
      historyIdentical: 0,
      historyConflicts: 0,
      memoryNew: 0,
      memoryIdentical: 0,
      memoryConflicts: 0,
      recoverableErrors: 1,
    },
  };
  assert.equal(legacyDataImportPlanHasWork(quarantineOnly), true);
  assert.equal(legacyDataImportPlanHasWork({
    ...quarantineOnly,
    totals: { ...quarantineOnly.totals, recoverableErrors: 0 },
  }), false);
});

test("large character lists and additive host fields remain displayable", () => {
  const characters = Array.from({ length: 300 }, (_, i) => ({
    id: `character-${i}`, displayName: "长名称".repeat(100),
    hasVoice: true, hasExportableVoice: true, futureField: "supported",
  }));
  const normalized = normalizeCharacterSettingsSnapshot({ ...emptyCharacters, characters });
  assert.equal(normalized.character.characters.length, 300);
  assert.equal(normalized.character.characters[299].display_name, characters[299].displayName);
});

test("storage snapshot projects paths, status and reset availability", () => {
  const current = normalizeStorageSettingsSnapshot(defaultStorage);
  assert.equal(current.ttsRoot, defaultStorage.ttsRoot);
  assert.equal(current.statusState, "ready");
  assert.equal(current.canReset, false);
  const missing = normalizeStorageSettingsSnapshot({
    ...defaultStorage,
    ttsRoot: "/Volumes/Voice/TTS",
    ttsRootSource: "custom",
    ttsRootAvailable: false,
    reasonCode: "TTS_ROOT_MISSING",
  });
  assert.equal(missing.statusState, "failed");
  assert.equal(missing.canReset, true);
});

test("package-only and orphan reassociation plans have executable work", () => {
  const totals = Object.fromEntries(Object.keys(legacyDataPlan.totals).map(key => [key, 0]));
  assert.equal(legacyDataImportPlanHasWork({ ...legacyDataPlan, totals, packagesNew: 1 }), true);
  assert.equal(legacyDataImportPlanHasWork({ ...legacyDataPlan, totals, reassociatedRecords: 2 }), true);
  assert.equal(legacyDataImportPlanHasWork({ ...legacyDataPlan, totals }), false);
});

test("role mapping is sent for a fresh plan before apply uses its token", async () => {
  const calls = [];
  const report = { schemaVersion: 1, outcome: "completed", importId: "import-result", plan: legacyDataPlan };
  const client = createRootSettingsClient({ invoke: async (command, args) => {
    calls.push({command,args});
    if (command === "settings_legacy_data_import_choose") return legacyDataPlan;
    return report;
  }});
  const mapping = { OldRole:"CurrentRole" };
  const plan = await client.legacyRoleDataImportChoose("old-selection", mapping);
  assert.deepEqual(plan, legacyDataPlan);
  assert.deepEqual(await client.legacyRoleDataImportApply(plan.selectionId, plan.planToken, false), report);
  assert.deepEqual(calls[0], {command:"settings_legacy_data_import_choose", args:{selectionId:"old-selection",roleMapping:mapping}});
  assert.deepEqual(calls[1].args, {selectionId:plan.selectionId,planToken:plan.planToken,overwriteConflicts:false});
});

test("settings client preserves host receipts, settings and cancelled selections", async () => {
  const responses = {
    settings_character_export: { schemaVersion: 1, outputPath: "/exports/Sakura.char", message: "导出完成" },
    settings_update_cached_get: null,
    settings_update_get: { available: true, version: "1.3.6", downloadUrl: "https://example.test/Sakura.zip" },
    settings_legacy_data_import_choose: null,
    settings_telemetry_set_enabled: { enabled: false, installationId: null },
  };
  const client = createRootSettingsClient({ invoke: async (command) => responses[command] });

  assert.deepEqual(await client.characterExport("/exports/Sakura.char", "Sakura", "full"), responses.settings_character_export);
  assert.equal(await client.updateCachedGet(), null);
  assert.deepEqual(await client.updateGet(), responses.settings_update_get);
  assert.equal(await client.legacyRoleDataImportChoose(), null);
  assert.deepEqual(await client.telemetrySetEnabled(false), responses.settings_telemetry_set_enabled);
});

test("settings client preserves native operation errors and their causes", async () => {
  const failure = new Error("LEGACY_IMPORT_RECOVERY_FAILED", { cause: new Error("original data is locked") });
  const client = createRootSettingsClient({ invoke: async () => { throw failure; } });
  await assert.rejects(client.legacyRoleDataImportApply("selection", "plan", false), (error) => error === failure);
  await assert.rejects(client.telemetrySetEnabled(false), (error) => error === failure);
});
