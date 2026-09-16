const CHARACTER_ERROR = "CHARACTER_SETTINGS_RESPONSE_INVALID";
const STORAGE_REASONS = Object.freeze({
  TTS_ROOT_MISSING: "目录不存在；请重新连接外置盘或选择其他目录。",
  TTS_ROOT_NOT_DIRECTORY: "当前路径不是目录。",
  TTS_ROOT_NOT_WRITABLE: "当前目录不可写。",
});

function fail(code) {
  throw new Error(code);
}

export function formatSettingsError(value) {
  const text = String(value ?? "").trim();
  return text.replace(
    /(^|[：:]\s*)[A-Z][A-Z0-9_]{2,63}\|[^\r\n|]{0,120}\|[^\r\n|]{0,120}\|([^\r\n]{1,512})$/,
    (_match, separator, message) => `${separator}${message}`,
  );
}

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

export function normalizeCharacterExportReceipt(receipt) {
  return Object.freeze({ ...receipt });
}

export function normalizeCharacterSwitchReceipt(receipt) {
  const requirements = receipt.pluginRequirements ?? [];
  const normalized = normalizeCharacterSettingsSnapshot(receipt.snapshot);
  if ((receipt.targetCharacterId || "") !== normalized.character.current_character_id) {
    fail(CHARACTER_ERROR);
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

export function normalizeLegacyDataImportPlan(plan) {
  return Object.freeze({ ...plan });
}

export function legacyDataImportPlanHasWork(plan) {
  const totals = plan?.totals;
  return Boolean(
    totals
    && (
      (plan.packagesNew || 0)
      + (plan.reassociatedRecords || 0)
      + totals.historyNew
      + totals.memoryNew
      + totals.historyConflicts
      + totals.memoryConflicts
      + totals.recoverableErrors
    ) > 0
  );
}

export function normalizeUpdateSettingsSnapshot(snapshot) {
  return Object.freeze({ ...snapshot });
}

export function normalizeUpdatePreferencesSnapshot(snapshot) {
  return Object.freeze({ ...snapshot });
}

export function normalizeAboutSettingsSnapshot(snapshot) {
  return Object.freeze({ ...snapshot });
}

export function normalizeTelemetrySettingsSnapshot(snapshot) {
  return Object.freeze({ ...snapshot });
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
      return normalizeCharacterExportReceipt(
        await invoke("settings_character_export", { path, characterId, kind }),
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
    async legacyRoleDataImportChoose(selectionId = null, roleMapping = {}) {
      const plan = await invoke("settings_legacy_data_import_choose", { selectionId, roleMapping });
      return plan === null ? null : normalizeLegacyDataImportPlan(plan);
    },
    async legacyRoleDataImportApply(selectionId, planToken, overwriteConflicts) {
      const report = await invoke("settings_legacy_data_import_apply", {
        selectionId,
        planToken,
        overwriteConflicts,
      });
      if (
        report?.schemaVersion !== 1
        || report.outcome !== "completed"
        || typeof report.importId !== "string"
      ) fail(LEGACY_DATA_ERROR);
      return Object.freeze({ ...report });
    },
    async updateGet() {
      return normalizeUpdateSettingsSnapshot(await invoke("settings_update_get"));
    },
    async updateCachedGet() {
      const snapshot = await invoke("settings_update_cached_get");
      return snapshot === null ? null : normalizeUpdateSettingsSnapshot(snapshot);
    },
    async updatePreferencesGet() {
      return normalizeUpdatePreferencesSnapshot(await invoke("settings_update_preferences_get"));
    },
    async updatePreferencesSet(autoCheckEnabled) {
      return normalizeUpdatePreferencesSnapshot(
        await invoke("settings_update_preferences_set", { autoCheckEnabled }),
      );
    },
    async updateInstall() {
      return invoke("settings_update_install");
    },
    async updateOpenPortableDownload(url) {
      return invoke("settings_update_open_portable_download", { url });
    },
    async aboutGet() {
      return normalizeAboutSettingsSnapshot(await invoke("settings_about_get"));
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
      return normalizeTelemetrySettingsSnapshot(await invoke("settings_telemetry_get"));
    },
    async telemetrySetEnabled(enabled) {
      if (typeof enabled !== "boolean") fail(TELEMETRY_ERROR);
      return normalizeTelemetrySettingsSnapshot(
        await invoke("settings_telemetry_set_enabled", { enabled }),
      );
    },
    async telemetryRegenerateInstallationId() {
      return normalizeTelemetrySettingsSnapshot(
        await invoke("settings_telemetry_regenerate_installation_id"),
      );
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
