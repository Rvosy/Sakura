import { petStore } from "./core/store.js";
import { PetController } from "./pet/pet_controller.js";
import { PortraitController } from "./pet/portrait_controller.js";
import { SubtitleController } from "./pet/subtitle_controller.js";
import { ChatController } from "./chat/chat_controller.js";
import { ConfirmationView } from "./chat/confirmation_view.js";
import { AudioController } from "./audio/audio_controller.js";
import { CaptureController } from "./capture/capture_controller.js";

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
  openSettingsButton: document.querySelector("#open-settings"),
  openStudioButton: document.querySelector("#open-studio"),
  openHistoryButton: document.querySelector("#open-history"),
  openDiagnosticsButton: document.querySelector("#open-diagnostics"),
  hideButton: document.querySelector("#hide-pet"),
  currentPortrait: document.querySelector("#portrait-current"),
  transitionPortrait: document.querySelector("#portrait-transition"),
  portraitFallback: document.querySelector("#portrait-fallback"),
  characterName: document.querySelector("#character-name"),
  subtitleText: document.querySelector("#subtitle-text"),
  send: document.querySelector("#send-message"),
  cancel: document.querySelector("#cancel-message"),
  capture: document.querySelector("#capture-screen"),
  confirmationPanel: document.querySelector("#tool-confirmation"),
  confirmationName: document.querySelector("#tool-confirmation-name"),
  confirmationReason: document.querySelector("#tool-confirmation-reason"),
  confirmationArguments: document.querySelector("#tool-confirmation-arguments"),
  confirmAction: document.querySelector("#confirm-tool-action"),
  rejectAction: document.querySelector("#reject-tool-action"),
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

let chatController;
const audioController = new AudioController({
  store: petStore,
  invoke: callDesktop,
  setStatus: setResult,
});
const confirmationView = new ConfirmationView({
  panel: elements.confirmationPanel,
  name: elements.confirmationName,
  reason: elements.confirmationReason,
  argumentsView: elements.confirmationArguments,
  confirmButton: elements.confirmAction,
  rejectButton: elements.rejectAction,
  onConfirm: (actionId) => chatController.confirm(actionId).catch(() => {}),
  onReject: (actionId) => chatController.reject(actionId).catch(() => {}),
});
const captureController = new CaptureController({
  store: petStore,
  invoke: callDesktop,
  setStatus: setResult,
});

chatController = new ChatController({
  store: petStore,
  invoke: callDesktop,
  subtitleController,
  confirmationView,
  audioController,
  setStatus: setResult,
});

async function loadPetBootstrap(brain, { force = false } = {}) {
  if (!brain?.acceptingRequests || (!force && brain.sessionGeneration === loadedSessionGeneration)) return;
  const bootstrap = await callDesktop("pet_bootstrap");
  loadedSessionGeneration = brain.sessionGeneration;
  petController.applyBootstrap(bootstrap);
}

async function refreshPetBootstrap() {
  await audioController.stop().catch(() => {});
  const brain = await callDesktop("brain_status");
  await loadPetBootstrap(brain, { force: true });
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
    chatController.reset();
    elements.status.textContent = `Brain 正在恢复 · 第 ${brain.restartCount} 次重启`;
    return;
  }
  if (phase === "diagnostic") {
    loadedSessionGeneration = null;
    petStore.resetSession();
    chatController.reset();
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

for (const [button, command] of [
  [elements.openSettingsButton, "open_settings_window"],
  [elements.openStudioButton, "open_studio_window"],
  [elements.openHistoryButton, "open_history_window"],
  [elements.openDiagnosticsButton, "open_diagnostics_window"],
]) {
  button.addEventListener("click", () => {
    callDesktop(command).catch((error) => setResult(`窗口打开失败：${error}`, "error"));
  });
}

window.addEventListener("DOMContentLoaded", () => {
  document.documentElement.dataset.ready = "true";
  if (listen) {
    listen("sakura://click-through-changed", ({ payload }) => {
      clickThrough = Boolean(payload?.enabled);
      elements.clickThroughButton.setAttribute("aria-pressed", String(clickThrough));
      elements.clickThroughButton.textContent = clickThrough ? "已穿透 · 托盘恢复" : "鼠标穿透";
    });
    listen("sakura://brain-status", ({ payload }) => renderBrainStatus(payload));
    listen("sakura://chat-progress", ({ payload }) => chatController.handleProgress(payload));
    listen("sakura://chat-reply", ({ payload }) => chatController.handleReply(payload));
    listen("sakura://chat-cancelled", ({ payload }) => chatController.handleCancelled(payload));
    listen("sakura://chat-error", ({ payload }) => chatController.handleError(payload));
    listen("sakura://chat-confirmation-requested", ({ payload }) =>
      chatController.handleConfirmation(payload),
    );
    listen("sakura://tts-audio-ready", ({ payload }) => audioController.handleAudioReady(payload));
    listen("sakura://tts-error", ({ payload }) => audioController.handleSynthesisError(payload));
    listen("sakura://tts-cancelled", ({ payload }) =>
      audioController.handleSynthesisCancelled(payload),
    );
    listen("sakura://tts-playback-state", ({ payload }) =>
      audioController.handlePlaybackState(payload),
    );
    listen("sakura://assistant-backchannel", ({ payload }) => {
      const segment = payload?.segment;
      if (!segment) return;
      subtitleController.showSegments([segment]);
      audioController.queueSegments([segment]);
    });
    listen("sakura://assistant-busy-changed", ({ payload }) => {
      if (payload?.kind === "chat") return;
      petStore.setInteractionState({ busy: Boolean(payload?.busy), interactionId: null });
    });
    listen("sakura://assistant-proactive-message", ({ payload }) => {
      const segments = payload?.reply?.segments || [];
      if (segments.length) subtitleController.showSegments(segments);
      if (segments.length) audioController.queueSegments(segments);
      setResult(payload?.kind === "reminder" ? "提醒已送达。" : "主动观察已完成。", "success");
    });
    listen("sakura://manual-observation-ready", ({ payload }) =>
      captureController.handleReady(payload),
    );
    listen("sakura://manual-observation-cancelled", () => captureController.handleCancelled());
    listen("sakura://manual-observation-error", ({ payload }) =>
      captureController.handleError(payload),
    );
    listen("sakura://layout-preview", ({ payload }) => {
      petController.previewLayout(payload || {}).catch((error) => {
        setResult(`布局预览失败：${error}`, "error");
      });
    });
    listen("sakura://character-changed", () => {
      refreshPetBootstrap().catch((error) => setResult(`角色刷新失败：${error}`, "error"));
    });
    listen("sakura://settings-changed", () => {
      refreshPetBootstrap().catch((error) => setResult(`设置刷新失败：${error}`, "error"));
    });
  }
  callDesktop("brain_status").then(renderBrainStatus).catch((error) => {
    setResult(`无法读取 Brain 状态：${error}`, "error");
  });
});

window.addEventListener("sakura:chat-send", ({ detail }) => {
  chatController.send(detail.text, detail.observationId).then((accepted) => {
    if (!accepted) return;
    captureController.clearAttachment();
    elements.input.value = "";
  }).catch(() => {});
});

window.addEventListener("sakura:chat-cancel", () => {
  chatController.cancel().catch(() => {});
});

window.addEventListener("sakura:capture-request", () => {
  captureController.open().catch(() => {});
});
