import assert from "node:assert/strict";
import test from "node:test";

import {
  createRootSettingsClient,
  formatSettingsError,
  legacyDataImportPlanHasWork,
  normalizeCharacterSettingsSnapshot,
  normalizeLegacyDataImportPlan,
  normalizeStorageSettingsSnapshot,
} from "../settings/root-settings-runtime.js";

test("settings errors retain the context and original message", () => {
  const message = formatSettingsError("MODEL_SLOT_INCOMPLETE|model.slots|core:chat|模型槽必须同时选择 Provider 和模型。");
  for (const detail of ["model.slots", "core:chat", "模型槽必须同时选择 Provider 和模型。"]) {
    assert.ok(message.includes(detail));
  }
  assert.equal(
    formatSettingsError("连接失败：PROVIDER_TIMEOUT|providers.test_connection||供应商请求超时。"),
    "连接失败：PROVIDER_TIMEOUT|providers.test_connection||供应商请求超时。",
  );
  assert.equal(
    formatSettingsError(
      "自动检测失败：PROVIDER_ACCESS_FORBIDDEN|providers.list_models||API HTTP 403: Model listing is not allowed (code: access_denied; type: permission_error)",
    ),
    "自动检测失败：PROVIDER_ACCESS_FORBIDDEN|providers.list_models||API HTTP 403: Model listing is not allowed (code: access_denied; type: permission_error)",
  );
  assert.equal(formatSettingsError("请先选择模型。"), "请先选择模型。");
});

test("clearing settings feedback does not create an unknown error", () => {
  assert.equal(formatSettingsError(""), "");
  assert.equal(formatSettingsError("  \n"), "");
  assert.ok(formatSettingsError(new Error("connection failed")).includes("connection failed"));
});

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
  const quarantineOnly = normalizeLegacyDataImportPlan({
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
  });
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
  const client = createRootSettingsClient({ invoke: async (command, args) => {
    calls.push({command,args});
    if (command === "settings_legacy_data_import_choose") return legacyDataPlan;
    return { schemaVersion:1, outcome:"completed", importId:"import-result", plan:legacyDataPlan };
  }});
  const mapping = { OldRole:"CurrentRole" };
  const plan = await client.legacyRoleDataImportChoose("old-selection", mapping);
  await client.legacyRoleDataImportApply(plan.selectionId, plan.planToken, false);
  assert.deepEqual(calls[0], {command:"settings_legacy_data_import_choose", args:{selectionId:"old-selection",roleMapping:mapping}});
  assert.deepEqual(calls[1].args, {selectionId:plan.selectionId,planToken:plan.planToken,overwriteConflicts:false});
});
