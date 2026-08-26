import assert from "node:assert/strict";
import test from "node:test";

import {
  createRootSettingsClient,
  normalizeCharacterSettingsSnapshot,
  normalizeStorageSettingsSnapshot,
  normalizeUpdateSettingsSnapshot,
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

const noUpdate = Object.freeze({
  schemaVersion: 1,
  currentVersion: "1.0.0",
  mode: "installed",
  available: false,
  version: null,
  notes: null,
  downloadUrl: null,
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
});

test("typed root settings client uses only frozen character storage and update commands", async () => {
  const calls = [];
  const invoke = async (command, args) => {
    calls.push([command, args]);
    if (command.startsWith("settings_character") || command === "settings_characters_get") {
      return emptyCharacters;
    }
    if (command === "settings_storage_open_user_root") return null;
    if (command === "settings_update_get") return noUpdate;
    if (command.startsWith("settings_update_")) return null;
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
  await client.updateInstall();
  await client.updateOpenPortableDownload("https://example.test/Sakura.zip");
  assert.deepEqual(calls, [
    ["settings_characters_get", undefined],
    ["settings_character_import", { path: "/tmp/role.char" }],
    ["settings_character_select", { characterId: "role" }],
    ["settings_storage_get", undefined],
    ["settings_storage_open_user_root", undefined],
    ["settings_storage_choose_tts_root", undefined],
    ["settings_storage_reset_tts_root", undefined],
    ["settings_update_get", undefined],
    ["settings_update_install", undefined],
    ["settings_update_open_portable_download", { url: "https://example.test/Sakura.zip" }],
  ]);
});
