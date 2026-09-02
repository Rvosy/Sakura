import assert from "node:assert/strict";
import test from "node:test";

import {
  createRootSettingsClient,
  formatSettingsError,
  legacyDataImportPlanHasWork,
  normalizeAboutSettingsSnapshot,
  normalizeCharacterSettingsSnapshot,
  normalizeCharacterSwitchReceipt,
  normalizeLegacyDataImportPlan,
  normalizeStorageSettingsSnapshot,
  normalizeTelemetrySettingsSnapshot,
  normalizeUpdatePreferencesSnapshot,
  normalizeUpdateSettingsSnapshot,
} from "../settings/root-settings-runtime.js";

test("settings errors display their public message instead of protocol metadata", () => {
  assert.equal(
    formatSettingsError("MODEL_SLOT_INCOMPLETE|model.slots|core:chat|模型槽必须同时选择 Provider 和模型。"),
    "模型槽必须同时选择 Provider 和模型。",
  );
  assert.equal(
    formatSettingsError("连接失败：PROVIDER_TIMEOUT|providers.test_connection||供应商请求超时。"),
    "连接失败：供应商请求超时。",
  );
  assert.equal(formatSettingsError("请先选择模型。"), "请先选择模型。");
});

const emptyCharacters = Object.freeze({
  schemaVersion: 1,
  revision: 0,
  currentCharacterId: null,
  characters: [],
});

const unchangedCharacters = Object.freeze({
  schemaVersion: 1,
  snapshot: emptyCharacters,
  targetCharacterId: null,
  previousCoreGenerationId: "generation-a",
  restartState: "not_required",
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
  planToken: "a".repeat(64),
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
  blocked: false,
});

const noUpdate = Object.freeze({
  schemaVersion: 1,
  currentVersion: "1.0.0",
  mode: "installed",
  available: false,
  version: null,
  notes: null,
  pubDate: null,
  downloadUrl: null,
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

const updatePreferences = Object.freeze({
  schemaVersion: 1,
  autoCheckEnabled: true,
});

const about = Object.freeze({
  schemaVersion: 1,
  version: "1.0.0",
  repositoryUrl: "https://github.com/Rvosy/Sakura",
});

const telemetry = Object.freeze({
  schemaVersion: 1,
  enabled: true,
  installationId: "550e8400-e29b-41d4-a716-446655440000",
});

test("empty character snapshot renders as a supported no-selection state", () => {
  const normalized = normalizeCharacterSettingsSnapshot(emptyCharacters);
  assert.equal(normalized.character.current_character_id, "");
  assert.deepEqual(normalized.character.characters, []);
});

test("selected character must be a member and character fields are exact", () => {
  assert.throws(() => normalizeCharacterSettingsSnapshot({
    ...emptyCharacters,
    currentCharacterId: "missing",
  }), /CHARACTER_SETTINGS_RESPONSE_INVALID/);
  assert.throws(() => normalizeCharacterSettingsSnapshot({
    ...emptyCharacters,
    characters: [{ id: "sakura", displayName: "Sakura", hasVoice: true, path: "/secret" }],
  }), /CHARACTER_SETTINGS_RESPONSE_INVALID/);
});

test("character switch receipt binds the committed target and previous generation", () => {
  const selected = {
    ...emptyCharacters,
    revision: 2,
    currentCharacterId: "sakura",
    characters: [{ id: "sakura", displayName: "Sakura", hasVoice: true }],
  };
  const normalized = normalizeCharacterSwitchReceipt({
    ...unchangedCharacters,
    snapshot: selected,
    targetCharacterId: "sakura",
    restartState: "requested",
  });
  assert.equal(normalized.targetCharacterId, "sakura");
  assert.equal(normalized.character.current_character_id, "sakura");
  assert.throws(() => normalizeCharacterSwitchReceipt({
    ...unchangedCharacters,
    snapshot: selected,
    targetCharacterId: "other",
  }), /CHARACTER_SETTINGS_RESPONSE_INVALID/);
});

test("storage snapshot projects paths, status and reset availability", () => {
  assert.deepEqual(normalizeStorageSettingsSnapshot(defaultStorage), {
    ...defaultStorage,
    statusText: "默认位置，当前可用。",
    statusState: "ready",
    canReset: false,
  });
  const missing = normalizeStorageSettingsSnapshot({
    ...defaultStorage,
    ttsRoot: "/Volumes/Voice/TTS",
    ttsRootSource: "custom",
    ttsRootAvailable: false,
    reasonCode: "TTS_ROOT_MISSING",
  });
  assert.equal(missing.statusState, "failed");
  assert.equal(missing.canReset, true);
  assert.match(missing.statusText, /重新连接外置盘/);
});

test("storage availability and reason code cannot contradict each other", () => {
  assert.throws(() => normalizeStorageSettingsSnapshot({
    ...defaultStorage,
    ttsRootAvailable: false,
  }), /STORAGE_SETTINGS_RESPONSE_INVALID/);
  assert.throws(() => normalizeStorageSettingsSnapshot({
    ...defaultStorage,
    reasonCode: "TTS_ROOT_MISSING",
  }), /STORAGE_SETTINGS_RESPONSE_INVALID/);
});

test("legacy role data plan exposes only bounded counts and opaque identities", () => {
  assert.deepEqual(normalizeLegacyDataImportPlan(legacyDataPlan), legacyDataPlan);
  assert.throws(() => normalizeLegacyDataImportPlan({
    ...legacyDataPlan,
    planToken: "/private/source",
  }), /LEGACY_DATA_IMPORT_RESPONSE_INVALID/);
  assert.throws(() => normalizeLegacyDataImportPlan({
    ...legacyDataPlan,
    totals: { ...legacyDataPlan.totals, memoryNew: -1 },
  }), /LEGACY_DATA_IMPORT_RESPONSE_INVALID/);
});

test("update snapshot separates installed updater from portable download", () => {
  assert.deepEqual(normalizeUpdateSettingsSnapshot(noUpdate), noUpdate);
  assert.equal(normalizeUpdateSettingsSnapshot({
    ...noUpdate,
    mode: "portable",
    available: true,
    version: "1.1.0",
    downloadUrl: "https://example.test/Sakura.zip",
  }).mode, "portable");
  assert.throws(() => normalizeUpdateSettingsSnapshot({
    ...noUpdate,
    mode: "portable",
    available: true,
    version: "1.1.0",
  }), /UPDATE_SETTINGS_RESPONSE_INVALID/);
  assert.throws(() => normalizeUpdateSettingsSnapshot({
    ...noUpdate,
    notes: "stale notes",
  }), /UPDATE_SETTINGS_RESPONSE_INVALID/);
});

test("update preferences accept only the typed auto-check switch", () => {
  assert.deepEqual(normalizeUpdatePreferencesSnapshot(updatePreferences), updatePreferences);
  assert.throws(() => normalizeUpdatePreferencesSnapshot({
    ...updatePreferences,
    lastAnnouncedVersion: "1.1.0",
  }), /UPDATE_PREFERENCES_RESPONSE_INVALID/);
});

test("about snapshot exposes only the packaged version and fixed repository", () => {
  assert.deepEqual(normalizeAboutSettingsSnapshot(about), about);
  assert.throws(() => normalizeAboutSettingsSnapshot({
    ...about,
    repositoryUrl: "https://example.test/fork",
  }), /ABOUT_SETTINGS_RESPONSE_INVALID/);
});

test("telemetry snapshot accepts only the one switch and canonical v4 id", () => {
  assert.deepEqual(normalizeTelemetrySettingsSnapshot(telemetry), telemetry);
  assert.deepEqual(normalizeTelemetrySettingsSnapshot({
    ...telemetry,
    enabled: false,
    installationId: null,
  }).installationId, null);
  assert.throws(() => normalizeTelemetrySettingsSnapshot({
    ...telemetry,
    installationId: "PRIVATE-MACHINE-ID",
  }), /TELEMETRY_SETTINGS_RESPONSE_INVALID/);
  assert.throws(() => normalizeTelemetrySettingsSnapshot({
    ...telemetry,
    prompt: "PRIVATE CHAT",
  }), /TELEMETRY_SETTINGS_RESPONSE_INVALID/);
});

test("typed root settings client uses only frozen character storage, update, and about commands", async () => {
  const calls = [];
  const invoke = async (command, args) => {
    calls.push([command, args]);
    if (command === "settings_characters_get") return emptyCharacters;
    if (command.startsWith("settings_character")) return unchangedCharacters;
    if (command === "settings_storage_open_user_root") return null;
    if (command === "settings_update_get") return noUpdate;
    if (command === "settings_update_preferences_get") return updatePreferences;
    if (command === "settings_update_preferences_set") {
      return { ...updatePreferences, autoCheckEnabled: args.autoCheckEnabled };
    }
    if (command.startsWith("settings_update_")) return null;
    if (command === "settings_about_get") return about;
    if (command.startsWith("settings_about_open_")) return null;
    if (command === "settings_telemetry_get") return telemetry;
    if (command === "settings_telemetry_set_enabled") {
      return { ...telemetry, enabled: args.enabled };
    }
    if (command === "settings_telemetry_regenerate_installation_id") return telemetry;
    if (command === "settings_telemetry_open_documentation") return null;
    return defaultStorage;
  };
  const client = createRootSettingsClient({ invoke });
  await client.charactersGet();
  await client.characterImport("/tmp/role.char");
  await client.characterSelect("role");
  await client.storageGet();
  await client.storageOpenUserRoot();
  await client.storageChooseTtsRoot();
  await client.storageResetTtsRoot();
  await client.updateGet();
  await client.updateCachedGet();
  await client.updatePreferencesGet();
  await client.updatePreferencesSet(false);
  await client.updateInstall();
  await client.updateOpenPortableDownload("https://example.test/Sakura.zip");
  await client.aboutGet();
  await client.aboutOpenWebsite();
  await client.aboutOpenRepository();
  await client.aboutOpenChangelog();
  await client.aboutOpenSponsor();
  await client.telemetryGet();
  await client.telemetrySetEnabled(false);
  await client.telemetryRegenerateInstallationId();
  await client.telemetryOpenDocumentation();
  assert.deepEqual(calls, [
    ["settings_characters_get", undefined],
    ["settings_character_import", { path: "/tmp/role.char" }],
    ["settings_character_select", { characterId: "role" }],
    ["settings_storage_get", undefined],
    ["settings_storage_open_user_root", undefined],
    ["settings_storage_choose_tts_root", undefined],
    ["settings_storage_reset_tts_root", undefined],
    ["settings_update_get", undefined],
    ["settings_update_cached_get", undefined],
    ["settings_update_preferences_get", undefined],
    ["settings_update_preferences_set", { autoCheckEnabled: false }],
    ["settings_update_install", undefined],
    ["settings_update_open_portable_download", { url: "https://example.test/Sakura.zip" }],
    ["settings_about_get", undefined],
    ["settings_about_open_website", undefined],
    ["settings_about_open_repository", undefined],
    ["settings_about_open_changelog", undefined],
    ["settings_about_open_sponsor", undefined],
    ["settings_telemetry_get", undefined],
    ["settings_telemetry_set_enabled", { enabled: false }],
    ["settings_telemetry_regenerate_installation_id", undefined],
    ["settings_telemetry_open_documentation", undefined],
  ]);
});
