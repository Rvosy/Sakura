import { createErrorDialog } from '../core/error-dialog.js';
import { installDevtoolsShortcutGuard } from "../core/devtools-guard.js";
import { createRuntimeDiagnostics } from "../core/runtime-diagnostics.js";

installDevtoolsShortcutGuard();
document.addEventListener("contextmenu", (event) => event.preventDefault());

const runtimeDiagnostics = createRuntimeDiagnostics({ invoke: window.__TAURI__.core.invoke });
const invoke = runtimeDiagnostics.invoke;
const errorDialog = createErrorDialog({ document });
window.addEventListener("pagehide", () => errorDialog.dispose(), { once: true });
window.addEventListener("beforeunload", () => runtimeDiagnostics.dispose({ settings: true }), { once: true });
const welcomeView = document.getElementById("welcomeView");
const macosOpenHelpView = document.getElementById("macosOpenHelpView");
const firstUseButton = document.getElementById("firstUseButton");
const macosOpenHelpButton = document.getElementById("macosOpenHelpButton");
const macosOpenHelpBackButton = document.getElementById("macosOpenHelpBackButton");
const macosOpenSystemSettingsButton = document.getElementById("macosOpenSystemSettingsButton");
const macosOpenAppleSupportButton = document.getElementById("macosOpenAppleSupportButton");
const macosOpenHelpStatus = document.getElementById("macosOpenHelpStatus");
const startupStatus = document.getElementById("startupStatus");

const animationReplayHandles = new WeakMap();

let viewTransitionRunning = false;

function replayAnimation(element, className) {
  const previousHandle = animationReplayHandles.get(element);
  if (previousHandle) {
    window.cancelAnimationFrame(previousHandle.frameId);
    window.clearTimeout(previousHandle.timeoutId);
    element.classList.remove(previousHandle.className);
  }
  element.classList.remove(className);
  const handle = { className, frameId: 0, timeoutId: 0 };
  handle.frameId = window.requestAnimationFrame(() => {
    element.classList.add(className);
    handle.timeoutId = window.setTimeout(() => {
      if (animationReplayHandles.get(element) !== handle) return;
      element.classList.remove(className);
      animationReplayHandles.delete(element);
    }, 520);
  });
  animationReplayHandles.set(element, handle);
}

function setAnimatedText(element, value, className = "is-updating") {
  if (element.textContent === value) return false;
  element.textContent = value;
  replayAnimation(element, className);
  return true;
}

function waitForViewAnimation(element, fallbackMs) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      element.removeEventListener("animationend", onAnimationEnd);
      window.clearTimeout(timeoutId);
      resolve();
    };
    const onAnimationEnd = (event) => {
      if (event.target === element) finish();
    };
    const timeoutId = window.setTimeout(finish, fallbackMs);
    element.addEventListener("animationend", onAnimationEnd);
  });
}

async function transitionViews(fromView, toView, direction, focusTarget) {
  if (viewTransitionRunning || !toView.hidden) return;
  viewTransitionRunning = true;
  const leavingClass = `is-leaving-${direction}`;
  const enteringClass = `is-entering-${direction}`;
  fromView.inert = true;
  toView.inert = true;
  try {
    fromView.classList.add(leavingClass);
    await waitForViewAnimation(fromView, 240);
    fromView.hidden = true;
    fromView.classList.remove(leavingClass);
    toView.classList.add(enteringClass);
    toView.hidden = false;
    toView.inert = false;
    focusTarget.focus({ preventScroll: true });
    await waitForViewAnimation(toView, 320);
  } finally {
    fromView.classList.remove(leavingClass);
    toView.classList.remove(enteringClass);
    if (!fromView.hidden) fromView.inert = false;
    if (!toView.hidden) toView.inert = false;
    viewTransitionRunning = false;
  }
}

async function openFirstUseGuide() {
  firstUseButton.disabled = true;
  setAnimatedText(startupStatus, "正在启动 Sakura…");
  try {
    await invoke("first_run_start_core");
    window.location.replace("../settings/index.html?guide=first-run");
  } catch (error) {
    firstUseButton.disabled = false;
    showOperationError(startupStatus, "无法启动 Sakura。", error);
  }
}

async function showMacosOpenHelpView() {
  await transitionViews(welcomeView, macosOpenHelpView, "forward", macosOpenHelpBackButton);
}

async function hideMacosOpenHelpView() {
  await transitionViews(macosOpenHelpView, welcomeView, "backward", macosOpenHelpButton);
}

async function runMacosOpenHelpAction(button, command, successMessage) {
  button.disabled = true;
  setAnimatedText(macosOpenHelpStatus, "");
  try {
    await invoke(command);
    setAnimatedText(macosOpenHelpStatus, successMessage);
  } catch (error) {
    showOperationError(macosOpenHelpStatus, "无法完成操作。", error);
  } finally {
    button.disabled = false;
  }
}

async function configureMacosOpenHelp() {
  try {
    const manifest = await invoke("settings_capability_manifest");
    macosOpenHelpButton.hidden = manifest?.sections?.["open-help"]?.status !== "available";
  } catch {
    macosOpenHelpButton.hidden = true;
  }
}

firstUseButton.addEventListener("click", openFirstUseGuide);
macosOpenHelpButton.addEventListener("click", showMacosOpenHelpView);
macosOpenHelpBackButton.addEventListener("click", hideMacosOpenHelpView);
macosOpenSystemSettingsButton.addEventListener("click", () => {
  void runMacosOpenHelpAction(
    macosOpenSystemSettingsButton,
    "settings_macos_open_system_settings",
    "系统设置已打开，请选择“隐私与安全性”。",
  );
});
macosOpenAppleSupportButton.addEventListener("click", () => {
  void runMacosOpenHelpAction(
    macosOpenAppleSupportButton,
    "settings_macos_open_apple_support",
    "已在浏览器中打开 Apple 官方说明。",
  );
});

function showOperationError(target, message, error) {
  setAnimatedText(target, message);
  errorDialog.show({ title: "操作失败", message, error });
}

async function bindWindowLifecycle() {
  const listen = window.__TAURI__?.event?.listen;
  if (!listen) return;
  await listen("sakura://settings-close-requested", () => {
    invoke("resolve_settings_close", { discard: true })
      .catch(error => showOperationError(startupStatus, "无法关闭欢迎页。", error));
  });
  await listen("sakura://settings-exit-requested", (event) => {
    invoke("resolve_settings_exit", { discard: true, revision: event.payload })
      .catch(error => showOperationError(startupStatus, "无法退出 Sakura。", error));
  });
}

async function start() {
  runtimeDiagnostics.markReady({ settings: true });
  await bindWindowLifecycle();
  try {
    const snapshot = await invoke("first_run_guide_get");
    if (snapshot?.completed === true) {
      window.location.replace("../settings/index.html");
      return;
    }
  } catch (error) {
    showOperationError(startupStatus, "无法读取首次启动状态。", error);
  }
  await configureMacosOpenHelp();
  await invoke("reveal_settings_window");
  if (!document.querySelector("dialog[open]")) firstUseButton.focus();
}

start().catch((error) => {
  showOperationError(startupStatus, "欢迎页暂时无法启动。", error);
  invoke("reveal_settings_window").catch(() => {});
});
