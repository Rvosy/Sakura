import { createAsrController } from "../audio/asr-controller.js";

export function createAsrInputTest({ document, invoke, listen, readProvider, readDevice,
  readContext = () => "settings-asr-test" }) {
  const start = document.getElementById("asrTestStart");
  const cancel = document.getElementById("asrTestCancel");
  const output = document.getElementById("asrTestResult");
  const level = document.getElementById("asrTestLevel");
  let connected = null, disposed = false;
  const controller = createAsrController({
    invoke, listen,
    readContext,
    readDraft: () => ({ value: "", version: 0, selectionStart: 0, selectionEnd: 0 }),
    prepareOptions: () => ({ purpose: "test", providerId: readProvider(), inputDeviceId: readDevice() }),
    writeDraft: ({ value }) => { output.textContent = value; start.focus?.({ preventScroll: true }); },
    onLevel: (value) => { level.value = value; },
    onError: (message) => { output.textContent = message; },
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
    catch { output.textContent = "无法连接麦克风测试，请重新打开设置后重试。"; }
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
