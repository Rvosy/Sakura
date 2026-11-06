const CHARACTER_ERROR = "CHARACTER_SETTINGS_RESPONSE_INVALID";
const STORAGE_REASONS = Object.freeze({
  TTS_ROOT_MISSING: "目录不存在；请重新连接外置盘或选择其他目录。",
  TTS_ROOT_NOT_DIRECTORY: "当前路径不是目录。",
  TTS_ROOT_NOT_WRITABLE: "当前目录不可写。",
});

export function normalizeCharacterSettingsSnapshot(snapshot) {
  const characters = snapshot.characters.map((character) => Object.freeze({
    id: character.id,
    display_name: character.displayName,
    has_voice: character.hasVoice,
    has_exportable_voice: character.hasExportableVoice,
  }));
  return Object.freeze({
    snapshot: Object.freeze({ ...snapshot, characters: Object.freeze([...snapshot.characters]) }),
    character: Object.freeze({
      current_character_id: snapshot.currentCharacterId || "",
      characters: Object.freeze(characters),
    }),
  });
}

export function normalizeCharacterSwitchReceipt(receipt) {
  const requirements = receipt.pluginRequirements ?? [];
  const normalized = normalizeCharacterSettingsSnapshot(receipt.snapshot);
  if ((receipt.targetCharacterId || "") !== normalized.character.current_character_id) {
    throw new Error(CHARACTER_ERROR);
  }
  return Object.freeze({
    ...normalized,
    previousCoreGenerationId: receipt.previousCoreGenerationId,
    restartState: receipt.restartState,
    characterChanged: receipt.characterChanged === true,
    targetCharacterId: receipt.targetCharacterId,
    pluginRequirements: requirements,
  });
}

export function normalizeStorageSettingsSnapshot(snapshot) {
  const sourceLabel = snapshot.ttsRootSource === "custom" ? "自定义位置" : "默认位置";
  return Object.freeze({
    ...snapshot,
    statusText: snapshot.ttsRootAvailable
      ? `${sourceLabel}，当前可用。`
      : `${sourceLabel}不可用：${STORAGE_REASONS[snapshot.reasonCode]}`,
    statusState: snapshot.ttsRootAvailable ? "ready" : "failed",
    canReset: snapshot.ttsRootSource === "custom",
  });
}

export function createRootSettingsClient({ invoke }) {
  if (typeof invoke !== "function") throw new TypeError("invoke is required");
  return Object.freeze({
    async charactersGet() {
      return normalizeCharacterSettingsSnapshot(await invoke("settings_characters_get"));
    },
    async characterImport(path) {
      return normalizeCharacterSwitchReceipt(await invoke("settings_character_import", { path }));
    },
    async characterVoiceImport(path, characterId) {
      return normalizeCharacterSwitchReceipt(
        await invoke("settings_character_import_voice", { path, characterId }),
      );
    },
    async characterExport(path, characterId, kind) {
      return invoke("settings_character_export", { path, characterId, kind });
    },
    async characterDelete(characterId) {
      return normalizeCharacterSwitchReceipt(
        await invoke("settings_character_delete", { characterId }),
      );
    },
    async characterSelect(characterId, visualSelections) {
      return normalizeCharacterSwitchReceipt(
        await invoke("settings_character_select", { characterId, ...(visualSelections ? { visualSelections } : {}) }),
      );
    },
    async storageGet() {
      return normalizeStorageSettingsSnapshot(await invoke("settings_storage_get"));
    },
    async storageOpenUserRoot() {
      return invoke("settings_storage_open_user_root");
    },
    async storageChooseTtsRoot() {
      const snapshot = await invoke("settings_storage_choose_tts_root");
      return snapshot === null ? null : normalizeStorageSettingsSnapshot(snapshot);
    },
    async storageResetTtsRoot() {
      return normalizeStorageSettingsSnapshot(await invoke("settings_storage_reset_tts_root"));
    },
    async updateGet() {
      return invoke("settings_update_get");
    },
    async updateCachedGet() {
      return invoke("settings_update_cached_get");
    },
    async updatePreferencesGet() {
      return invoke("settings_update_preferences_get");
    },
    async updatePreferencesSet(autoCheckEnabled) {
      return invoke("settings_update_preferences_set", { autoCheckEnabled });
    },
    async updateInstall() {
      return invoke("settings_update_install");
    },
    async updateOpenPortableDownload(url) {
      return invoke("settings_update_open_portable_download", { url });
    },
    async aboutGet() {
      return invoke("settings_about_get");
    },
    async aboutOpenWebsite() {
      return invoke("settings_about_open_website");
    },
    async aboutOpenRepository() {
      return invoke("settings_about_open_repository");
    },
    async aboutOpenChangelog() {
      return invoke("settings_about_open_changelog");
    },
    async aboutOpenSponsor() {
      return invoke("settings_about_open_sponsor");
    },
    async telemetryGet() {
      return invoke("settings_telemetry_get");
    },
    async telemetrySetEnabled(enabled) {
      return invoke("settings_telemetry_set_enabled", { enabled });
    },
    async telemetryRegenerateInstallationId() {
      return invoke("settings_telemetry_regenerate_installation_id");
    },
    async telemetryOpenDocumentation() {
      return invoke("settings_telemetry_open_documentation");
    },
    async macosOpenSystemSettings() {
      return invoke("settings_macos_open_system_settings");
    },
    async macosOpenAppleSupport() {
      return invoke("settings_macos_open_apple_support");
    },
  });
}
