import {
  createRootSettingsClient,
  normalizeCharacterSettingsSnapshot,
} from "./root-settings-runtime.js";
import {
  applyCharacterCatalogChange,
  applyCharacterSwitch,
  pendingCharacterSelection,
  syncCharacterEditorControl,
  setCharacterSwitchLock,
} from "./character-switch-runtime.js";
import { isHexColor, RUNTIME_THEME_FIELDS as runtimeThemeLegacyFields } from "../core/theme-runtime.js";
import { createCharacterVisualSettings } from "./character-visual-settings.js";

export function createCharacterSettingsFeature({
  document,
  window,
  invoke,
  onDirty: refreshDirty,
  onError: setError,
  notify,
  enhanceSelect,
  refreshSelect,
  hasCharacterDrafts: currentCharacterHasDrafts,
  isSubmitting,
  applyPreviewTheme,
  rebindSettings,
  clearCharacterState,
  renderMemorySurface,
  openPlugin = () => {},
  reportError = () => {},
}) {
  const rootSettingsClient = createRootSettingsClient({ invoke });
  const fields = {
    characterSelect: document.getElementById("characterSelect"),
    characterImportButton: document.getElementById("characterImportButton"),
    ttsVoiceImportButton: document.getElementById("ttsVoiceImportButton"),
    characterExportButton: document.getElementById("characterExportButton"),
    characterEditorButton: document.getElementById("characterEditorButton"),
    characterArchiveHint: document.getElementById("characterArchiveHint"),
    saveButton: document.getElementById("saveButton"),
    applyButton: document.getElementById("applyButton"),
    pages: {
      character: document.getElementById("page-character"),
      appearance: document.getElementById("page-appearance"),
      voice: document.getElementById("page-voice"),
      memory: document.getElementById("page-memory"),
    },
  };
  let characterView = null;
  let runtimeCharacterSnapshot = null;
  let runtimeCharacterDraftId = "";
  let runtimeCharacterVisualPreviewRevision = 0;
  let runtimeCharacterVisualPreviewPromise = Promise.resolve();
  let characterArchiveBusy = false;
  let characterSwitching = false;
  let characterCatalogRefreshRevision = 0;
  const memoryState = { rebinding: false };
  let closeExportKindDialog = null;
  let disposed = false;
  const listeners = [];
  const visualSettings = createCharacterVisualSettings({ document, invoke, refreshSelect, onDirty: refreshDirty, openPlugin, reportError });

  const characterExportOptions = [
    {
      kind: "full",
      label: "完整包 (.char)",
      description: "导出角色配置和可携带语音模型，适合完整迁移。",
      requiresVoice: true,
    },
    {
      kind: "card",
      label: "单角色包 (.char)",
      description: "只导出角色配置，不包含语音模型。",
      requiresVoice: false,
    },
    {
      kind: "voice",
      label: "语音包 (.voice)",
      description: "只导出当前角色的可携带 TTS 模型。",
      requiresVoice: true,
    },
  ];

  function selectedCharacter() {
    const id = fields.characterSelect.value;
    return characterView?.characters.find((item) => item.id === id) || null;
  }

  function selectedCharacterHasExportableVoice() {
    return Boolean(selectedCharacter()?.has_exportable_voice);
  }

  function renderCharacters() {
    fields.characterSelect.textContent = "";
    characterView.characters.forEach((character) => {
      const option = document.createElement("option");
      option.value = character.id;
      option.textContent = character.display_name || character.id;
      fields.characterSelect.append(option);
    });
    const pendingCharacterId = pendingCharacterSelection({
      committedCharacterId: characterView.current_character_id,
      selectedCharacterId: runtimeCharacterDraftId,
    });
    fields.characterSelect.value = pendingCharacterId
      || characterView.current_character_id;
    syncCharacterArchiveState();
  }

  function applyRuntimeCharacterSnapshot(snapshot, { preserveSelection = false } = {}) {
    if (disposed) return;
    const normalized = snapshot?.snapshot && snapshot?.character
      ? snapshot
      : normalizeCharacterSettingsSnapshot(snapshot);
    const pendingSelection = preserveSelection ? pendingRuntimeCharacterId() : null;
    runtimeCharacterSnapshot = normalized.snapshot;
    runtimeCharacterDraftId = normalized.character.characters.some((item) => item.id === pendingSelection)
      ? pendingSelection : normalized.character.current_character_id;
    characterView = normalized.character;
    renderCharacters();
    refreshSelect(fields.characterSelect);
  }

  function syncCharacterArchiveState() {
    if (!characterView || disposed) {
      return;
    }
    const pendingCharacterId = pendingRuntimeCharacterId();
    setCharacterSwitchLock({
      pages: [fields.pages.character],
      // Global drafts remain editable on their own pages, but the aggregate
      // submit actions must not cross the generation hand-off.
      submitControls: [fields.saveButton, fields.applyButton],
    }, characterSwitching);
    for (const page of [fields.pages.appearance, fields.pages.voice, fields.pages.memory]) {
      if (!page) continue;
      page.inert = characterSwitching || Boolean(pendingCharacterId);
      page.setAttribute("aria-busy", String(characterSwitching));
      page.setAttribute("aria-disabled", String(Boolean(pendingCharacterId)));
    }
    if (isSubmitting()) {
      fields.saveButton.disabled = true;
      fields.applyButton.disabled = true;
    }
    const character = selectedCharacter();
    visualSettings.sync(character?.id, characterArchiveBusy || characterSwitching || isSubmitting());
    const hasCharacter = Boolean(character);
    fields.characterSelect.disabled = characterArchiveBusy || characterSwitching
      || !characterView.characters.length;
    fields.characterImportButton.disabled = characterArchiveBusy || characterSwitching
      || Boolean(pendingCharacterId);
    fields.ttsVoiceImportButton.disabled = characterArchiveBusy || characterSwitching
      || !hasCharacter || Boolean(pendingCharacterId) || currentCharacterHasDrafts();
    fields.characterExportButton.disabled = characterArchiveBusy || characterSwitching
      || !hasCharacter || Boolean(pendingCharacterId);
    syncCharacterEditorControl(
      fields.characterEditorButton,
      characterArchiveBusy || characterSwitching || !hasCharacter,
    );
    fields.characterArchiveHint.textContent = pendingCharacterId
      ? `应用后切换到 ${character?.display_name || pendingCharacterId}。`
      : currentCharacterHasDrafts()
        ? "请先保存改动再导入语音；导出使用已保存的版本。"
        : hasCharacter
        ? ""
      : "请导入 .char 角色包。";
    fields.characterArchiveHint.hidden = !fields.characterArchiveHint.textContent;
    refreshSelect(fields.characterSelect);
  }

  function setCharacterArchiveBusy(busy) {
    characterArchiveBusy = Boolean(busy);
    syncCharacterArchiveState();
  }

  function pendingRuntimeCharacterId() {
    return pendingCharacterSelection({
      committedCharacterId: runtimeCharacterSnapshot?.currentCharacterId,
      selectedCharacterId: runtimeCharacterDraftId,
    });
  }

  function runtimeVisualPreviewTheme(publication) {
    const presentation = publication?.presentation;
    const appearance = publication?.appearance;
    if (
      publication?.schemaVersion !== 1
      || !Number.isSafeInteger(publication.windowGeneration)
      || !Number.isSafeInteger(publication.revision)
      || appearance?.coreGenerationId !== presentation?.generationId
      || appearance?.characterId !== presentation?.characterId
    ) throw new Error("CHARACTER_VISUAL_PREVIEW_INVALID");
    return Object.fromEntries(Object.entries(runtimeThemeLegacyFields).map(([source, target]) => {
      const value = appearance.values?.themeTokens?.[source];
      if (!isHexColor(value)) throw new Error("CHARACTER_VISUAL_PREVIEW_INVALID");
      return [target, value];
    }));
  }

  function previewRuntimeCharacterVisual(characterId) {
    if (!characterId) return;
    const pending = (async () => {
      const revision = ++runtimeCharacterVisualPreviewRevision;
      const publication = await invoke("settings_character_visual_preview", {
        characterId,
        revision,
      });
      if (
        revision !== runtimeCharacterVisualPreviewRevision
        || characterId !== runtimeCharacterDraftId
        || publication?.revision !== revision
        || publication?.presentation?.characterId !== characterId
      ) return;
      applyPreviewTheme(runtimeVisualPreviewTheme(publication));
    })();
    runtimeCharacterVisualPreviewPromise = pending;
    return pending;
  }

  async function discardRuntimeCharacterSelection() {
    visualSettings.discard();
    runtimeCharacterDraftId = runtimeCharacterSnapshot?.currentCharacterId || "";
    fields.characterSelect.value = runtimeCharacterDraftId;
    refreshSelect(fields.characterSelect);
    syncCharacterArchiveState();
    refreshDirty();
    if (runtimeCharacterDraftId) await previewRuntimeCharacterVisual(runtimeCharacterDraftId);
  }

  async function rebindSettingsAfterCharacterSwitch(lifecycle) {
    if (disposed) return;
    const generationId = lifecycle?.supervisor?.generationId;
    if (typeof generationId !== "string" || !generationId) {
      throw new Error("CHARACTER_SWITCH_IDENTITY_INVALID");
    }
    await rebindSettings(generationId);
    if (disposed) return;
    const snapshot = await rootSettingsClient.charactersGet();
    if (disposed) return;
    applyRuntimeCharacterSnapshot(snapshot, { preserveSelection: true });
    memoryState.rebinding = false;
    refreshDirty();
  }

  async function refreshRuntimeCharacterCatalog(payload) {
    if (disposed) return;
    const revision = ++characterCatalogRefreshRevision;
    const generationId = typeof payload?.generationId === "string"
      ? payload.generationId
      : "";
    const rebinding = Boolean(generationId);
    if (rebinding) {
      characterSwitching = true;
      memoryState.rebinding = true;
      syncCharacterArchiveState();
    }
    try {
      const applied = await applyCharacterCatalogChange({
        generationId,
        readLifecycle: () => invoke("runtime_lifecycle_snapshot"),
        readCatalog: () => rootSettingsClient.charactersGet(),
        applyCatalog: (snapshot) => applyRuntimeCharacterSnapshot(snapshot, { preserveSelection: true }),
        rebindSettings: rebindSettingsAfterCharacterSwitch,
      });
      if (applied && revision === characterCatalogRefreshRevision) {
        await visualSettings.refresh(runtimeCharacterDraftId);
        setError("");
      }
    } catch (error) {
      if (revision === characterCatalogRefreshRevision) {
        setError(`角色列表刷新失败：${String(error)}`);
      }
    } finally {
      if (rebinding && revision === characterCatalogRefreshRevision) {
        characterSwitching = false;
        memoryState.rebinding = false;
        renderMemorySurface();
        syncCharacterArchiveState();
      }
    }
  }

  async function applyRuntimeCharacterChange(receipt, previousLifecycle) {
    await applyCharacterSwitch({
      receipt,
      previousLifecycle,
      applyCommittedSnapshot: applyRuntimeCharacterSnapshot,
      clearCharacterState() {
        memoryState.rebinding = true;
        clearCharacterState();
      },
      rebindSettings: rebindSettingsAfterCharacterSwitch,
      setSwitching(value) {
        characterSwitching = value;
        if (!value) memoryState.rebinding = false;
        syncCharacterArchiveState();
      },
      readLifecycle: () => invoke("runtime_lifecycle_snapshot"),
      delay: (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds)),
    });
  }

  function characterExportDefaultName(kind) {
    const id = selectedCharacter()?.id || "character";
    if (kind === "voice") {
      return `${id}.voice`;
    }
    if (kind === "card") {
      return `${id}.card.char`;
    }
    return `${id}.char`;
  }

  async function chooseArchivePath(kind) {
    return invoke("settings_character_choose_import", { kind });
  }

  async function chooseExportPath(kind) {
    return invoke("settings_character_choose_export", {
      kind,
      defaultName: characterExportDefaultName(kind),
    });
  }

  function chooseExportKind() {
    return new Promise((resolve) => {
      const hasVoice = selectedCharacterHasExportableVoice();
      const overlay = document.createElement("div");
      overlay.className = "confirm-overlay";
      const dialog = document.createElement("section");
      dialog.className = "confirm-dialog export-kind-dialog";
      dialog.setAttribute("role", "dialog");
      dialog.setAttribute("aria-modal", "true");
      const heading = document.createElement("h2");
      heading.textContent = "选择导出内容";
      const body = document.createElement("div");
      body.className = "export-kind-list";

      characterExportOptions.forEach((option) => {
        const disabled = option.requiresVoice && !hasVoice;
        const button = document.createElement("button");
        button.type = "button";
        button.className = "export-kind-option";
        button.disabled = disabled;
        const title = document.createElement("span");
        title.className = "export-kind-title";
        title.textContent = option.label;
        const desc = document.createElement("span");
        desc.className = "export-kind-desc";
        desc.textContent = disabled
          ? `${option.description} 当前角色没有可导出的语音模型。`
          : option.description;
        button.append(title, desc);
        button.addEventListener("click", () => close(option.kind));
        body.append(button);
      });

      const actions = document.createElement("div");
      actions.className = "confirm-actions";
      const cancel = document.createElement("button");
      cancel.type = "button";
      cancel.className = "secondary-button";
      cancel.textContent = "取消";
      actions.append(cancel);
      dialog.append(heading, body, actions);
      overlay.append(dialog);

      function close(kind) {
        document.removeEventListener("keydown", onKey, true);
        overlay.remove();
        closeExportKindDialog = null;
        resolve(kind || "");
      }
      function onKey(event) {
        if (event.key === "Escape") {
          close("");
        }
      }
      overlay.addEventListener("click", (event) => {
        if (event.target === overlay) {
          close("");
        }
      });
      cancel.addEventListener("click", () => close(""));
      document.addEventListener("keydown", onKey, true);
      closeExportKindDialog = () => close("");
      document.body.append(overlay);
      dialog.querySelector("button:not(:disabled)")?.focus();
    });
  }

  async function runCharacterArchiveAction(action) {
    if (!characterView || characterArchiveBusy || disposed) {
      return;
    }
    setError("");
    setCharacterArchiveBusy(true);
    try {
      await action();
    } catch (error) {
      if (!disposed) setError(String(error));
    } finally {
      setCharacterArchiveBusy(false);
    }
  }

  async function importCharacterArchive() {
    await runCharacterArchiveAction(async () => {
      const path = String(await chooseArchivePath("character") || "").trim();
      if (!path) {
        return;
      }
      const previousLifecycle = await invoke("runtime_lifecycle_snapshot");
      const result = await rootSettingsClient.characterImport(path);
      await applyRuntimeCharacterChange(result, previousLifecycle);
      notify("角色包已导入。", "success");
    });
  }

  async function stageRuntimeCharacterSelection() {
    if (characterArchiveBusy) return;
    const characterId = fields.characterSelect.value;
    if (!characterId || characterId === runtimeCharacterDraftId) return;
    const previousCharacterId = runtimeCharacterDraftId
      || runtimeCharacterSnapshot?.currentCharacterId
      || "";
    const committedCharacterId = runtimeCharacterSnapshot?.currentCharacterId || "";
    if (characterId !== committedCharacterId && currentCharacterHasDrafts()) {
      fields.characterSelect.value = previousCharacterId;
      refreshSelect(fields.characterSelect);
      setError("当前角色还有未保存的外观、语音或记忆改动，请先保存或放弃后再切换。");
      return;
    }
    runtimeCharacterDraftId = characterId;
    setError("");
    refreshSelect(fields.characterSelect);
    syncCharacterArchiveState();
    refreshDirty();
    if (pendingRuntimeCharacterId()) {
      notify("已选好角色，点击“应用”或“保存并关闭”即可切换。", "info");
    }
    try {
      await previewRuntimeCharacterVisual(characterId);
    } catch (error) {
      if (!disposed && characterId === runtimeCharacterDraftId) {
        setError(`角色视觉预览失败：${String(error)}`);
      }
    }
  }

  async function importCharacterVoiceArchive() {
    await runCharacterArchiveAction(async () => {
      const character = selectedCharacter();
      if (!character) {
        setError("请先选择一个角色。");
        return;
      }
      if (pendingRuntimeCharacterId() || currentCharacterHasDrafts()) {
        setError("请先保存或放弃角色相关改动，再导入语音包。");
        return;
      }
      const path = String(await chooseArchivePath("voice") || "").trim();
      if (!path) {
        return;
      }
      const previousLifecycle = await invoke("runtime_lifecycle_snapshot");
      const result = await rootSettingsClient.characterVoiceImport(path, character.id);
      await applyRuntimeCharacterChange(result, previousLifecycle);
      notify(`已为角色「${character.display_name}」导入 TTS 模型包。`, "success");
    });
  }

  async function exportCharacterArchive() {
    await runCharacterArchiveAction(async () => {
      const character = selectedCharacter();
      if (!character) {
        setError("当前没有可导出的角色。");
        return;
      }
      if (pendingRuntimeCharacterId()) {
        setError("请先应用或放弃待切换的角色，再导出角色包。");
        return;
      }
      const kind = await chooseExportKind();
      if (!kind) {
        return;
      }
      const path = String(await chooseExportPath(kind) || "").trim();
      if (!path) {
        return;
      }
      const result = await rootSettingsClient.characterExport(path, character.id, kind);
      notify(result.message, "success");
    });
  }

  async function launchCharacterStudio() {
    await runCharacterArchiveAction(async () => {
      const character = selectedCharacter();
      if (!character) {
        setError("请先选择一个角色。");
        return;
      }
      await invoke("open_character_studio", { characterId: character.id });
    });
  }

  function listen(target, event, callback) {
    target.addEventListener(event, callback);
    listeners.push(() => target.removeEventListener(event, callback));
  }

  listen(fields.characterSelect, "change", () => {
    void stageRuntimeCharacterSelection();
  });
  listen(fields.characterSelect, "change", syncCharacterArchiveState);
  listen(fields.characterImportButton, "click", importCharacterArchive);
  listen(fields.ttsVoiceImportButton, "click", importCharacterVoiceArchive);
  listen(fields.characterExportButton, "click", exportCharacterArchive);
  listen(fields.characterEditorButton, "click", launchCharacterStudio);
  listen(window, "focus", () => { if (!characterSwitching && !isSubmitting()) void visualSettings.refresh(); });

  return Object.freeze({
    async initialize() {
      if (disposed) return;
      try {
        applyRuntimeCharacterSnapshot(await rootSettingsClient.charactersGet());
      } catch (error) {
        if (disposed) return;
        applyRuntimeCharacterSnapshot({
          schemaVersion: 1,
          revision: 0,
          currentCharacterId: null,
          characters: [],
        });
        setError(String(error));
      }
    },
    prepareControls() {
      enhanceSelect(fields.characterSelect);
      enhanceSelect(document.getElementById("visualSelect"));
      refreshSelect(fields.characterSelect);
      syncCharacterArchiveState();
    },
    applyAppearancePresentation(presentation, theme, themeDefaults) {
      if (disposed) return;
      const knownCharacters = characterView?.characters || [];
      const currentCharacter = {
        ...(knownCharacters.find((item) => item.id === presentation.characterId) || {}),
        id: presentation.characterId,
        display_name: presentation.displayName,
        theme,
        default_theme: themeDefaults,
      };
      characterView = {
        current_character_id: presentation.characterId,
        characters: knownCharacters.length
          ? knownCharacters.map((item) => item.id === currentCharacter.id ? currentCharacter : item)
          : [currentCharacter],
      };
      renderCharacters();
    },
    selectedThemeDefaults: () => selectedCharacter()?.default_theme,
    currentCharacterId: () => runtimeCharacterSnapshot?.currentCharacterId || "",
    pendingCharacterId: pendingRuntimeCharacterId,
    isDirty: () => Boolean(pendingRuntimeCharacterId()) || visualSettings.isDirty(),
    isSwitching: () => characterSwitching,
    isTransitioning: () => memoryState.rebinding || characterSwitching,
    syncControls: syncCharacterArchiveState,
    refreshCatalog: refreshRuntimeCharacterCatalog,
    onPageChanged(page) { if (page === "character" && !characterSwitching && !isSubmitting()) void visualSettings.refresh(runtimeCharacterDraftId); },
    discard: discardRuntimeCharacterSelection,
    waitForPreview: () => runtimeCharacterVisualPreviewPromise,
    async commit() {
      if (!pendingRuntimeCharacterId() && !visualSettings.isDirty()) return null;
      const previous = await invoke("runtime_lifecycle_snapshot");
      const receipt = await rootSettingsClient.characterSelect(runtimeCharacterDraftId, visualSettings.selections());
      visualSettings.committed();
      await applyRuntimeCharacterChange(receipt, previous);
      await visualSettings.refresh(runtimeCharacterDraftId);
      return receipt;
    },
    dispose() {
      disposed = true;
      visualSettings.dispose();
      characterCatalogRefreshRevision += 1;
      for (const removeListener of listeners) removeListener();
      listeners.length = 0;
      closeExportKindDialog?.();
      runtimeCharacterVisualPreviewRevision += 1;
    },
  });
}
