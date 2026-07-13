const invoke = window.__TAURI__?.core?.invoke;
const listen = window.__TAURI__?.event?.listen;

const stage = document.querySelector("#pet-stage");
const input = document.querySelector("#message-input");
const status = document.querySelector("#desktop-status");
const result = document.querySelector("#prototype-result");
const audioButton = document.querySelector("#audio-prototype");
const captureButton = document.querySelector("#capture-prototype");
const clickThroughButton = document.querySelector("#click-through-toggle");
const hideButton = document.querySelector("#hide-pet");

let composing = false;
let dragging = false;
let clickThrough = false;

function setResult(message, kind = "ready") {
  result.textContent = message;
  result.dataset.kind = kind;
}

async function callDesktop(command, payload = {}) {
  if (!invoke) {
    throw new Error("Tauri bridge unavailable");
  }
  return invoke(command, payload);
}

input.addEventListener("compositionstart", () => {
  composing = true;
  status.textContent = "输入法正在组合文本";
});

input.addEventListener("compositionend", () => {
  composing = false;
  status.textContent = "输入法组合完成";
});

input.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || composing || event.isComposing) {
    return;
  }
  event.preventDefault();
  setResult(input.value.trim() || "输入法事件正常，空消息未发送。", "success");
});

stage.addEventListener("pointerdown", async (event) => {
  if (event.button !== 0 || event.target.closest("button, input")) {
    return;
  }
  const dragRegion = event.target.closest("[data-drag-region]");
  if (!dragRegion || dragging) {
    return;
  }
  dragging = true;
  try {
    await callDesktop("start_dragging");
  } catch (error) {
    setResult(`窗口拖动失败：${error}`, "error");
  } finally {
    dragging = false;
  }
});

audioButton.addEventListener("click", async () => {
  audioButton.disabled = true;
  setResult("正在请求 Rust 播放提示音…");
  try {
    await callDesktop("play_audio_prototype");
    setResult("提示音播放完成。", "success");
  } catch (error) {
    setResult(`音频原型失败：${error}`, "error");
  } finally {
    audioButton.disabled = false;
  }
});

captureButton.addEventListener("click", async () => {
  captureButton.disabled = true;
  setResult("正在请求 Rust 捕获主显示器…");
  try {
    const capture = await callDesktop("capture_screen_prototype");
    setResult(
      `截图完成：${capture.width} × ${capture.height}，${capture.byteLength.toLocaleString()} 字节。`,
      "success",
    );
  } catch (error) {
    setResult(`截图原型失败：${error}`, "error");
  } finally {
    captureButton.disabled = false;
  }
});

clickThroughButton.addEventListener("click", async () => {
  const next = !clickThrough;
  try {
    await callDesktop("set_click_through", { enabled: next });
    clickThrough = next;
    clickThroughButton.setAttribute("aria-pressed", String(next));
    clickThroughButton.textContent = next ? "已穿透 · 托盘恢复" : "鼠标穿透";
    setResult(next ? "鼠标穿透已开启，请从托盘恢复交互。" : "鼠标交互已恢复。", "success");
  } catch (error) {
    setResult(`切换鼠标穿透失败：${error}`, "error");
  }
});

hideButton.addEventListener("click", async () => {
  try {
    await callDesktop("set_pet_visible", { visible: false });
  } catch (error) {
    setResult(`隐藏窗口失败：${error}`, "error");
  }
});

window.addEventListener("DOMContentLoaded", () => {
  document.documentElement.dataset.ready = "true";
  status.textContent = "Tauri 桌面核心已就绪";
  if (listen) {
    listen("sakura://click-through-changed", ({ payload }) => {
      clickThrough = Boolean(payload?.enabled);
      clickThroughButton.setAttribute("aria-pressed", String(clickThrough));
      clickThroughButton.textContent = clickThrough ? "已穿透 · 托盘恢复" : "鼠标穿透";
    });
  }
});
