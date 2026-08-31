const CHARACTER_ERROR = "CHARACTER_SETTINGS_RESPONSE_INVALID";
const STORAGE_ERROR = "STORAGE_SETTINGS_RESPONSE_INVALID";
const LEGACY_DATA_ERROR = "LEGACY_DATA_IMPORT_RESPONSE_INVALID";
const UPDATE_ERROR = "UPDATE_SETTINGS_RESPONSE_INVALID";
const UPDATE_PREFERENCES_ERROR = "UPDATE_PREFERENCES_RESPONSE_INVALID";
const ABOUT_ERROR = "ABOUT_SETTINGS_RESPONSE_INVALID";
const STORAGE_REASONS = Object.freeze({
  TTS_ROOT_MISSING: "目录不存在；请重新连接外置盘或选择其他目录。",
  TTS_ROOT_NOT_DIRECTORY: "当前路径不是目录。",
  TTS_ROOT_NOT_WRITABLE: "当前目录不可写。",
});

function fail(code) {
  throw new Error(code);
}

export function normalizeCharacterSettingsSnapshot(snapshot) {
  if (
    snapshot?.schemaVersion !== 1
    || !Number.isSafeInteger(snapshot.revision)
    || snapshot.revision < 0
    || (snapshot.currentCharacterId !== null && typeof snapshot.currentCharacterId !== "string")
    || !Array.isArray(snapshot.characters)
    || snapshot.characters.length > 256
  ) fail(CHARACTER_ERROR);

  const ids = new Set();
  const characters = snapshot.characters.map((character) => {
    if (
      !character
      || typeof character.id !== "string"
      || !character.id
      || character.id.length > 128
      || ids.has(character.id)
      || typeof character.displayName !== "string"
      || !character.displayName
      || character.displayName.length > 128
      || typeof character.hasVoice !== "boolean"
      || Object.keys(character).length !== 3
    ) fail(CHARACTER_ERROR);
    ids.add(character.id);
    return Object.freeze({
      id: character.id,
      display_name: character.displayName,
      has_voice: character.hasVoice,
      has_exportable_voice: false,
    });
  });
  if (snapshot.currentCharacterId !== null && !ids.has(snapshot.currentCharacterId)) {
    fail(CHARACTER_ERROR);
  }
  return Object.freeze({
    snapshot: Object.freeze({ ...snapshot, characters: Object.freeze([...snapshot.characters]) }),
    character: Object.freeze({
      current_character_id: snapshot.currentCharacterId || "",
      characters: Object.freeze(characters),
    }),
  });
}

export function normalizeCharacterSwitchReceipt(receipt) {
  const keys = receipt && typeof receipt === "object" ? Object.keys(receipt).sort() : [];
  const expected = [
    "previousCoreGenerationId",
    "restartState",
    "schemaVersion",
    "snapshot",
    "targetCharacterId",
  ];
  if (
    receipt?.schemaVersion !== 1
    || keys.length !== expected.length
    || keys.some((key, index) => key !== expected[index])
    || typeof receipt.previousCoreGenerationId !== "string"
    || !receipt.previousCoreGenerationId
    || !["not_required", "requested"].includes(receipt.restartState)
    || (receipt.targetCharacterId !== null && typeof receipt.targetCharacterId !== "string")
  ) fail(CHARACTER_ERROR);
  const normalized = normalizeCharacterSettingsSnapshot(receipt.snapshot);
  if ((receipt.targetCharacterId || "") !== normalized.character.current_character_id) {
    fail(CHARACTER_ERROR);
  }
  return Object.freeze({
    ...normalized,
    previousCoreGenerationId: receipt.previousCoreGenerationId,
    restartState: receipt.restartState,
    targetCharacterId: receipt.targetCharacterId,
  });
}

export function normalizeStorageSettingsSnapshot(snapshot) {
  const keys = snapshot && typeof snapshot === "object" ? Object.keys(snapshot).sort() : [];
  const expected = [
    "reasonCode",
    "schemaVersion",
    "ttsRoot",
    "ttsRootAvailable",
    "ttsRootSource",
    "userRoot",
  ];
  if (
    snapshot?.schemaVersion !== 1
    || keys.length !== expected.length
    || keys.some((key, index) => key !== expected[index])
    || typeof snapshot.userRoot !== "string"
    || !snapshot.userRoot
    || typeof snapshot.ttsRoot !== "string"
    || !snapshot.ttsRoot
    || !["default", "custom"].includes(snapshot.ttsRootSource)
    || typeof snapshot.ttsRootAvailable !== "boolean"
    || (snapshot.reasonCode !== null && !Object.hasOwn(STORAGE_REASONS, snapshot.reasonCode))
    || (snapshot.ttsRootAvailable && snapshot.reasonCode !== null)
    || (!snapshot.ttsRootAvailable && snapshot.reasonCode === null)
  ) fail(STORAGE_ERROR);

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
  if (
    plan?.schemaVersion !== 1
    || typeof plan.selectionId !== "string"
    || !plan.selectionId
    || typeof plan.planToken !== "string"
    || !/^[a-f0-9]{64}$/.test(plan.planToken)
    || typeof plan.sourceLabel !== "string"
    || !Array.isArray(plan.characters)
    || plan.characters.length > 256
    || typeof plan.charactersTruncated !== "boolean"
    || !Array.isArray(plan.conflicts)
    || plan.conflicts.length > 100
    || typeof plan.requiresConflictConfirmation !== "boolean"
    || typeof plan.blocked !== "boolean"
    || !plan.totals
  ) fail(LEGACY_DATA_ERROR);
  const countKeys = [
    "historyNew", "historyIdentical", "historyConflicts",
    "memoryNew", "memoryIdentical", "memoryConflicts", "recoverableErrors",
  ];
  if (countKeys.some((key) => !Number.isSafeInteger(plan.totals[key]) || plan.totals[key] < 0)) {
    fail(LEGACY_DATA_ERROR);
  }
  for (const character of plan.characters) {
    if (
      typeof character?.characterId !== "string"
      || !character.characterId
      || !character.history
      || !character.memory
      || ["new", "identical", "conflicts"].some((key) => (
        !Number.isSafeInteger(character.history[key])
        || character.history[key] < 0
        || !Number.isSafeInteger(character.memory[key])
        || character.memory[key] < 0
      ))
    ) fail(LEGACY_DATA_ERROR);
  }
  return Object.freeze({ ...plan });
}

export function legacyDataImportPlanHasWork(plan) {
  const totals = plan?.totals;
  return Boolean(
    totals
    && (
      totals.historyNew
      + totals.memoryNew
      + totals.historyConflicts
      + totals.memoryConflicts
      + totals.recoverableErrors
    ) > 0
  );
}

export function normalizeUpdateSettingsSnapshot(snapshot) {
  const keys = snapshot && typeof snapshot === "object" ? Object.keys(snapshot).sort() : [];
  const expected = [
    "available",
    "currentVersion",
    "downloadUrl",
    "mode",
    "notes",
    "pubDate",
    "schemaVersion",
    "version",
  ];
  if (
    snapshot?.schemaVersion !== 1
    || keys.length !== expected.length
    || keys.some((key, index) => key !== expected[index])
    || typeof snapshot.currentVersion !== "string"
    || !snapshot.currentVersion
    || !["installed", "portable"].includes(snapshot.mode)
    || typeof snapshot.available !== "boolean"
    || (snapshot.version !== null && typeof snapshot.version !== "string")
    || (snapshot.notes !== null && typeof snapshot.notes !== "string")
    || (snapshot.pubDate !== null && typeof snapshot.pubDate !== "string")
    || (snapshot.downloadUrl !== null && typeof snapshot.downloadUrl !== "string")
    || (!snapshot.available && (
      snapshot.version !== null
      || snapshot.notes !== null
      || snapshot.pubDate !== null
      || snapshot.downloadUrl !== null
    ))
    || (snapshot.available && !snapshot.version)
    || (snapshot.available && snapshot.mode === "portable" && !snapshot.downloadUrl?.startsWith("https://"))
    || (snapshot.mode === "installed" && snapshot.downloadUrl !== null)
  ) fail(UPDATE_ERROR);
  return Object.freeze({ ...snapshot });
}

export function normalizeUpdatePreferencesSnapshot(snapshot) {
  const keys = snapshot && typeof snapshot === "object" ? Object.keys(snapshot).sort() : [];
  if (
    snapshot?.schemaVersion !== 1
    || keys.length !== 2
    || keys[0] !== "autoCheckEnabled"
    || keys[1] !== "schemaVersion"
    || typeof snapshot.autoCheckEnabled !== "boolean"
  ) fail(UPDATE_PREFERENCES_ERROR);
  return Object.freeze({ ...snapshot });
}

export function normalizeAboutSettingsSnapshot(snapshot) {
  const keys = snapshot && typeof snapshot === "object" ? Object.keys(snapshot).sort() : [];
  const expected = ["repositoryUrl", "schemaVersion", "version"];
  if (
    snapshot?.schemaVersion !== 1
    || keys.length !== expected.length
    || keys.some((key, index) => key !== expected[index])
    || typeof snapshot.version !== "string"
    || !snapshot.version
    || typeof snapshot.repositoryUrl !== "string"
    || snapshot.repositoryUrl !== "https://github.com/Rvosy/Sakura"
  ) fail(ABOUT_ERROR);
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
    async characterSelect(characterId) {
      return normalizeCharacterSwitchReceipt(
        await invoke("settings_character_select", { characterId }),
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
    async legacyRoleDataImportChoose() {
      const plan = await invoke("settings_legacy_data_import_choose");
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
  });
}
