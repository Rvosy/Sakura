import { createAsrInputTest } from "./asr-input-test.js";

export function createAsrSettingsController({ document, invoke, enhanceSelect = () => {},
  refreshSelect = () => {}, onDirty = () => {}, onStatus = () => {},
  listen = null }) {
  const provider = document.getElementById("asrProvider");
  const status = document.getElementById("asrStatus");
  const location = document.getElementById("asrLocation");
  const settings = document.getElementById("asrSettings");
  const settingsHome = document.getElementById("asrSettingsHome");
  const device = document.getElementById("asrInputDevice");
  const inputControls = document.getElementById("asrInputControls");
  const inputControlsHome = document.getElementById("asrInputControlsHome");
  let pluginProvider = null;
  const inputTest = listen ? createAsrInputTest({
    document, invoke, listen, readProvider: () => pluginProvider || provider.value,
    readDevice: () => device?.value || "",
  }) : null;
  let snapshot = null;
  let baseline = "";
  let disposed = false;
  let revision = 0;
  let deviceRevision = 0;
  const draft = () => ({ selectedProviderId: provider.value || null,
    inputDeviceId: device?.value || "" });
  enhanceSelect(provider);
  if (device) enhanceSelect(device);

  function selectDevice(id) {
    if (!device) return;
    if (id && !Array.from(device.children).some((item) => item.value === id)) {
      const missing = document.createElement("option");
      missing.value = id; missing.textContent = `${id}（未连接）`; device.append(missing);
    }
    device.value = id || ""; refreshSelect(device);
  }
  async function refreshDevices() {
    if (!device) return;
    const request = ++deviceRevision;
    try {
      const value = await invoke("settings_asr_devices");
      if (disposed || request !== deviceRevision || !Array.isArray(value?.devices)) return;
      const selected = device.value;
      device.replaceChildren();
      const system = document.createElement("option");
      system.value = "";
      const defaultDevice = value.devices.find((item) => item.id === value.defaultDeviceId);
      system.textContent = defaultDevice ? `系统默认（${defaultDevice.label}）` : "系统默认";
      device.append(system);
      for (const item of value.devices) {
        const option = document.createElement("option");
        option.value = item.id; option.textContent = item.label; device.append(option);
      }
      selectDevice(selected);
    } catch {
      if (!disposed && request === deviceRevision) onStatus("无法读取麦克风列表，请检查设备后重新刷新。", "error");
    }
  }

  function renderStatus() {
    const selected = snapshot?.providers.find((item) => item.providerId === provider.value);
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
      if (!value.selectedProviderId) {
        const none = document.createElement("option");
        none.value = ""; none.disabled = true;
        none.textContent = value.providers.length ? "选择引擎" : "暂无可用引擎";
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
        missing.value = selectedId; missing.textContent = `${selectedId}（未加载）`; provider.append(missing);
      }
      provider.value = value.selectedProviderId || "";
      selectDevice(value.inputDeviceId || "");
      baseline = JSON.stringify(draft());
      if (savedDraft) {
        provider.value = savedDraft.selectedProviderId || "";
        selectDevice(savedDraft.inputDeviceId);
      }
      refreshSelect(provider);
      status.textContent = ""; status.hidden = true; renderStatus(); onDirty();
    } catch (error) {
      if (!disposed && request === revision) {
        status.textContent = "语音输入设置暂不可用，请重新打开插件设置。";
        status.hidden = false;
      }
    }
  }
  provider.addEventListener("change", () => { void inputTest?.cancel(); renderStatus(); onDirty(); });
  device?.addEventListener("change", () => { void inputTest?.cancel(); onDirty(); });
  document.getElementById("asrRefreshDevices")?.addEventListener("click", refreshDevices);
  const hasPluginControls = (pluginId) => Boolean(pluginId && (pluginId === snapshot?.hubPluginId
    || snapshot?.providers.some((item) => item.providerId === pluginId)));
  return Object.freeze({
    refresh,
    refreshDevices,
    hasPluginControls,
    pluginDraft: draft,
    restorePluginDraft(value) {
      provider.value = value?.selectedProviderId || "";
      selectDevice(value?.inputDeviceId || "");
      refreshSelect(provider); renderStatus(); onDirty();
    },
    mountPluginControls(pluginId, container) {
      if (!hasPluginControls(pluginId)) return;
      if (pluginProvider !== pluginId) void inputTest?.cancel();
      pluginProvider = pluginId;
      if (pluginId === snapshot.hubPluginId) {
        container.append(settings); renderStatus(); void refresh({ preserveDraft: true });
      } else {
        container.append(inputControls); void refreshDevices();
      }
    },
    unmountPluginControls() {
      if (!pluginProvider) return;
      void inputTest?.cancel(); pluginProvider = null;
      settingsHome?.append(settings); inputControlsHome?.append(inputControls);
    },
    cancelTest: () => inputTest?.cancel(),
    onPageChanged(page) { if (page !== "plugins") void inputTest?.cancel(); },
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
    dispose() { disposed = true; revision += 1; inputTest?.dispose(); },
  });
}
