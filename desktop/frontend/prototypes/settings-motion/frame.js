import { createState, appearanceLimits, theme } from "../settings-demo/data.js";
import { createBridge } from "../settings-demo/bridge.js";
import { createPluginSettingsFeature } from "../../settings/plugin-settings.js";
import { createProviderSettingsFeature } from "../../settings/provider-settings.js";
import { createSettingsForm } from "../../settings/settings-form.js";
import { enhanceSelect, refreshSelect, closeSelects, focusSelect } from "../../settings/select-control.js";
import { createIcon, installClickIconMotion } from "../../core/icons.js";
import { createMorphIcon } from "../../core/morph-icon.js";
import { createErrorDialog } from "../../core/error-dialog.js";
import { createAsrWaveform } from "../../audio/asr-waveform.js";
import { createAsrController } from "../../audio/asr-controller.js";
import { createConnectionEditor } from "../../../../plugins/builtin/sakura_model_openai_compatible/frontend/connections.js";
import { mount as mountAsr } from "../../../../plugins/builtin/sakura_asr_hub/frontend/input.js";
import { createMicrophoneLevels } from "./microphone.js";

const review = parent.settingsMotionPreview;
const $ = id => document.getElementById(id);
const state = createState("portrait");
const errorDialog = createErrorDialog({ document });
const signal = new AbortController();
const timers = new Set();
let currentPage = "providers", plugins, models, asr, probe = null, recording = null, probeIndicator = null;
let saving = false;
const pause = ms => new Promise(resolve => {
  const timer = setTimeout(() => { timers.delete(timer); resolve(); }, ms);
  timers.add(timer);
});
function notify(message, kind = "success") {
  const toast = document.createElement("div");
  toast.className = `toast toast-${kind}`;
  toast.textContent = message;
  $("toastStack").append(toast);
  void pause(2800).then(() => toast.remove());
}
function onError(error) {
  if (!error || error.name === "AbortError") return;
  errorDialog.show({ error, message: "操作未完成" });
}
function markDirty() {
  document.body.classList.add("is-dirty");
  $("saveButton").classList.add("has-changes");
}
const bridge = createBridge(window, state, { version: "proposed", info: (_title, message) => notify(message, "info") });
const nativeInvoke = window.__TAURI__.core.invoke;
const serviceKey = "sakura.model.openai_compatible";
let profiles = structuredClone(state.provider.providers);
const providerCatalog = () => [{ serviceKey, label: "OpenAI 兼容模型", profiles: profiles.map(p => ({
  profileId: p.id, label: p.alias, models: p.models.map(modelId => ({ modelId, label: modelId })),
})) }];
let modelSlots = state.provider.model_slots.map(slot => ({
  ...slot, selection: slot.selection.profile_id
    ? { serviceKey, profileId: slot.selection.profile_id, modelId: slot.selection.model }
    : { serviceKey: "", profileId: "", modelId: "" },
}));
const microphone = createMicrophoneLevels({ window, onLevel(level) {
  if (recording?.state !== "recording") return;
  bridge.emit("sakura://asr-level", { recordingId: recording.recordingId, sequence: ++recording.sequence, level });
} });
function stopRecording() { microphone.stop(); }
async function invoke(command, args = {}) {
  if (command === "settings_provider_model_get") return {
    schema_version: 2, window_generation: 1, core_generation_id: "preview",
    providers: providerCatalog(), model_slots: modelSlots, setup_complete: true,
  };
  if (command === "settings_provider_model_save") {
    modelSlots = modelSlots.map(slot => ({ ...slot, selection: args.draft.model_slots[slot.identity] }));
    return { change_plan: "applied", save_state: "complete" };
  }
  if (command === "asr_prepare") {
    recording = { ...args.payload, state: "preparing", sequence: 0 };
    const current = recording;
    const ready = await microphone.start(args.payload.inputDeviceId);
    if (!ready || current !== recording) return { state: "cancelled" };
    recording.state = "ready";
    return { ...recording };
  }
  if (command === "asr_capture_start") {
    recording.state = "recording";
    return { ...recording };
  }
  if (command === "asr_capture_stop") {
    stopRecording();
    Object.assign(recording, { state: "succeeded", text: "音量测试已结束。此预览不运行语音识别。" });
    return { ...recording };
  }
  if (command === "asr_poll") return recording ? { ...recording } : { state: "cancelled" };
  if (command === "asr_cancel") {
    if (args.payload.recordingId === recording?.recordingId) { stopRecording(); recording = null; }
    return null;
  }
  if (command === "settings_asr_devices") {
    const devices = (await navigator.mediaDevices.enumerateDevices()).filter(device => device.kind === "audioinput");
    return { devices: devices.map((device, index) => ({ id: device.deviceId, label: device.label || `麦克风 ${index + 1}` })), defaultDeviceId: "default" };
  }
  return nativeInvoke(command, args);
}
function replayMotion(element, className) {
  if (!element) return;
  element.classList.remove(className);
  void element.offsetWidth;
  element.classList.add(className);
}
function cancelProbe() {
  if (!probe) return;
  probe.controller.abort();
  probe.motion.dispose();
  probe.feedback.remove();
  probe = null;
}
const connection = createConnectionEditor({
  document, window, createIcon, read: () => profiles,
  write: value => { profiles = value; markDirty(); },
  notify, onError, onCatalogChanged: () => models?.refreshChoices(),
  cancel: async () => cancelProbe(),
  async probe(operation) {
    cancelProbe();
    probeIndicator?.dispose();
    probeIndicator = null;
    connection.element.querySelector(".preview-test-feedback")?.remove();
    const controller = new AbortController();
    const feedback = document.createElement("div");
    feedback.className = "preview-test-feedback";
    feedback.dataset.state = "working";
    const icon = document.createElement("span");
    icon.className = "preview-action-icon";
    const stage = document.createElement("span");
    stage.className = "sakura-morph-icon";
    icon.append(stage);
    const text = document.createElement("span");
    text.setAttribute("role", "status");
    text.textContent = operation === "list_models" ? "正在获取模型…" : "正在测试连接…";
    feedback.append(icon, text);
    connection.element.querySelector(".admin-detail").append(feedback);
    const motion = createMorphIcon({ element: stage, initial: "loader-circle" });
    motion.set("loader-circle", review.enabled);
    const ticket = { controller, feedback, motion };
    probe = ticket;
    probeIndicator = motion;
    const failed = review.outcome === "failure";
    try {
      await new Promise((resolve, reject) => {
        const timer = setTimeout(resolve, 2600);
        controller.signal.addEventListener("abort", () => { clearTimeout(timer); reject(new DOMException("演示请求已取消", "AbortError")); }, { once: true });
      });
      if (failed) {
        feedback.dataset.state = "error";
        motion.set("x");
        text.textContent = "连接失败：请求超时。";
        throw new Error("连接失败：请求超时（演示）。");
      }
      feedback.dataset.state = "success";
      motion.set("check");
      text.textContent = operation === "list_models" ? "模型列表已获取。" : "连接正常，模型已响应。";
      return { models: ["deepseek-chat", "deepseek-reasoner", "qwen3:8b"] };
    } finally {
      if (probe === ticket) probe = null;
    }
  },
});
connection.element.addEventListener("click", event => {
  if (event.target.closest(".provider-card")) cancelProbe();
}, true);
plugins = createPluginSettingsFeature({
  document, window, invoke, onDirty: () => { if (plugins?.isDirty()) markDirty(); }, onError, notify,
  confirmAction: async message => window.confirm(message),
  enhanceSelect, refreshSelect, closeSelects, focusSelect, replayMotion,
  removeOverlayAfterExit: async element => { element.classList.add("is-closing"); await Promise.allSettled(element.getAnimations().map(a => a.finished)); element.remove(); },
  showPage, isCharacterTransitioning: () => false, hasPendingCharacterSelection: () => false,
  currentCharacterId: () => state.current,
});
plugins.initialize(await invoke("settings_plugins_get"));
models = createProviderSettingsFeature({
  document, invoke, onDirty: () => { if (models?.isDirty()) markDirty(); }, onError, enhanceSelect,
  getProviderCatalog: providerCatalog,
});
await models.initialize();
$("modelProviderSurface").replaceChildren(connection.element);
connection.update();

function showPage(page) {
  if (currentPage !== page) { cancelProbe(); void asr?.cancel(); }
  currentPage = page;
  closeSelects();
  document.querySelectorAll(".settings-page").forEach(element => {
    const selected = element.id === `page-${page}`;
    element.hidden = !selected;
    element.classList.toggle("is-active", selected);
  });
  document.querySelectorAll(".nav-item[data-page]").forEach(button => {
    const selected = button.dataset.page === page;
    button.classList.toggle("is-active", selected);
    if (selected) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
  });
  $("pageTitle").textContent = document.querySelector(`.nav-item[data-page="${page}"] .nav-item-label`)?.textContent || "设置";
  $("pageSubtitle").hidden = true;
  document.querySelector(".page-scroll").classList.toggle("is-admin-active", ["memory", "plugins", "providers"].includes(page));
  if (!["providers", "model"].includes(page)) plugins?.onPageChanged(page);
  replayMotion(document.querySelector(".page-head"), "is-switching");
}
document.querySelectorAll(".nav-item[data-page]").forEach(button => button.addEventListener("click", () => showPage(button.dataset.page)));
installClickIconMotion(document);
function fillSelect(id, options, selected) {
  const select = $(id);
  if (!select) return;
  select.replaceChildren(...options.map(([value, label]) => new Option(label, value)));
  select.value = selected;
  select.disabled = false;
  enhanceSelect(select);
  refreshSelect(select);
}
fillSelect("characterSelect", state.characters.map(c => [c.id, c.name]), state.current);
fillSelect("visualSelect", [["portrait", "默认立绘"]], "portrait");
fillSelect("ttsProvider", [["sakura.tts.gpt-sovits", "GPT-SoVITS"], ["sakura.tts.genie", "Genie TTS"]], "sakura.tts.gpt-sovits");
fillSelect("asrProvider", [["sakura.asr.sensevoice", "SenseVoice"]], "sakura.asr.sensevoice");
fillSelect("visualEffectMode", [["none", "无"], ["gaussian_blur", "高斯模糊"], ["liquid_glass", "液态玻璃"]], "gaussian_blur");
for (const [id, limitKey] of Object.entries({
  portraitScale: "portraitScalePercent", controlPanelWidth: "controlPanelWidth", bubbleHeight: "bubbleMaxHeight",
  controlPanelOffset: "controlPanelVerticalOffset", inputBarOffset: "inputBarOffset",
  speechFontSize: "speechFontSize", nameFontSize: "nameFontSize", inputFontSize: "inputFontSize",
})) {
  const input = $(id);
  if (!input) continue;
  const [min, max] = appearanceLimits[limitKey];
  Object.assign(input, { min, max, value: state.appearance[limitKey], disabled: false });
  const update = () => {
    const output = document.querySelector(`output[for="${id}"]`);
    if (output) output.textContent = input.value;
    input.style.setProperty("--slider-progress", `${(input.value - min) / (max - min) * 100}%`);
  };
  input.addEventListener("input", update); update();
}
for (const [id, value] of Object.entries({
  subtitleTypingInterval: 35, replySegmentPause: 300, silentSegmentPause: 2000, bubbleAutoHideDelay: 15,
  agentSteps: 6, toolCallsPerStep: 3, toolCallsPerTurn: 12, voiceCacheDirectory: "默认语音缓存目录", voiceCacheMaxMegabytes: 2048,
})) if ($(id)) { $(id).value = value; $(id).disabled = false; }
for (const id of ["ttsEnabled", "bubbleAutoExpand", "telemetryEnabled", "updateAutoCheck", "voiceCacheIdleFill"]) $(id).checked = true;
$("launchAtLogin").disabled = false;
$("storageUserRoot").textContent = "演示 / Sakura";
$("storageTtsRoot").textContent = "默认语音目录";
$("telemetryInstallationId").textContent = "00000000-0000-4000-8000-000000000001";
$("asrLocation").textContent = "在本机识别";
$("aboutVersion").textContent = "设置动效预览";

const voiceSection = state.plugins.find(p => p.pluginId === "sakura.tts.gpt-sovits").sections.find(s => s.sectionId === "runtime");
const voiceFields = createSettingsForm({
  document, plugin: { id: "preview-tts" }, section: { ...voiceSection, section_id: voiceSection.sectionId },
  read: key => voiceSection.values[key], write: (key, value) => { voiceSection.values[key] = value; markDirty(); },
  enhanceSelect, refreshSelect,
});
$("voiceSettings").append(voiceFields.element);
voiceFields.mounted();
const asrCanvas = document.createElement("canvas");
asrCanvas.className = "preview-asr-waveform";
asrCanvas.hidden = true;
asrCanvas.setAttribute("aria-hidden", "true");
const asrWave = createAsrWaveform({ canvas: asrCanvas, window });
let asrState = "idle";
asr = await mountAsr({
  document, invoke, listen: window.__TAURI__.event.listen, signal: signal.signal,
  read: key => key === "selectedProviderId" ? "sakura.asr.sensevoice" : state.asr[key],
  write: (key, value) => { state.asr[key] = value; markDirty(); }, enhanceSelect, refreshSelect, onError,
  importModule: async () => ({
    createAsrController: options => createAsrController({
      ...options,
      onLevel(value) { options.onLevel(value); asrWave.push(value); },
      onState(value) {
        options.onState(value);
        const actions = asr?.element.querySelectorAll(".plugin-setting-actions button");
        if (actions) actions[1].textContent = value.state === "recording" ? "结束音量测试" : "开始音量测试";
        asrState = value.state;
        asrCanvas.hidden = value.state !== "recording" || !review.enabled;
        if (!asrCanvas.hidden) asrWave.start(); else asrWave.stop();
        const meter = asr?.element.querySelector("meter");
        if (meter && review.enabled) meter.hidden = true;
      },
    }),
  }),
});
$("asrSettings").append(asr.element);
asr.element.querySelector("meter").after(asrCanvas);
asr.element.querySelector("meter").setAttribute("aria-label", "麦克风实际音量");
asr.element.querySelectorAll(".plugin-setting-actions button")[1].textContent = "开始音量测试";
const microphoneNote = document.createElement("p");
microphoneNote.className = "setting-desc";
microphoneNote.textContent = "点击开始后读取麦克风实际音量。音频不保存、不上传；此预览不运行语音识别。";
asr.element.prepend(microphoneNote);

const themeLabels = { primary: "主色", primaryHover: "主色悬停", accent: "强调色", text: "文字", secondaryText: "次级文字", mutedText: "弱化文字", pageBackground: "页面背景", panelBackground: "面板背景", inputBackground: "输入背景", bubbleBackground: "气泡背景", border: "边框" };
const themeVars = { primary: "primary", primaryHover: "primary-hover", accent: "accent", text: "text", secondaryText: "secondary-text", mutedText: "muted-text", pageBackground: "page-bg", panelBackground: "panel-bg", inputBackground: "input-bg", bubbleBackground: "bubble-bg", border: "border" };
for (const [key, value] of Object.entries(theme)) {
  const row = document.createElement("label"); row.className = "form-row";
  const title = document.createElement("span"); title.textContent = themeLabels[key];
  const input = document.createElement("input"); input.type = "color"; input.value = value;
  input.addEventListener("input", () => document.documentElement.style.setProperty(`--sakura-${themeVars[key]}`, input.value));
  row.append(title, input); $("themeColors").append(row);
}
$("resetThemeButton").addEventListener("click", () => {
  for (const [key, value] of Object.entries(theme)) document.documentElement.style.setProperty(`--sakura-${themeVars[key]}`, value);
  $("themeColors").querySelectorAll("input").forEach((input, index) => input.value = Object.values(theme)[index]);
  markDirty(); notify("已恢复默认配色。");
});
const applyIcon = document.createElement("span");
applyIcon.className = "preview-action-icon";
const morphElement = document.createElement("span"); morphElement.className = "sakura-morph-icon"; applyIcon.append(morphElement);
$("applyButton").prepend(applyIcon);
const applyMotion = createMorphIcon({ element: morphElement, initial: "check" });
async function save(close = false) {
  if (saving) return;
  saving = true;
  const button = close ? $("saveButton") : $("applyButton");
  const label = close ? "保存并关闭" : "应用";
  button.lastChild.textContent = close ? "保存中…" : "应用中…";
  document.querySelectorAll("footer button").forEach(b => b.disabled = true);
  document.querySelector(".page-scroll").inert = true;
  if (review.enabled) applyMotion.set("loader-circle", true);
  try {
    await pause(900); await plugins.save(); await models.save();
    document.body.classList.remove("is-dirty");
    $("saveButton").classList.remove("has-changes");
    applyMotion.set("check");
    button.lastChild.textContent = close ? "已保存" : "已应用";
    notify(close ? "演示设置已保存；预览保持打开。" : "已应用。");
  } catch (error) { onError(error); button.lastChild.textContent = label; }
  finally {
    saving = false;
    document.querySelectorAll("footer button").forEach(b => b.disabled = false);
    document.querySelector(".page-scroll").inert = false;
  }
}
$("applyButton").addEventListener("click", () => save());
$("saveButton").addEventListener("click", () => save(true));
$("cancelButton").addEventListener("click", () => { cancelProbe(); void asr.cancel(); notify("预览已停止当前测试。", "info"); });
document.addEventListener("input", () => { markDirty(); if (!saving) $("applyButton").lastChild.textContent = "应用"; });
document.addEventListener("change", markDirty);
review.setMode = enabled => {
  document.body.dataset.previewMotion = enabled ? "on" : "off";
  if (probe) probe.motion.set("loader-circle", enabled);
  asrCanvas.hidden = !enabled || asrState !== "recording";
  if (!asrCanvas.hidden) asrWave.start(); else asrWave.stop();
  asr.element.querySelector("meter").hidden = enabled || asrState !== "recording";
};
review.setMode(review.enabled);
window.addEventListener("pagehide", () => {
  cancelProbe(); stopRecording(); asrWave.stop(); signal.abort();
  asr.dispose(); plugins.dispose(); models.dispose(); connection.dispose(); applyMotion.dispose(); probeIndicator?.dispose();
  timers.forEach(clearTimeout); review.setMode = null;
}, { once: true });
showPage("providers");
review.showDemo = key => {
  const targets = {
    connection: ["providers", ".detail-actions button"],
    apply: [currentPage, "#applyButton"],
    advanced: ["voice", ".advanced-section summary"],
    voice: ["voice", ".asr-input-controls .plugin-setting-actions button:nth-child(2)"],
    navigation: ["character", '[data-page="character"]'],
    select: ["character", "#characterSelect"],
  };
  const [page, selector] = targets[key];
  showPage(page);
  const target = document.querySelector(selector);
  const control = target?.tagName === "SELECT" ? target.previousElementSibling : target;
  control?.scrollIntoView({ block: "center" });
  control?.focus({ preventScroll: true });
};
document.body.classList.remove("is-dirty");
document.body.dataset.previewReady = "true";
