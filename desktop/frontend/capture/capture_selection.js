const invoke = window.__TAURI__?.core?.invoke;
const box = document.querySelector("#selection-box");
const params = new URLSearchParams(window.location.search);
const captureSessionId = params.get("captureSessionId") || "";
let start = null;
let current = null;
let submitting = false;

function selection() {
  if (!start || !current) return null;
  const x = Math.min(start.x, current.x);
  const y = Math.min(start.y, current.y);
  return {
    x,
    y,
    width: Math.abs(current.x - start.x),
    height: Math.abs(current.y - start.y),
  };
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
  if (submitting || !invoke) return;
  submitting = true;
  await invoke("cancel_capture_overlay", { captureSessionId }).catch(() => {});
}

window.addEventListener("pointerdown", (event) => {
  if (event.button !== 0 || submitting) return;
  start = { x: event.clientX, y: event.clientY };
  current = start;
  render();
});

window.addEventListener("pointermove", (event) => {
  if (!start || submitting) return;
  current = { x: event.clientX, y: event.clientY };
  render();
});

window.addEventListener("pointerup", async (event) => {
  if (!start || submitting || !invoke) return;
  current = { x: event.clientX, y: event.clientY };
  const rect = selection();
  if (!rect || rect.width < 4 || rect.height < 4) {
    start = null;
    current = null;
    box.hidden = true;
    return;
  }
  submitting = true;
  document.body.classList.add("capturing");
  try {
    await invoke("capture_selected_region", { captureSessionId, ...rect });
  } catch (_error) {
    document.body.classList.remove("capturing");
    submitting = false;
  }
});

window.addEventListener("keydown", (event) => {
  if (event.key === "Escape") cancel();
});
