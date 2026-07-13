import { petStore } from "./core/store.js";
import { PetController } from "./pet/pet_controller.js";
import { PortraitController } from "./pet/portrait_controller.js";
import { SubtitleController } from "./pet/subtitle_controller.js";

const invoke = window.__TAURI__?.core?.invoke;
const listen = window.__TAURI__?.event?.listen;

const elements = {
  stage: document.querySelector("#pet-stage"),
  input: document.querySelector("#message-input"),
  status: document.querySelector("#desktop-status"),
  result: document.querySelector("#prototype-result"),
  audioButton: document.querySelector("#audio-prototype"),
  capturePrototypeButton: document.querySelector("#capture-prototype"),
  clickThroughButton: document.querySelector("#click-through-toggle"),
  hideButton: document.querySelector("#hide-pet"),
  currentPortrait: document.querySelector("#portrait-current"),
  transitionPortrait: document.querySelector("#portrait-transition"),
  portraitFallback: document.querySelector("#portrait-fallback"),
  characterName: document.querySelector("#character-name"),
  subtitleText: document.querySelector("#subtitle-text"),
  send: document.querySelector("#send-message"),
  cancel: document.querySelector("#cancel-message"),
  capture: document.querySelector("#capture-screen"),
};

let dragging = false;
let clickThrough = false;
let loadedSessionGeneration = null;

function setResult(message, kind = "ready") {
  elements.result.textContent = message;
  elements.result.dataset.kind = kind;
}

async function callDesktop(command, payload = {}) {
  if (!invoke) throw new Error("Tauri bridge unavailable");
  return invoke(command, payload);
}

const subtitleController = new SubtitleController({
  target: elements.subtitleText,
  onSegment: (segment) => portraitController.showForSegment(segment),
});

const portraitController = new PortraitController({
  currentImage: elements.currentPortrait,
  transitionImage: elements.transitionPortrait,
  fallback: elements.portraitFallback,
  onNaturalSize: (size) => petController.setPortraitNaturalSize(size),
});

const petController = new PetController({
  store: petStore,
  invoke: callDesktop,
  portraitController,
  subtitleController,
  elements,
});

async function loadPetBootstrap(brain) {
  if (!brain?.acceptingRequests || brain.sessionGeneration === loadedSessionGeneration) return;
  const bootstrap = await callDesktop("pet_bootstrap");
  loadedSessionGeneration = brain.sessionGeneration;
  petController.applyBootstrap(bootstrap);
}

function renderBrainStatus(brain) {
  const phase = brain?.phase || "starting";
  document.documentElement.dataset.brainPhase = phase;
  if (phase === "ready") {
    elements.status.textContent = brain.restartCount
      ? `Brain 已恢复 · 第 ${brain.restartCount} 次重启`
      : "Brain Host 已就绪";
    loadPetBootstrap(brain).catch((error) => setResult(`角色加载失败：${error}`, "error"));
    return;
  }
  if (phase === "restarting") {
    loadedSessionGeneration = null;
    petStore.resetSession();
    elements.status.textContent = `Brain 正在恢复 · 第 ${brain.restartCount} 次重启`;
    return;
  }
  if (phase === "diagnostic") {
    loadedSessionGeneration = null;
    petStore.resetSession();
    elements.status.textContent = "Brain Host 暂不可用 · 诊断模式";
    setResult(
      brain.diagnostic?.message || "Brain Host 连续启动失败，请查看诊断信息。",
      "error",
    );
    return;
  }
  if (phase === "stopping" || phase === "stopped") {
    elements.status.textContent = "Brain Host 已停止";
    return;
  }
  elements.status.textContent = "正在初始化 Brain Host";
}

elements.stage.addEventListener("pointerdown", async (event) => {
  if (event.button !== 0 || event.target.closest("button, input, details")) return;
  const dragRegion = event.target.closest("[data-drag-region]");
  if (!dragRegion || dragging) return;
  dragging = true;
  try {
    await callDesktop("start_dragging");
  } catch (error) {
    setResult(`窗口拖动失败：${error}`, "error");
  } finally {
    dragging = false;
  }
});

elements.audioButton.addEventListener("click", async () => {
  elements.audioButton.disabled = true;
  try {
    await callDesktop("play_audio_prototype");
    setResult("提示音播放完成。", "success");
  } catch (error) {
    setResult(`音频原型失败：${error}`, "error");
  } finally {
    elements.audioButton.disabled = false;
  }
});

elements.capturePrototypeButton.addEventListener("click", async () => {
  elements.capturePrototypeButton.disabled = true;
  try {
    const capture = await callDesktop("capture_screen_prototype");
    setResult(
      `截图完成：${capture.width} × ${capture.height}，${capture.byteLength.toLocaleString()} 字节。`,
      "success",
    );
  } catch (error) {
    setResult(`截图原型失败：${error}`, "error");
  } finally {
    elements.capturePrototypeButton.disabled = false;
  }
});

elements.clickThroughButton.addEventListener("click", async () => {
  const next = !clickThrough;
  try {
    await callDesktop("set_click_through", { enabled: next });
    clickThrough = next;
    elements.clickThroughButton.setAttribute("aria-pressed", String(next));
    elements.clickThroughButton.textContent = next ? "已穿透 · 托盘恢复" : "鼠标穿透";
  } catch (error) {
    setResult(`切换鼠标穿透失败：${error}`, "error");
  }
});

elements.hideButton.addEventListener("click", () => {
  callDesktop("set_pet_visible", { visible: false }).catch((error) => {
    setResult(`隐藏窗口失败：${error}`, "error");
  });
});

window.addEventListener("DOMContentLoaded", () => {
  document.documentElement.dataset.ready = "true";
  if (listen) {
    listen("sakura://click-through-changed", ({ payload }) => {
      clickThrough = Boolean(payload?.enabled);
      elements.clickThroughButton.setAttribute("aria-pressed", String(clickThrough));
      elements.clickThroughButton.textContent = clickThrough ? "已穿透 · 托盘恢复" : "鼠标穿透";
    });
    listen("sakura://brain-status", ({ payload }) => renderBrainStatus(payload));
  }
  callDesktop("brain_status").then(renderBrainStatus).catch((error) => {
    setResult(`无法读取 Brain 状态：${error}`, "error");
  });
});

window.addEventListener("sakura:chat-send", ({ detail }) => {
  setResult(`待接通聊天：${detail.text}`, "ready");
});

window.addEventListener("sakura:chat-cancel", () => {
  setResult("待接通取消请求。", "ready");
});

window.addEventListener("sakura:capture-request", () => {
  setResult("待接通框选截图。", "ready");
});
