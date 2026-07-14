const invoke = window.__TAURI__?.core?.invoke;
const listen = window.__TAURI__?.event?.listen;

const phase = document.getElementById("repair-phase");
const code = document.getElementById("repair-code");
const message = document.getElementById("repair-message");
const refreshButton = document.getElementById("refresh-status");
const diagnosticsButton = document.getElementById("open-diagnostics");

function render(status) {
  phase.textContent = status?.phase || "unknown";
  code.textContent = status?.diagnostic?.code || "BRAIN_BASE_HEALTH_FAILED";
  message.textContent = status?.diagnostic?.message
    || "Brain Host 尚未通过基础健康检查。请确认 runtime/python.exe 和应用文件完整。";
}

async function refresh() {
  if (!invoke) {
    throw new Error("Tauri bridge unavailable");
  }
  refreshButton.disabled = true;
  try {
    render(await invoke("brain_status"));
  } catch (error) {
    phase.textContent = "unavailable";
    code.textContent = "STATUS_UNAVAILABLE";
    message.textContent = String(error);
  } finally {
    refreshButton.disabled = false;
  }
}

refreshButton.addEventListener("click", refresh);
diagnosticsButton.addEventListener("click", () => {
  invoke?.("open_diagnostics_window").catch((error) => {
    message.textContent = `诊断窗口打开失败：${error}`;
  });
});

listen?.("sakura://brain-status", ({ payload }) => render(payload));
refresh();
