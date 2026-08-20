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
    "step", "maxLength", "required", "readonly", "copyable", "restartRequired", "value"];
  exactKeys(value, keys, "TTS_SETTINGS_RESPONSE_INVALID");
  if (!IDENTIFIER.test(value.key) || typeof value.label !== "string" || !value.label
      || !["string", "password", "boolean", "integer", "number", "select", "readonly"].includes(value.type)
      || !(value.maxLength === null || (["string", "password", "readonly"].includes(value.type)
        && Number.isSafeInteger(value.maxLength) && value.maxLength >= 1 && value.maxLength <= 16_384))
      || !Array.isArray(value.options) || value.options.length > 64 || !boundedJson(value, 16_384)) {
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

export function createVoiceController({ document, invoke, onDirty = () => {}, onStatus = () => {} }) {
  const fields = {
    character: document.getElementById("ttsCharacterLabel"),
    enabled: document.getElementById("ttsEnabled"),
    provider: document.getElementById("ttsProvider"),
    sections: document.getElementById("ttsProviderSettings"),
    status: document.getElementById("ttsResourceCard"),
  };
  let snapshot = null;
  let baseline = "";
  let disposed = false;
  const sectionInputs = new Map();

  function sectionKey(pluginId, sectionId) { return `${pluginId}\u0000${sectionId}`; }

  function currentDraft() {
    if (!snapshot) return null;
    const sections = snapshot.sections.map((section) => {
      const inputs = sectionInputs.get(sectionKey(section.pluginId, section.sectionId)) || new Map();
      return {
        pluginId: section.pluginId,
        sectionId: section.sectionId,
        values: Object.fromEntries(section.fields
          .filter((field) => !field.readonly && field.type !== "readonly")
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

  function markDirty() { onDirty(); renderStatus(); }

  function renderStatus() {
    if (!snapshot || !fields.status) return;
    const selected = snapshot.providers.find((item) => item.providerId === fields.provider.value);
    fields.status.textContent = fields.enabled.checked
      ? `${selected?.label || fields.provider.value || "未选择 Provider"} · ${selected?.available ? "可用" : "当前不可用"}`
      : "当前角色已关闭语音；Provider 选择和配置会保留。";
  }

  function renderSections() {
    sectionInputs.clear();
    fields.sections.textContent = "";
    for (const section of snapshot.sections) {
      const group = document.createElement("fieldset");
      group.className = "settings-group plugin-voice-section";
      const legend = document.createElement("legend");
      legend.textContent = section.title;
      group.append(legend);
      const inputs = new Map();
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
        if (field.type === "select") {
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
        input.disabled = Boolean(field.readonly || field.type === "readonly");
        setInputValue(field, input);
        input.addEventListener("input", markDirty);
        input.addEventListener("change", markDirty);
        inputs.set(field.key, input);
        row.append(label, input);
        group.append(row);
      }
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

  function initialize(value) {
    snapshot = exactVoiceSnapshot(value);
    fields.character.textContent = `正在配置角色：${snapshot.character.displayName}`;
    fields.enabled.checked = snapshot.selection.enabled;
    fields.provider.textContent = "";
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
    renderSections();
    baseline = draftSignature(currentDraft());
    renderStatus();
    onDirty();
  }

  async function refresh() {
    if (disposed) return null;
    initialize(await invoke("settings_voice_get"));
    return snapshot;
  }

  fields.enabled.addEventListener("input", markDirty);
  fields.enabled.addEventListener("change", markDirty);
  fields.provider.addEventListener("input", markDirty);
  fields.provider.addEventListener("change", markDirty);

  return Object.freeze({
    initialize,
    refreshStatus: refresh,
    refreshCurrent: refresh,
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
          ? "部分 Provider 配置已保存，但后续 Provider 配置和角色语音选择未保存"
          : "Provider 配置已保存，但角色语音选择未保存";
        const message = refreshFailed
          ? `${savedWhat}，且当前状态刷新失败。请重新打开设置后确认。`
          : `${savedWhat}。页面已刷新为实际状态，请确认后重试。`;
        onStatus(message, "error");
        throw new Error(message);
      }
      if (refreshFailed) throw new Error("TTS_SETTINGS_REFRESH_FAILED");
      if (result.applicationState === "restart_required") {
        onStatus("配置已保存；请在对应 Provider 区块重新加载插件。", "info");
      } else if (result.applicationState === "error") {
        onStatus("配置已保存，但 Provider 应用失败。", "error");
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
