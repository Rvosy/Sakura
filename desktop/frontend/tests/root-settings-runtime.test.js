import assert from "node:assert/strict";
import test from "node:test";

import {
  createRootSettingsClient,
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

test("settings client preserves host receipts, settings and cancelled selections", async () => {
  const responses = {
    settings_character_export: { schemaVersion: 1, outputPath: "/exports/Sakura.char", message: "导出完成" },
    settings_update_cached_get: null,
    settings_update_get: { available: true, version: "1.3.6", downloadUrl: "https://example.test/Sakura.zip" },
    settings_storage_choose_tts_root: null,
    settings_telemetry_set_enabled: { enabled: false, installationId: null },
  };
  const client = createRootSettingsClient({ invoke: async (command) => responses[command] });

  assert.deepEqual(await client.characterExport("/exports/Sakura.char", "Sakura", "full"), responses.settings_character_export);
  assert.equal(await client.updateCachedGet(), null);
  assert.deepEqual(await client.updateGet(), responses.settings_update_get);
  assert.equal(await client.storageChooseTtsRoot(), null);
  assert.deepEqual(await client.telemetrySetEnabled(false), responses.settings_telemetry_set_enabled);
});

test("settings client preserves native operation errors and their causes", async () => {
  const failure = new Error("STORAGE_ACCESS_FAILED", { cause: new Error("data directory is locked") });
  const client = createRootSettingsClient({ invoke: async () => { throw failure; } });
  await assert.rejects(client.storageOpenUserRoot(), (error) => error === failure);
  await assert.rejects(client.telemetrySetEnabled(false), (error) => error === failure);
});
