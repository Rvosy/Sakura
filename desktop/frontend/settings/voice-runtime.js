import { createIcon } from "../core/icons.js";

function clone(value) { return structuredClone(value); }

function exactVoiceSaveResult(value) { return Object.freeze(clone(value)); }

export function exactVoiceSnapshot(value) { return Object.freeze(clone(value)); }

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
  onSectionsRendered = () => {},
}) {
  const fields = {
    page: document.getElementById("page-voice"),
    settings: document.getElementById("voiceSettings"),
    unavailable: document.getElementById("voiceUnavailable"),
    enabled: document.getElementById("ttsEnabled"),
    provider: document.getElementById("ttsProvider"),
    sections: document.getElementById("ttsProviderSettings"),
  };
  const characterNotice = document.createElement("p");
  characterNotice.className = "page-note";
  characterNotice.textContent = "选择角色后可启用语音输出。";
  characterNotice.hidden = true;
  fields.settings.append(characterNotice);
  let snapshot = null;
  let baseline = "";
  let disposed = false;
  const sectionInputs = new Map();
  const sectionAvailability = new Map();
  let sectionHost = null;

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
      characterId: snapshot.character?.characterId || null,
      enabled: snapshot.character ? Boolean(fields.enabled.checked) : false,
      providerId: snapshot.character ? (fields.provider.value || null) : null,
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

  function renderSections(drafts = []) {
    sectionInputs.clear();
    sectionAvailability.clear();
    fields.sections.textContent = "";
    if (sectionHost) sectionHost.container.textContent = "";
    for (const section of snapshot.sections) {
      const draftValues = drafts.find((item) => item.pluginId === section.pluginId
        && item.sectionId === section.sectionId)?.values || {};
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
          input.id = `voice-field-${section.pluginId}-${section.sectionId}-${field.key}`;
          label.htmlFor = input.id;
          input.setAttribute("aria-label", field.label);
          input.required = Boolean(field.required);
          setInputValue(Object.hasOwn(draftValues, field.key)
            ? { ...field, value: draftValues[field.key] } : field, input);
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
          row.hidden = field.enabledWhen.hide === true && !enabled;
          input.disabled = !enabled;
          row.className = `setting-row${enabled ? "" : " is-disabled"}`;
          refreshSelect(input);
        }
      };
      syncFieldAvailability();
      sectionAvailability.set(sectionKey(section.pluginId, section.sectionId), syncFieldAvailability);
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
      if (sectionHost?.pluginId === section.pluginId) {
        group.hidden = false;
        sectionHost.container.append(group);
      } else fields.sections.append(group);
    }
  }

  function pluginDraft(pluginId) {
    return {
      coreGenerationId: snapshot?.coreGenerationId,
      characterId: snapshot?.character?.characterId || null,
      sections: currentDraft()?.sections.filter((section) => section.pluginId === pluginId) || [],
    };
  }

  function restorePluginDraft(draft) {
    if (!snapshot || snapshot.coreGenerationId !== draft?.coreGenerationId
        || (snapshot.character?.characterId || null) !== draft.characterId) return;
    for (const section of draft.sections) {
      const descriptor = snapshot.sections.find((item) => item.pluginId === section.pluginId && item.sectionId === section.sectionId);
      const inputs = sectionInputs.get(sectionKey(section.pluginId, section.sectionId));
      if (!descriptor || !inputs) continue;
      for (const field of descriptor.fields) {
        if (!Object.hasOwn(section.values, field.key)) continue;
        setInputValue({ ...field, value: section.values[field.key] }, inputs.get(field.key));
        refreshSelect(inputs.get(field.key));
      }
      sectionAvailability.get(sectionKey(section.pluginId, section.sectionId))?.();
    }
    markDirty();
  }

  function unmountPluginSections() {
    if (!sectionHost) return;
    for (const group of Array.from(sectionHost.container.children)) fields.sections.append(group);
    sectionHost = null;
    syncSectionVisibility();
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
    characterNotice.hidden = true;

    const empty = document.createElement("div");
    empty.className = "memory-surface-state memory-surface-unavailable";
    const mark = document.createElement("span");
    mark.className = "memory-empty-mark";
    mark.append(createIcon(document, "audio-lines"));
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
    sectionAvailability.clear();
    if (sectionHost) sectionHost.container.textContent = "";
    fields.enabled.checked = false;
    fields.enabled.disabled = true;
    fields.provider.textContent = "";
    fields.provider.disabled = true;
    fields.sections.textContent = "";
    refreshSelect(fields.provider);
    showUnavailable();
    onDirty();
    onSectionsRendered();
  }

  function initialize(value, { preserveDraft = false } = {}) {
    const next = exactVoiceSnapshot(value);
    const previousDraft = preserveDraft ? currentDraft() : null;
    const previousBaseline = baseline ? JSON.parse(baseline) : null;
    if (!next.providers.length) {
      if (previousDraft && draftSignature(previousDraft) !== baseline) {
        throw new Error("语音引擎暂不可用，请稍后重试。");
      }
      renderUnavailable();
      return;
    }
    snapshot = next;
    showSettings();
    const hasCharacter = snapshot.character !== null;
    characterNotice.hidden = hasCharacter;
    fields.enabled.checked = hasCharacter ? snapshot.selection.enabled : false;
    fields.enabled.disabled = !hasCharacter;
    fields.provider.textContent = "";
    fields.provider.disabled = false;
    for (const provider of snapshot.providers) {
      const option = document.createElement("option");
      option.value = provider.providerId;
      option.textContent = `${provider.label}${provider.available ? "" : "（不可用）"}`;
      fields.provider.append(option);
    }
    if (snapshot.selection?.providerId
        && !snapshot.providers.some((item) => item.providerId === snapshot.selection.providerId)) {
      const option = document.createElement("option");
      option.value = snapshot.selection.providerId;
      option.textContent = `${snapshot.selection.providerId}（未加载）`;
      fields.provider.append(option);
    }
    fields.provider.value = snapshot.selection?.providerId || snapshot.providers[0]?.providerId || "";
    refreshSelect(fields.provider);
    renderSections();
    baseline = draftSignature(currentDraft());
    if (previousDraft && previousBaseline
        && previousDraft.characterId === (snapshot.character?.characterId || null)) {
      if (previousDraft.enabled !== previousBaseline.enabled) fields.enabled.checked = previousDraft.enabled;
      if (previousDraft.providerId !== previousBaseline.providerId && previousDraft.providerId) {
        if (!Array.from(fields.provider.children).some((item) => item.value === previousDraft.providerId)) {
          const option = document.createElement("option");
          option.value = previousDraft.providerId;
          option.textContent = `${previousDraft.providerId}（未加载）`;
          fields.provider.append(option);
        }
        fields.provider.value = previousDraft.providerId;
      }
      const edits = previousDraft.sections.map((section) => {
        const oldValues = previousBaseline.sections.find((item) => item.pluginId === section.pluginId
          && item.sectionId === section.sectionId)?.values || {};
        return { ...section, values: Object.fromEntries(Object.entries(section.values)
          .filter(([key, value]) => value !== oldValues[key])) };
      });
      renderSections(edits);
      refreshSelect(fields.provider);
    }
    onDirty();
    onSectionsRendered();
  }

  async function refresh(options = {}) {
    if (disposed) return null;
    const next = await invoke("settings_voice_get");
    if (!disposed) initialize(next, options);
    return snapshot;
  }

  async function refreshCurrent({ preserveDraft = false } = {}) {
    if (!isAvailable()) {
      if (preserveDraft && snapshot && draftSignature(currentDraft()) !== baseline) {
        throw new Error("语音设置暂不可用，请稍后重试。");
      }
      if (!disposed) renderUnavailable();
      return null;
    }
    try {
      return await refresh({ preserveDraft });
    } catch (error) {
      if (preserveDraft && snapshot) throw error;
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
    hasPluginSections: (pluginId) => Boolean(snapshot?.sections.some((section) => section.pluginId === pluginId)),
    pluginDraft,
    restorePluginDraft,
    mountPluginSections(pluginId, container) {
      unmountPluginSections();
      sectionHost = { pluginId, container };
      for (const group of Array.from(fields.sections.children)) {
        if (group.voiceProviderId !== pluginId) continue;
        group.hidden = false;
        container.append(group);
      }
    },
    unmountPluginSections,
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
        onStatus("已保存，请重新加载语音插件。", "info");
      } else if (result.applicationState === "error") {
        onStatus("配置已保存，但语音引擎配置应用失败。", "error");
      }
      return result;
    },
    dispose() {
      unmountPluginSections();
      disposed = true;
      snapshot = null;
      sectionInputs.clear();
      sectionAvailability.clear();
    },
  });
}
