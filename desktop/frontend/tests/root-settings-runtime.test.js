import assert from "node:assert/strict";
import test from "node:test";

import {
  createRootSettingsClient,
  normalizeAboutSettingsSnapshot,
  normalizeCharacterSettingsSnapshot,
  normalizeCharacterSwitchReceipt,
  normalizeStorageSettingsSnapshot,
  normalizeUpdatePreferencesSnapshot,
  normalizeUpdateSettingsSnapshot,
} from "../settings/root-settings-runtime.js";

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

const updatePreferences = Object.freeze({
  schemaVersion: 1,
  autoCheckEnabled: true,
});

const about = Object.freeze({
  schemaVersion: 1,
  version: "1.0.0",
  repositoryUrl: "https://github.com/Rvosy/Sakura",
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
  await client.macosOpenSystemSettings();
  await client.macosOpenAppleSupport();
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
    ["settings_macos_open_system_settings", undefined],
    ["settings_macos_open_apple_support", undefined],
  ]);
});
