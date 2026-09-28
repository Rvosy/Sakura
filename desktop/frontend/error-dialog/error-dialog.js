import { createErrorDialog } from "../core/error-dialog.js";
import { applyTheme } from "../core/theme.js";
import { waitForRuntimeFonts } from "../core/font-loader.js";

const { invoke } = window.__TAURI__.core;
let disposed = false;
let closing = false;
const errors = createErrorDialog({ document, onClose: closeWindow });
let revision = 0;
const fonts = waitForRuntimeFonts();

async function closeWindow() {
  if (disposed || closing) return;
  closing = true;
  ++revision;
  try {
    await invoke("close_error_dialog");
  } catch (error) {
    if (disposed) return;
    closing = false;
    errors.show({ title: "无法关闭错误窗口", error });
  }
}

async function refresh() {
  if (disposed || closing) return;
  const current = ++revision;
  try {
    const snapshot = await invoke("error_dialog_bootstrap");
    await fonts;
    if (disposed || closing || current !== revision) return;
    applyTheme(snapshot.themeTokens);
    errors.show({ title: snapshot.title, message: snapshot.message, error: snapshot.details });
    await invoke("reveal_error_dialog");
  } catch (error) {
    if (disposed || closing || current !== revision) return;
    throw error;
  }
}
const unlisten = await window.__TAURI__.event.listen("sakura://error-dialog-updated", () => { void refresh(); });
window.addEventListener("pagehide", () => { disposed = true; ++revision; unlisten(); errors.dispose(); }, { once: true });
await refresh();
