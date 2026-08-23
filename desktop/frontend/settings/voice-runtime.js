const IDENTIFIER = /^[A-Za-z0-9_.-]{1,200}$/;
const APPLICATION_STATES = new Set(["applied", "restart_required", "error"]);

function exactKeys(value, keys, code) {
  if (!value || typeof value !== "object" || Array.isArray(value)
      || Object.keys(value).length !== keys.length
      || !keys.every((key) => Object.hasOwn(value, key))) {
    throw new Error(code);
  }
  return value;
}

function clone(value) { return JSON.parse(JSON.stringify(value)); }

function boundedJson(value, maximum = 65_536) {
  try { return JSON.stringify(value).length <= maximum; } catch { return false; }
}

function exactProvider(value) {
  exactKeys(value, ["providerId", "label", "available"], "TTS_SETTINGS_RESPONSE_INVALID");
  if (!IDENTIFIER.test(value.providerId) || typeof value.label !== "string" || !value.label
      || value.label.length > 120 || typeof value.available !== "boolean") {
    throw new Error("TTS_SETTINGS_RESPONSE_INVALID");
  }
  return Object.freeze({ ...value });
}

function exactField(value) {
  const keys = ["key", "label", "type", "default", "description", "options", "minimum", "maximum",
    "step", "maxLength", "placement", "actionIds", "enabledWhen", "required", "readonly", "copyable", "restartRequired", "value"];
  exactKeys(value, keys, "TTS_SETTINGS_RESPONSE_INVALID");
  if (!IDENTIFIER.test(value.key) || typeof value.label !== "string" || !value.label
      || !["string", "password", "boolean", "integer", "number", "select", "readonly", "status", "resource"].includes(value.type)
      || !(value.maxLength === null || (["string", "password", "readonly"].includes(value.type)
        && Number.isSafeInteger(value.maxLength) && value.maxLength >= 1 && value.maxLength <= 16_384))
      || !Array.isArray(value.options) || value.options.length > 64
      || !["row", "advanced", "section_header"].includes(value.placement)
      || !Array.isArray(value.actionIds) || value.actionIds.length > 8
      || !(value.enabledWhen === null
        || (value.enabledWhen && typeof value.enabledWhen === "object"
          && !Array.isArray(value.enabledWhen)
          && Object.keys(value.enabledWhen).length === 2
          && IDENTIFIER.test(value.enabledWhen.field)
          && typeof value.enabledWhen.equals === "string"
          && value.enabledWhen.equals.length <= 200
          && value.enabledWhen.field !== value.key))
      || (value.type !== "resource" && value.actionIds.length)
      || (["status", "resource"].includes(value.type) && !value.readonly)
      || !boundedJson(value, 16_384)) {
    throw new Error("TTS_SETTINGS_RESPONSE_INVALID");
  }
  return Object.freeze(clone(value));
}

function exactAction(value) {
  exactKeys(value, ["actionId", "label", "description", "danger"], "TTS_SETTINGS_RESPONSE_INVALID");
  if (!IDENTIFIER.test(value.actionId) || typeof value.label !== "string" || !value.label
      || typeof value.description !== "string" || value.danger !== false) {
    throw new Error("TTS_SETTINGS_RESPONSE_INVALID");
  }
  return Object.freeze({ ...value });
}

function exactSection(value) {
  exactKeys(value, ["pluginId", "sectionId", "title", "reasonCode", "fields", "values", "actions", "collections"],
    "TTS_SETTINGS_RESPONSE_INVALID");
  if (!IDENTIFIER.test(value.pluginId) || !IDENTIFIER.test(value.sectionId)
      || typeof value.title !== "string" || !value.title || typeof value.reasonCode !== "string"
      || !Array.isArray(value.fields) || value.fields.length > 32
      || !value.values || typeof value.values !== "object" || Array.isArray(value.values)
      || !Array.isArray(value.actions) || value.actions.length > 16
      || !Array.isArray(value.collections) || value.collections.length > 4
      || !boundedJson(value.collections, 65_536)) {
    throw new Error("TTS_SETTINGS_RESPONSE_INVALID");
  }
  return Object.freeze({
    ...value,
    fields: Object.freeze(value.fields.map(exactField)),
    values: Object.freeze(clone(value.values)),
    actions: Object.freeze(value.actions.map(exactAction)),
    collections: Object.freeze(clone(value.collections)),
  });
}

function exactSavedSection(value) {
  exactKeys(value, ["pluginId", "sectionId"], "TTS_SETTINGS_CHANGE_PLAN_INVALID");
  if (!IDENTIFIER.test(value.pluginId) || !IDENTIFIER.test(value.sectionId)) {
    throw new Error("TTS_SETTINGS_CHANGE_PLAN_INVALID");
  }
  return Object.freeze({ ...value });
}

function exactVoiceSaveResult(value) {
  exactKeys(value, ["snapshot", "applicationState", "saveState", "savedSections",
    "selectionSaved", "reasonCode"], "TTS_SETTINGS_CHANGE_PLAN_INVALID");
  if (!APPLICATION_STATES.has(value.applicationState)
      || !["complete", "partial"].includes(value.saveState)
      || !Array.isArray(value.savedSections) || value.savedSections.length > 32
      || typeof value.selectionSaved !== "boolean" || !IDENTIFIER.test(value.reasonCode)
      || (value.snapshot !== null
        && (!value.snapshot || typeof value.snapshot !== "object" || Array.isArray(value.snapshot)
          || !boundedJson(value.snapshot)))) {
    throw new Error("TTS_SETTINGS_CHANGE_PLAN_INVALID");
  }
  if ((value.saveState === "complete") !== value.selectionSaved) {
    throw new Error("TTS_SETTINGS_CHANGE_PLAN_INVALID");
  }
  return Object.freeze({
    ...value,
    savedSections: Object.freeze(value.savedSections.map(exactSavedSection)),
  });
}

export function exactVoiceSnapshot(value) {
  exactKeys(value, ["schemaVersion", "character", "selection", "providers", "sections",
    "windowGeneration", "coreGenerationId"], "TTS_SETTINGS_RESPONSE_INVALID");
  if (value.schemaVersion !== 2 || !Number.isSafeInteger(value.windowGeneration)
      || value.windowGeneration < 1 || typeof value.coreGenerationId !== "string" || !value.coreGenerationId) {
    throw new Error("TTS_SETTINGS_RESPONSE_INVALID");
  }
  exactKeys(value.character, ["characterId", "displayName"], "TTS_SETTINGS_RESPONSE_INVALID");
  exactKeys(value.selection, ["configured", "enabled", "providerId", "available"],
    "TTS_SETTINGS_RESPONSE_INVALID");
  if (!IDENTIFIER.test(value.character.characterId) || typeof value.character.displayName !== "string"
      || typeof value.selection.configured !== "boolean" || typeof value.selection.enabled !== "boolean"
      || (value.selection.providerId !== null && !IDENTIFIER.test(value.selection.providerId))
      || typeof value.selection.available !== "boolean" || !Array.isArray(value.providers)
      || value.providers.length > 64 || !Array.isArray(value.sections) || value.sections.length > 32) {
    throw new Error("TTS_SETTINGS_RESPONSE_INVALID");
  }
  return Object.freeze({
    ...value,
    character: Object.freeze({ ...value.character }),
    selection: Object.freeze({ ...value.selection }),
    providers: Object.freeze(value.providers.map(exactProvider)),
    sections: Object.freeze(value.sections.map(exactSection)),
  });
}

function draftSignature(draft) { return JSON.stringify(draft); }

function fieldValue(field, input) {
  if (field.type === "boolean") return Boolean(input.checked);
  if (field.type === "integer") return Number.parseInt(input.value, 10);
  if (field.type === "number") return Number(input.value);
  return input.value;
}

function setInputValue(field, input) {
  if (field.type === "boolean") input.checked = Boolean(field.value);
  else input.value = field.value === null || field.value === undefined ? "" : String(field.value);
}

export function createVoiceController({
  document,
  invoke,
  isAvailable = () => true,
  refreshAvailability = async () => {},
  openPlugins = () => {},
  enhanceSelect = () => {},
  refreshSelect = () => {},
  onDirty = () => {},
  onStatus = () => {},
}) {
  const fields = {
    page: document.getElementById("page-voice"),
    settings: document.getElementById("voiceSettings"),
    unavailable: document.getElementById("voiceUnavailable"),
    enabled: document.getElementById("ttsEnabled"),
    provider: document.getElementById("ttsProvider"),
    sections: document.getElementById("ttsProviderSettings"),
  };
  let snapshot = null;
  let baseline = "";
  let disposed = false;
  const sectionInputs = new Map();

  enhanceSelect(fields.provider);

  function sectionKey(pluginId, sectionId) { return `${pluginId}\u0000${sectionId}`; }

  function currentDraft() {
    if (!snapshot) return null;
    const sections = snapshot.sections.map((section) => {
      const inputs = sectionInputs.get(sectionKey(section.pluginId, section.sectionId)) || new Map();
      return {
        pluginId: section.pluginId,
        sectionId: section.sectionId,
        values: Object.fromEntries(section.fields
          .filter((field) => !field.readonly
            && !["readonly", "status", "resource"].includes(field.type))
          .map((field) => [field.key, fieldValue(field, inputs.get(field.key))])),
      };
    });
    return {
      characterId: snapshot.character.characterId,
      enabled: Boolean(fields.enabled.checked),
      providerId: fields.provider.value || null,
      sections,
    };
  }

  function changedSections(draft) {
    return draft.sections.filter((sectionDraft) => {
      const section = snapshot.sections.find((item) => item.pluginId === sectionDraft.pluginId
        && item.sectionId === sectionDraft.sectionId);
      return section && JSON.stringify(sectionDraft.values) !== JSON.stringify(section.values);
    });
  }

  function markDirty() { onDirty(); }

  function syncSectionVisibility() {
    for (const group of fields.sections.children || []) {
      group.hidden = group.voiceProviderId !== fields.provider.value;
    }
  }

  function renderSections() {
    sectionInputs.clear();
    fields.sections.textContent = "";
    for (const section of snapshot.sections) {
      const group = document.createElement("fieldset");
      group.className = "settings-group plugin-voice-section";
      group.voiceProviderId = section.pluginId;
      group.hidden = section.pluginId !== fields.provider.value;
      const legend = document.createElement("legend");
      legend.textContent = section.title;
      group.append(legend);
      const inputs = new Map();
      const advanced = document.createElement("details");
      advanced.className = "voice-advanced-settings";
      const advancedSummary = document.createElement("summary");
      advancedSummary.textContent = "高级设置";
      const advancedBody = document.createElement("div");
      advancedBody.className = "voice-advanced-settings__body";
      advanced.append(advancedSummary, advancedBody);
      let advancedFieldCount = 0;
      const conditionalFields = [];
      let syncFieldAvailability = () => {};
      for (const field of section.fields) {
        const row = document.createElement("div");
        row.className = "setting-row";
        const label = document.createElement("label");
        label.className = "setting-row-text";
        const title = document.createElement("span");
        title.className = "setting-title";
        title.textContent = field.label;
        label.append(title);
        if (field.description) {
          const description = document.createElement("span");
          description.className = "setting-desc";
          description.textContent = field.description;
          label.append(description);
        }
        let input;
        if (field.readonly || ["readonly", "status", "resource"].includes(field.type)) {
          input = document.createElement("output");
          input.className = "plugin-readonly-output";
          if (field.type === "status") {
            input.textContent = [field.value?.label, field.value?.message].filter(Boolean).join(" · ");
          } else if (field.type === "resource") {
            input.textContent = [field.value?.subtitle, field.value?.message].filter(Boolean).join(" · ");
          } else {
            input.textContent = field.value === null || field.value === undefined ? "" : String(field.value);
          }
        } else if (field.type === "select") {
          input = document.createElement("select");
          for (const item of field.options) {
            const option = document.createElement("option");
            option.value = String(item.value);
            option.textContent = item.label;
            input.append(option);
          }
        } else {
          input = document.createElement("input");
          input.type = field.type === "boolean" ? "checkbox"
            : field.type === "password" ? "password"
              : ["integer", "number"].includes(field.type) ? "number" : "text";
          if (field.minimum !== null) input.min = String(field.minimum);
          if (field.maximum !== null) input.max = String(field.maximum);
          if (field.step !== null) input.step = String(field.step);
        }
        if (!field.readonly && !["readonly", "status", "resource"].includes(field.type)) {
          setInputValue(field, input);
          const handleInput = () => { syncFieldAvailability(); markDirty(); };
          input.addEventListener("input", handleInput);
          input.addEventListener("change", handleInput);
        }
        inputs.set(field.key, input);
        if (field.enabledWhen) conditionalFields.push({ field, input, row });
        row.append(label, input);
        if (field.type === "select" && !field.readonly) enhanceSelect(input);
        if (field.placement === "advanced") {
          advancedBody.append(row);
          advancedFieldCount += 1;
        } else {
          group.append(row);
        }
      }
      syncFieldAvailability = () => {
        for (const { field, input, row } of conditionalFields) {
          const controller = inputs.get(field.enabledWhen.field);
          const enabled = Boolean(controller) && String(controller.value) === field.enabledWhen.equals;
          input.disabled = !enabled;
          row.className = `setting-row${enabled ? "" : " is-disabled"}`;
          refreshSelect(input);
        }
      };
      syncFieldAvailability();
      if (advancedFieldCount) group.append(advanced);
      for (const action of section.actions) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "secondary-button";
        button.textContent = action.label;
        button.addEventListener("click", async () => {
          try {
            const values = currentDraft().sections.find((item) => item.pluginId === section.pluginId
              && item.sectionId === section.sectionId)?.values || {};
            const result = await invoke("settings_plugins_action", {
              windowGeneration: snapshot.windowGeneration,
              coreGenerationId: snapshot.coreGenerationId,
              pluginId: section.pluginId,
              sectionId: section.sectionId,
              actionId: action.actionId,
              values,
            });
            onStatus(result?.message || "插件已重新加载。", "success");
            await refresh();
          } catch (error) { onStatus(String(error), "error"); }
        });
        group.append(button);
      }
      sectionInputs.set(sectionKey(section.pluginId, section.sectionId), inputs);
      fields.sections.append(group);
    }
  }

  function showSettings() {
    fields.page.dataset.voiceState = "available";
    fields.settings.hidden = false;
    fields.unavailable.hidden = true;
    fields.unavailable.textContent = "";
  }

  function showUnavailable() {
    fields.page.dataset.voiceState = "unavailable";
    fields.settings.hidden = true;
    fields.unavailable.hidden = false;
    fields.unavailable.textContent = "";

    const empty = document.createElement("div");
    empty.className = "memory-surface-state memory-surface-unavailable";
    const mark = document.createElement("span");
    mark.className = "memory-empty-mark";
    mark.textContent = "✦";
    const heading = document.createElement("strong");
    heading.textContent = "语音管理暂不可用";
    const message = document.createElement("p");
    message.textContent = "请确认语音插件已安装并启用。";
    const actions = document.createElement("div");
    const refresh = document.createElement("button");
    refresh.type = "button";
    refresh.className = "secondary-button";
    refresh.textContent = "重新检查";
    refresh.addEventListener("click", async () => {
      refresh.disabled = true;
      try {
        await refreshAvailability();
        await refreshCurrent();
      } finally {
        if (!disposed && fields.unavailable.hidden === false) refresh.disabled = false;
      }
    });
    const link = document.createElement("button");
    link.type = "button";
    link.className = "secondary-button";
    link.textContent = "前往插件页";
    link.addEventListener("click", openPlugins);
    actions.append(refresh, link);
    empty.append(mark, heading, message, actions);
    fields.unavailable.append(empty);
  }

  function renderUnavailable() {
    snapshot = null;
    baseline = "";
    sectionInputs.clear();
    fields.enabled.checked = false;
    fields.enabled.disabled = true;
    fields.provider.textContent = "";
    fields.provider.disabled = true;
    fields.sections.textContent = "";
    refreshSelect(fields.provider);
    showUnavailable();
    onDirty();
  }

  function initialize(value) {
    const next = exactVoiceSnapshot(value);
    if (!next.providers.length) {
      renderUnavailable();
      return;
    }
    snapshot = next;
    showSettings();
    fields.enabled.checked = snapshot.selection.enabled;
    fields.enabled.disabled = false;
    fields.provider.textContent = "";
    fields.provider.disabled = false;
    for (const provider of snapshot.providers) {
      const option = document.createElement("option");
      option.value = provider.providerId;
      option.textContent = `${provider.label}${provider.available ? "" : "（不可用）"}`;
      fields.provider.append(option);
    }
    if (snapshot.selection.providerId
        && !snapshot.providers.some((item) => item.providerId === snapshot.selection.providerId)) {
      const option = document.createElement("option");
      option.value = snapshot.selection.providerId;
      option.textContent = `${snapshot.selection.providerId}（未加载）`;
      fields.provider.append(option);
    }
    fields.provider.value = snapshot.selection.providerId || snapshot.providers[0]?.providerId || "";
    refreshSelect(fields.provider);
    renderSections();
    baseline = draftSignature(currentDraft());
    onDirty();
  }

  async function refresh() {
    if (disposed) return null;
    initialize(await invoke("settings_voice_get"));
    return snapshot;
  }

  async function refreshCurrent() {
    if (!isAvailable()) {
      if (!disposed) renderUnavailable();
      return null;
    }
    try {
      return await refresh();
    } catch {
      if (!disposed) renderUnavailable();
      return null;
    }
  }

  fields.enabled.addEventListener("input", markDirty);
  fields.enabled.addEventListener("change", markDirty);
  const handleProviderChange = () => {
    syncSectionVisibility();
    markDirty();
  };
  fields.provider.addEventListener("input", handleProviderChange);
  fields.provider.addEventListener("change", handleProviderChange);

  return Object.freeze({
    initialize,
    refreshStatus: refresh,
    refreshCurrent,
    isDirty: () => Boolean(snapshot) && draftSignature(currentDraft()) !== baseline,
    async save() {
      if (!snapshot || disposed) throw new Error("TTS_SETTINGS_NOT_READY");
      const draft = currentDraft();
      draft.sections = changedSections(draft);
      const result = exactVoiceSaveResult(await invoke("settings_voice_save", {
        windowGeneration: snapshot.windowGeneration,
        coreGenerationId: snapshot.coreGenerationId,
        draft,
      }));
      let refreshFailed = false;
      try { await refresh(); } catch { refreshFailed = true; }
      if (result.saveState === "partial") {
        const providerSaveFailed = result.reasonCode === "TTS_PROVIDER_SETTINGS_SAVE_FAILED";
        const savedWhat = providerSaveFailed
          ? "部分语音引擎配置已保存，但后续引擎配置和角色语音选择未保存"
          : "语音引擎配置已保存，但角色语音选择未保存";
        const message = refreshFailed
          ? `${savedWhat}，且当前状态刷新失败。请重新打开设置后确认。`
          : `${savedWhat}。页面已刷新为实际状态，请确认后重试。`;
        onStatus(message, "error");
        throw new Error(message);
      }
      if (refreshFailed) throw new Error("TTS_SETTINGS_REFRESH_FAILED");
      if (result.applicationState === "restart_required") {
        onStatus("配置已保存；请在对应语音引擎区块重新加载插件。", "info");
      } else if (result.applicationState === "error") {
        onStatus("配置已保存，但语音引擎配置应用失败。", "error");
      }
      return result;
    },
    dispose() {
      disposed = true;
      snapshot = null;
      sectionInputs.clear();
    },
  });
}
