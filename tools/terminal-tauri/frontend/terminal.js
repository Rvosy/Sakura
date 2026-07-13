const invoke = window.__TAURI__.core.invoke;
const listen = window.__TAURI__.event.listen;

const container = document.getElementById("terminal");
const statusText = document.getElementById("terminalStatus");
const statusDot = document.getElementById("statusDot");
const stopButton = document.getElementById("stopButton");
const terminal = new window.Terminal({
  cursorBlink: true,
  convertEol: false,
  fontFamily: '"SFMono-Regular", Consolas, "Liberation Mono", monospace',
  fontSize: 13,
  scrollback: 5000,
  theme: {
    background: "#181818",
    foreground: "#e8e8e8",
    cursor: "#e8e8e8",
    selectionBackground: "#4b5563",
  },
});
const fitAddon = new window.FitAddon.FitAddon();
terminal.loadAddon(fitAddon);
terminal.open(container);

let sessionId = "";
let cursor = 0;
let running = false;
let resizeTimer = null;

function encodeUtf8(value) {
  const bytes = new TextEncoder().encode(value);
  let binary = "";
  bytes.forEach((byte) => { binary += String.fromCharCode(byte); });
  return window.btoa(binary);
}

function decodeBytes(value) {
  const binary = window.atob(value || "");
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

function setState(state, exitCode = null) {
  running = state === "running";
  stopButton.disabled = !running;
  statusDot.classList.toggle("is-running", running);
  statusDot.classList.toggle("is-failed", state === "failed");
  if (running) {
    statusText.textContent = "运行中";
  } else if (state === "exited") {
    statusText.textContent = exitCode === null ? "已结束" : `已结束 · ${exitCode}`;
  } else if (state === "stopped") {
    statusText.textContent = "已停止";
  } else if (state === "failed") {
    statusText.textContent = "连接失败";
  } else {
    statusText.textContent = "等待会话";
  }
}

function applyResult(result) {
  if (!result) return;
  sessionId = result.session_id || sessionId;
  cursor = Number(result.cursor || cursor);
  if (result.output_b64) {
    terminal.write(decodeBytes(result.output_b64));
  }
  setState(result.state, result.exit_code ?? null);
}

async function snapshot() {
  if (!sessionId) return;
  try {
    applyResult(await invoke("terminal_snapshot", { cursor, maxBytes: 16384 }));
  } catch {
    setState("failed");
  }
}

async function resize() {
  fitAddon.fit();
  if (!sessionId) return;
  try {
    await invoke("terminal_resize", { columns: terminal.cols, rows: terminal.rows });
  } catch {
    // The session may have ended between the resize event and this call.
  }
}

terminal.onData(async (data) => {
  if (!running) return;
  try {
    applyResult(await invoke("terminal_write", { dataB64: encodeUtf8(data) }));
  } catch {
    setState("failed");
  }
});

stopButton.addEventListener("click", async () => {
  if (!running) return;
  stopButton.disabled = true;
  try {
    applyResult(await invoke("terminal_stop"));
  } catch {
    setState("failed");
  }
});

window.addEventListener("resize", () => {
  window.clearTimeout(resizeTimer);
  resizeTimer = window.setTimeout(resize, 80);
});

async function load() {
  const request = await invoke("load_request");
  sessionId = request.session_id || "";
  await listen("sakura://terminal-output", (event) => {
    const payload = event.payload || {};
    if (sessionId && payload.session_id !== sessionId) return;
    sessionId = payload.session_id || sessionId;
    cursor = Math.max(cursor, Number(payload.cursor || 0));
    terminal.write(decodeBytes(payload.data_b64));
    setState("running");
  });
  await listen("sakura://terminal-state", (event) => {
    const payload = event.payload || {};
    if (sessionId && payload.session_id !== sessionId) return;
    sessionId = payload.session_id || sessionId;
    setState(payload.state || "exited", payload.exit_code ?? null);
  });
  await resize();
  await snapshot();
  terminal.focus();
}

load().catch(() => setState("failed"));
