import { errorText } from "../core/error-display.js";

export function createAsrSettingsController({ document, invoke, enhanceSelect = () => {},
  refreshSelect = () => {}, onDirty = () => {}, onStatus = () => {},
  listen = null, openPlugin = () => {}, getPlugins = () => [] }) {
  const provider = document.getElementById("asrProvider");
  const status = document.getElementById("asrStatus");
  const location = document.getElementById("asrLocation");
  const pluginSettings = document.getElementById("asrPluginSettings");
  let snapshot = null;
  let baseline = "";
  let disposed = false;
  let revision = 0;
  const draft = () => ({ selectedProviderId: provider.value || null });
  enhanceSelect(provider);

  function renderStatus() {
    const selected = snapshot?.providers.find((item) => item.providerId === provider.value);
    if (pluginSettings) pluginSettings.disabled = !selected;
    const failure = selected?.diagnostics ? selected
      : provider.value === (snapshot?.selectedProviderId || "") ? snapshot : null;
    const absentSelection = !snapshot?.providers.length && !selected
      && failure?.stage === "provider_selection" && failure.errorCode === "ASR_PROVIDER_UNAVAILABLE"
      && !getPlugins().some(plugin => plugin.pluginId === snapshot.selectedProviderId && plugin.enabled);
    status.textContent = failure?.diagnostics && !absentSelection ? errorText({ ...failure, code: failure.errorCode || failure.reasonCode }) : "";
    status.hidden = !status.textContent;
    location.textContent = selected?.processingLocation === "remote"
      ? "录音将发送至该引擎配置的远端服务。" : "";
  }
  async function refresh({ preserveDraft = false } = {}) {
    const request = ++revision;
    try {
      const value = await invoke("settings_asr_get");
      if (disposed || request !== revision) return;
      if (!value || !Array.isArray(value.providers)) throw new Error("ASR_SETTINGS_INVALID");
      const savedDraft = preserveDraft && baseline && JSON.stringify(draft()) !== baseline ? draft() : null;
      snapshot = value;
      provider.replaceChildren();
      if (!value.selectedProviderId || !value.providers.length) {
        const none = document.createElement("option");
        none.value = ""; none.disabled = true;
        none.textContent = value.providers.length ? "选择引擎" : "未安装语音输入插件";
        provider.append(none);
      }
      for (const item of value.providers) {
        const option = document.createElement("option");
        option.value = item.providerId;
        const state = { missing_resources: "缺少模型", unavailable: "不可用", failed: "异常",
          loading: "准备中", warming: "准备中", preparing: "准备中", installing: "安装中" }[item.state];
        option.textContent = state ? `${item.label}（${state}）` : item.label;
        provider.append(option);
      }
      for (const selectedId of new Set([value.selectedProviderId, savedDraft?.selectedProviderId])) {
        if (!selectedId || value.providers.some((item) => item.providerId === selectedId)) continue;
        const missing = document.createElement("option");
        missing.value = selectedId;
        missing.textContent = value.providers.length ? `${selectedId}（未加载）` : "未安装语音输入插件";
        provider.append(missing);
      }
      provider.value = value.selectedProviderId || "";
      baseline = JSON.stringify(draft());
      if (savedDraft) {
        provider.value = savedDraft.selectedProviderId || "";
      }
      refreshSelect(provider);
      renderStatus(); onDirty();
    } catch (error) {
      if (!disposed && request === revision) {
        status.textContent = "语音输入设置暂不可用，请重新打开插件设置。";
        status.hidden = false;
      }
    }
  }
  pluginSettings?.addEventListener("click", () => openPlugin(provider.value));
  provider.addEventListener("change", () => { renderStatus(); onDirty(); });
  return Object.freeze({
    refresh,
    onPageChanged(page) {
      if (page === "voice") void refresh({ preserveDraft: true });
    },
    isDirty: () => Boolean(snapshot) && JSON.stringify(draft()) !== baseline,
    async save() {
      if (!snapshot || disposed) throw new Error("ASR_SETTINGS_NOT_READY");
      const saved = JSON.parse(baseline);
      const values = Object.fromEntries(Object.entries(draft()).filter(([key, value]) => value !== saved[key]));
      if (!Object.keys(values).length) return snapshot;
      const result = await invoke("settings_asr_save", { payload: values });
      await refresh();
      return result;
    },
    dispose() { disposed = true; revision += 1;  },
  });
}
