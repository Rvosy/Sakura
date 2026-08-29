import { normalizedSelection, selectionAccepted } from "./capture-selection.js";
import { installDevtoolsShortcutGuard } from "../core/devtools-guard.js";

installDevtoolsShortcutGuard();

const invoke = window.__TAURI__?.core?.invoke;
const box = document.querySelector("#selection-box");
const params = new URLSearchParams(window.location.search);
const sessionId = params.get("sessionId") || "";
const monitorId = Number(params.get("monitorId"));
let start = null;
let current = null;
let submitting = false;
let activePointerId = null;

function selection() {
  return normalizedSelection(start, current);
}

function render() {
  const rect = selection();
  if (!rect) return;
  box.hidden = false;
  box.style.left = `${rect.x}px`;
  box.style.top = `${rect.y}px`;
  box.style.width = `${rect.width}px`;
  box.style.height = `${rect.height}px`;
}

async function cancel() {
  if (submitting || !invoke || !sessionId) return;
  submitting = true;
  await invoke("cancel_screen_capture", { payload: { sessionId } }).catch(() => {});
}

window.addEventListener("pointerdown", (event) => {
  if (event.button === 2) {
    event.preventDefault();
    void cancel();
    return;
  }
  if (event.button !== 0 || submitting) return;
  activePointerId = event.pointerId;
  document.body.setPointerCapture?.(event.pointerId);
  start = { x: event.clientX, y: event.clientY };
  current = start;
  render();
});

window.addEventListener("pointermove", (event) => {
  if (!start || submitting || event.pointerId !== activePointerId) return;
  current = { x: event.clientX, y: event.clientY };
  render();
});

window.addEventListener("pointerup", async (event) => {
  if (!start || submitting || event.pointerId !== activePointerId
      || !invoke || !Number.isSafeInteger(monitorId)) return;
  document.body.releasePointerCapture?.(event.pointerId);
  activePointerId = null;
  current = { x: event.clientX, y: event.clientY };
  const rect = selection();
  if (!selectionAccepted(rect)) {
    start = null;
    current = null;
    box.hidden = true;
    return;
  }
  submitting = true;
  document.body.dataset.capturing = "true";
  await invoke("capture_selected_region", {
    payload: { sessionId, monitorId, ...rect },
  }).catch(() => {});
});

window.addEventListener("pointercancel", (event) => {
  if (event.pointerId !== activePointerId || submitting) return;
  activePointerId = null;
  start = null;
  current = null;
  box.hidden = true;
});

window.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    event.preventDefault();
    void cancel();
  }
});
window.addEventListener("contextmenu", (event) => event.preventDefault());
