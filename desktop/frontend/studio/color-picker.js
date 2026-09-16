const invoke = window.__TAURI__?.core?.invoke;
const params = new URLSearchParams(window.location.search);
const sessionId = params.get("sessionId") || "";
const monitorId = Number(params.get("monitorId"));
let submitting = false;

async function cancel() {
  if (submitting || !invoke || !sessionId) return;
  submitting = true;
  await invoke("studio_color_cancel", { payload: { sessionId } }).catch(() => {});
}

window.addEventListener("pointerdown", async (event) => {
  if (event.button === 2) {
    event.preventDefault();
    await cancel();
    return;
  }
  if (event.button !== 0 || submitting || !invoke || !Number.isSafeInteger(monitorId)) return;
  submitting = true;
  document.body.dataset.submitting = "true";
  await invoke("studio_color_pick", {
    payload: { sessionId, monitorId, x: event.clientX, y: event.clientY },
  }).catch(() => {});
});

window.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    event.preventDefault();
    void cancel();
  }
});
window.addEventListener("contextmenu", (event) => event.preventDefault());
