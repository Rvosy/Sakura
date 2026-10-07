export function createAsrInputTest({ document, invoke, listen, readProvider, readDevice, createAsrController,
  readContext = () => "settings-asr-test", onError = () => {} }) {
  const start = document.getElementById("asrTestStart");
  const cancel = document.getElementById("asrTestCancel");
  const output = document.getElementById("asrTestResult");
  const level = document.getElementById("asrTestLevel");
  let connected = null, disposed = false;
  const reportFailure = (error) => {
    if (disposed) return;
    output.textContent = "测试失败";
    onError(error, "语音识别测试失败");
  };
  const controller = createAsrController({
    invoke, listen,
    readContext,
    readDraft: () => ({ value: "", version: 0, selectionStart: 0, selectionEnd: 0 }),
    prepareOptions: () => ({ purpose: "test", providerId: readProvider(), inputDeviceId: readDevice() }),
    writeDraft: ({ value }) => { output.textContent = value; start.focus?.({ preventScroll: true }); },
    onLevel: (value) => { level.value = value; },
    onError: reportFailure,
    onState: ({ state }) => {
      const busy = state !== "idle";
      start.disabled = state === "preparing" || state === "recognizing";
      start.textContent = state === "recording" ? "停止并识别" : "开始测试";
      cancel.hidden = !busy;
      level.hidden = state !== "recording";
      if (state !== "recording") level.value = 0;
      output.textContent = { preparing: "正在准备", recording: "正在录音", recognizing: "正在识别", idle: "" }[state];
    },
  });
  async function activate() {
    if (disposed) return;
    if (controller.state() === "recording") return controller.stop();
    if (controller.active()) return;
    if (!readProvider()) { output.textContent = "请先选择要测试的识别引擎。"; return; }
    connected ||= controller.connect();
    try { await connected; if (!disposed) await controller.start(); }
    catch (error) { reportFailure(error); }
  }
  function cancelTest() { return controller.cancel({ restore: false }); }
  function onKey(event) {
    if (event.key !== "Escape" || !controller.active()) return;
    event.preventDefault(); event.stopImmediatePropagation(); void cancelTest();
  }
  start.addEventListener("click", activate);
  cancel.addEventListener("click", cancelTest);
  document.addEventListener?.("keydown", onKey, true);
  return {
    activate, cancel: cancelTest, active: controller.active,
    dispose() {
      disposed = true; controller.dispose();
      document.removeEventListener?.("keydown", onKey, true);
    },
  };
}

export async function mount({ document, invoke, listen, read, write, enhanceSelect, refreshSelect,
  onError, importModule, signal }) {
  const { createAsrController } = await importModule("audio/asr-controller.js");
  const root = document.createElement("div"); root.className = "asr-input-controls";
  const controls = {};
  const node = (tag, id, text = "") => {
    const element = document.createElement(tag); element.textContent = text;
    if (id) controls[id] = element;
    return element;
  };
  const row = (title, id) => {
    const label = node("label"); label.className = "setting-row";
    label.append(node("span", null, title));
    const select = node("select", id); select.setAttribute("aria-label", title);
    label.append(select); root.append(label); enhanceSelect(select); return select;
  };
  const provider = row("测试引擎", "provider");
  const device = row("麦克风", "device");
  const actions = node("div"); actions.className = "plugin-setting-actions";
  const refresh = node("button", "refresh", "刷新设备");
  const start = node("button", "asrTestStart", "开始测试");
  const cancel = node("button", "asrTestCancel", "取消测试"); cancel.hidden = true;
  for (const button of [refresh, start, cancel]) { button.type = "button"; button.className = "secondary-button"; actions.append(button); }
  const level = node("meter", "asrTestLevel"); level.min = 0; level.max = 1; level.value = 0; level.hidden = true;
  level.setAttribute("aria-label", "实际麦克风音量");
  const output = node("output", "asrTestResult"); output.className = "asr-test-result"; output.setAttribute("role", "status");
  root.append(actions, level, output);
  let disposed = false, revision = 0;
  let testProvider = read("selectedProviderId");
  const test = createAsrInputTest({ invoke, listen, createAsrController,
    document: { getElementById: id => controls[id],
      addEventListener: (...args) => document.addEventListener(...args),
      removeEventListener: (...args) => document.removeEventListener(...args) },
    readProvider: () => provider.value, readDevice: () => device.value, onError,
  });
  const choose = (select, id) => {
    if (id && !Array.from(select.children).some(option => option.value === id)) {
      const option = node("option", null, `${id}（未连接）`); option.value = id; select.append(option);
    }
    select.value = id || ""; refreshSelect(select);
  };
  const update = () => {
    choose(provider, testProvider);
    choose(device, read("inputDeviceId"));
  };
  const refreshDevices = async () => {
    const ticket = ++revision; refresh.disabled = true;
    try {
      const [snapshot, devices] = await Promise.all([invoke("settings_asr_get"), invoke("settings_asr_devices")]);
      if (disposed || signal.aborted || ticket !== revision) return;
      provider.replaceChildren();
      for (const item of snapshot.providers) {
        const option = node("option", null, item.label); option.value = item.providerId; provider.append(option);
      }
      device.replaceChildren();
      const defaultDevice = devices.devices.find(item => item.id === devices.defaultDeviceId);
      const system = node("option", null, defaultDevice ? `系统默认（${defaultDevice.label}）` : "系统默认");
      system.value = ""; device.append(system);
      for (const item of devices.devices) {
        const option = node("option", null, item.label); option.value = item.id; device.append(option);
      }
      update();
    } catch (error) { if (!disposed && !signal.aborted) onError(error); }
    finally { if (!disposed) refresh.disabled = false; }
  };
  provider.addEventListener("change", () => { void test.cancel(); testProvider = provider.value; });
  device.addEventListener("change", () => { void test.cancel(); write("inputDeviceId", device.value); });
  refresh.addEventListener("click", refreshDevices);
  signal.addEventListener("abort", () => test.dispose(), { once: true });
  void refreshDevices();
  return { element: root, update, cancel: test.cancel,
    dispose() { disposed = true; revision++; test.dispose(); root.remove(); },
  };
}
