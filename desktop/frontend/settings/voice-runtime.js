import { createIcon } from "../core/icons.js";
import { errorText } from "../core/error-display.js";

function clone(value) { return structuredClone(value); }

function exactVoiceSaveResult(value) { return Object.freeze(clone(value)); }

export function exactVoiceSnapshot(value) { return Object.freeze(clone(value)); }

function draftSignature(draft) { return JSON.stringify(draft); }

function hubAvailability(plugins) {
  const hubs = plugins.filter((plugin) => plugin.provides.includes("sakura.tts"));
  const hub = hubs.find((plugin) => plugin.enabled && plugin.state === "active")
    || hubs.find((plugin) => plugin.enabled) || hubs[0];
  if (!hub) return { state: "missing" };
  if (!hub.enabled) return { state: "disabled" };
  return { state: hub.state, reasonCode: hub.reasonCode, diagnostics: hub.diagnostics };
}

export function createVoiceController({
  document,
  invoke,
  getPlugins = () => [],
  refreshAvailability = async () => {},
  openPlugins = () => {},
  openPlugin = () => {},
  enhanceSelect = () => {},
  refreshSelect = () => {},
  onDirty = () => {},
  onStatus = () => {},
  reportError = () => {},
}) {
  const fields = {
    page: document.getElementById("page-voice"),
    settings: document.getElementById("voiceSettings"),
    unavailable: document.getElementById("voiceUnavailable"),
    enabled: document.getElementById("ttsEnabled"),
    provider: document.getElementById("ttsProvider"),
    pluginSettings: document.getElementById("ttsPluginSettings"),
  };
  const characterNotice = document.createElement("p");
  characterNotice.className = "page-note";
  characterNotice.textContent = "选择角色后可开启语音。";
  characterNotice.hidden = true;
  fields.settings.append(characterNotice);
  const failureNotice = document.createElement("p");
  failureNotice.className = "error";
  failureNotice.hidden = true;
  fields.settings.append(failureNotice);
  let snapshot = null;
  let baseline = "";
  let disposed = false;
  enhanceSelect(fields.provider);

  function renderFailure() {
    const selected = snapshot?.providers.find(item => item.providerId === fields.provider.value);
    if (fields.pluginSettings) fields.pluginSettings.disabled = !selected;
    const failure = selected?.diagnostics ? selected
      : fields.provider.value === snapshot?.selection?.providerId ? snapshot.selection : null;
    failureNotice.textContent = failure?.diagnostics ? errorText({ ...failure, code: failure.reasonCode }) : "";
    failureNotice.hidden = !failureNotice.textContent;
  }

  function currentDraft() {
    if (!snapshot) return null;
    return {
      characterId: snapshot.character?.characterId || null,
      enabled: snapshot.character ? Boolean(fields.enabled.checked) : false,
      providerId: snapshot.character ? (fields.provider.value || null) : null,
    };
  }

  function markDirty() { onDirty(); }

  function showSettings() {
    fields.page.dataset.voiceState = "available";
    fields.settings.hidden = false;
    fields.unavailable.hidden = true;
    fields.unavailable.textContent = "";
  }

  function showUnavailable(availability) {
    fields.page.dataset.voiceState = availability.state;
    fields.settings.hidden = false;
    fields.unavailable.hidden = false;
    fields.unavailable.textContent = "";
    characterNotice.hidden = true;

    const empty = document.createElement("div");
    empty.className = "memory-surface-state memory-surface-unavailable";
    const mark = document.createElement("span");
    mark.className = "memory-empty-mark";
    mark.append(createIcon(document, "audio-lines"));
    const heading = document.createElement("strong");
    heading.textContent = availability.state === "error" ? "语音设置读取失败" : "语音管理暂不可用";
    const message = document.createElement("p");
    message.textContent = {
      missing: "请先安装语音插件。",
      disabled: "语音插件已停用。",
      starting: "语音插件正在启动。",
      failed: "语音插件启动失败，请到插件页查看原因。",
      unavailable: "当前没有已启用的语音引擎。",
      error: "请重新检查；若仍然失败，可在运行日志中查看原因。",
    }[availability.state] || "语音插件暂不可用，请到插件页查看状态。";
    if (availability.diagnostics || availability.error) message.textContent = errorText(availability.error || { ...availability, code: availability.reasonCode });
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

  function renderUnavailable(availability = { state: "unavailable" }) {
    snapshot = null;
    baseline = "";
    characterNotice.hidden = true;
    failureNotice.hidden = true;
    failureNotice.textContent = "";
    fields.enabled.checked = false;
    fields.enabled.disabled = true;
    fields.provider.textContent = "";
    fields.provider.disabled = true;
    const option = document.createElement("option");
    option.value = "";
    option.textContent = { missing: "未安装语音插件", unavailable: "未安装语音插件",
      disabled: "语音插件已停用", starting: "语音插件正在启动", failed: "语音插件启动失败",
      error: "语音设置读取失败" }[availability.state] || "语音插件暂不可用";
    fields.provider.append(option);
    fields.provider.value = "";
    if (fields.pluginSettings) fields.pluginSettings.disabled = true;
    refreshSelect(fields.provider);
    if (["missing", "unavailable"].includes(availability.state) && !availability.diagnostics) {
      showSettings();
      fields.page.dataset.voiceState = availability.state;
    } else showUnavailable(availability);
    onDirty();
  }

  function initialize(value, { preserveDraft = false } = {}) {
    const next = exactVoiceSnapshot(value);
    const previousDraft = preserveDraft ? currentDraft() : null;
    const previousBaseline = baseline ? JSON.parse(baseline) : null;
    if ((next.availability && next.availability.state !== "active") || !next.providers.length) {
      if (previousDraft && draftSignature(previousDraft) !== baseline) {
        throw new Error("语音引擎暂不可用，请稍后重试。");
      }
      const absentSelection = next.selection?.stage === "provider_selection"
        && next.selection.reasonCode === "TTS_PROVIDER_UNAVAILABLE"
        && !getPlugins().some(plugin => plugin.pluginId === next.selection.providerId && plugin.enabled);
      renderUnavailable(next.availability && next.availability.state !== "active" ? next.availability
        : { state: "unavailable", diagnostics: absentSelection ? null : next.selection?.diagnostics, reasonCode: next.selection?.reasonCode });
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
      refreshSelect(fields.provider);
    }
    renderFailure();
    onDirty();
  }

  async function refresh(options = {}) {
    if (disposed) return null;
    const next = await invoke("settings_voice_get");
    if (!disposed) {
      try { initialize(next, options); }
      catch (error) {
        reportError(error, { command: "settings_voice_get", stage: "voice.render" });
        throw error;
      }
    }
    return snapshot;
  }

  async function refreshCurrent({ preserveDraft = false } = {}) {
    const availability = hubAvailability(getPlugins());
    if (availability.state !== "active") {
      if (preserveDraft && snapshot && draftSignature(currentDraft()) !== baseline) {
        throw new Error("语音设置暂不可用，请稍后重试。");
      }
      if (!disposed) renderUnavailable(availability);
      return null;
    }
    try {
      return await refresh({ preserveDraft });
    } catch (error) {
      if (preserveDraft && snapshot) throw error;
      if (!disposed) {
        renderUnavailable({ state: "error", error });
        onStatus(error, "error");
      }
      return null;
    }
  }

  fields.pluginSettings?.addEventListener("click", () => openPlugin(fields.provider.value));
  fields.enabled.addEventListener("input", markDirty);
  fields.enabled.addEventListener("change", markDirty);
  const handleProviderChange = () => {
    renderFailure();
    markDirty();
  };
  fields.provider.addEventListener("input", handleProviderChange);
  fields.provider.addEventListener("change", handleProviderChange);

  return Object.freeze({
    initialize,
    refreshStatus: refresh,
    refreshCurrent,
    async onPageChanged(page) {
      if (page !== "voice" || disposed) return;
      try {
        await refreshAvailability();
        if (!disposed) await refreshCurrent({ preserveDraft: true });
      } catch (error) {
        if (!disposed) onStatus(error, "error");
      }
    },
    isDirty: () => Boolean(snapshot) && draftSignature(currentDraft()) !== baseline,
    async save() {
      if (!snapshot || disposed) throw new Error("TTS_SETTINGS_NOT_READY");
      const draft = currentDraft();
      const result = exactVoiceSaveResult(await invoke("settings_voice_save", {
        windowGeneration: snapshot.windowGeneration,
        coreGenerationId: snapshot.coreGenerationId,
        draft,
      }));
      let refreshError = null;
      try { await refresh(); } catch (error) { refreshError = error; }
      if (refreshError) throw new Error("TTS_SETTINGS_REFRESH_FAILED", { cause: refreshError });
      return result;
    },
    dispose() {
      disposed = true;
      snapshot = null;
    },
  });
}
