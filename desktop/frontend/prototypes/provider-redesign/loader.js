import { THEMES } from "./data.js";

const frame = document.getElementById("settingsFrame");
const loading = document.getElementById("loadingOverlay");
const stage = document.querySelector(".review-stage");
const scenarioSelect = document.getElementById("scenarioSelect");

const state = {
  mode: "proposed",
  size: "default",
  theme: "light",
  scenario: "typical",
  resetCounter: 0,
};

function syncReviewControls() {
  document.getElementById("modeProposed").setAttribute("aria-pressed", String(state.mode === "proposed"));
  document.getElementById("modeOriginal").setAttribute("aria-pressed", String(state.mode === "original"));
  document.getElementById("sizeDefault").setAttribute("aria-pressed", String(state.size === "default"));
  document.getElementById("sizeMin").setAttribute("aria-pressed", String(state.size === "min"));
  document.getElementById("sizeFill").setAttribute("aria-pressed", String(state.size === "fill"));
  document.getElementById("themeLight").setAttribute("aria-pressed", String(state.theme === "light"));
  document.getElementById("themeDark").setAttribute("aria-pressed", String(state.theme === "dark"));
  stage.dataset.size = state.size;
  document.body.dataset.theme = state.theme;
}

window.__PROVIDER_REDESIGN_DEMO__ = {
  get mode() { return state.mode; },
  get theme() { return state.theme; },
  get themeTokens() { return THEMES[state.theme]; },
  get scenario() { return state.scenario; },
  get resetCounter() { return state.resetCounter; },
  ready() {
    loading.hidden = true;
  },
  failed(message) {
    loading.textContent = `Demo 载入失败：${message}`;
    loading.hidden = false;
  },
  close() {
    loading.replaceChildren();
    const box = document.createElement("div");
    box.className = "closed-state";
    const text = document.createElement("p");
    text.textContent = "设置窗口已关闭";
    const reopen = document.createElement("button");
    reopen.type = "button";
    reopen.textContent = "重新打开";
    reopen.addEventListener("click", () => loadFrame());
    box.append(text, reopen);
    loading.append(box);
    loading.hidden = false;
  },
  toggleMaximize() {
    state.size = state.size === "fill" ? "default" : "fill";
    syncReviewControls();
    return state.size === "fill";
  },
};

async function loadFrame() {
  loading.textContent = "正在载入设置界面…";
  loading.hidden = false;
  try {
    const response = await fetch("../../settings/index.html", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const doc = new DOMParser().parseFromString(await response.text(), "text/html");
    const base = doc.createElement("base");
    base.href = new URL("../../settings/", location.href).href;
    doc.head.prepend(base);
    doc.title = "Sakura 设置 · 模型服务";
    doc.querySelector('script[src="./settings.js"]')?.remove();

    const style = doc.createElement("link");
    style.rel = "stylesheet";
    style.href = new URL("./provider-redesign.css", location.href).href;
    doc.head.append(style);

    const script = doc.createElement("script");
    script.type = "module";
    script.src = new URL("./frame.js", location.href).href;
    doc.body.append(script);

    frame.srcdoc = "<!doctype html>\n" + doc.documentElement.outerHTML;
  } catch (error) {
    window.__PROVIDER_REDESIGN_DEMO__.failed(error?.message || String(error));
  }
}

document.getElementById("modeProposed").addEventListener("click", () => {
  if (state.mode === "proposed") return;
  state.mode = "proposed";
  syncReviewControls();
  void loadFrame();
});

document.getElementById("modeOriginal").addEventListener("click", () => {
  if (state.mode === "original") return;
  state.mode = "original";
  syncReviewControls();
  void loadFrame();
});

for (const [id, size] of [["sizeDefault", "default"], ["sizeMin", "min"], ["sizeFill", "fill"]]) {
  document.getElementById(id).addEventListener("click", () => {
    state.size = size;
    syncReviewControls();
    frame.contentWindow?.__DEMO_SYNC_WINDOW__?.(size === "fill");
  });
}

for (const [id, theme] of [["themeLight", "light"], ["themeDark", "dark"]]) {
  document.getElementById(id).addEventListener("click", () => {
    state.theme = theme;
    syncReviewControls();
    frame.contentWindow?.__DEMO_APPLY_THEME__?.(THEMES[theme]);
  });
}

scenarioSelect.addEventListener("change", () => {
  state.scenario = scenarioSelect.value;
  state.resetCounter += 1;
  void loadFrame();
});

document.getElementById("resetButton").addEventListener("click", () => {
  state.resetCounter += 1;
  void loadFrame();
});

syncReviewControls();
await loadFrame();
