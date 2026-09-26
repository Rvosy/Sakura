import { createState } from "../settings-demo/data.js";
import { readThemeColors, colorEditsToLegacy } from "./theme-model.js";

const frame = document.getElementById("settings");
const loading = document.getElementById("loading");
let state = createState("portrait");
const options = { version: "proposed", mode: "light", ...readThemeColors(state.appearance.themeTokens), page: "settings" };
window.__SAKURA_VISUAL_REVIEW__ = {
  options,
  update(next) {
    Object.assign(options, next);
    const changed = [];
    for (const [field,value] of Object.entries(colorEditsToLegacy(next))) {
      const input = frame.contentDocument?.querySelector(`[data-theme-field="${field}"]`);
      if (input && input.value !== value) { input.value = value; changed.push(input); }
    }
    for (const input of changed) input.dispatchEvent(new frame.contentWindow.Event("input", { bubbles: true }));
    setVersion(options.version);
    for (const id of ["settings", "history", "logs"]) document.getElementById(id).hidden = options.page !== id;
    for (const target of [window, ...Array.from(document.querySelectorAll("iframe"), iframe => iframe.contentWindow)]) target?.dispatchEvent(new target.Event("review-theme"));
  },
  get state() { return state; },
  ready() { loading.hidden = true; },
  failed(message) { loading.textContent = message; loading.hidden = false; },
  close() {
    const message = document.createElement("p");
    message.textContent = "设置已关闭";
    const reopen = document.createElement("button");
    reopen.textContent = "重新打开设置";
    reopen.onclick = load;
    loading.replaceChildren(message, reopen);
    loading.hidden = false;
  },
};

function setVersion(next) {
  options.version = next;
  for (const id of ["original", "proposed"]) {
    document.getElementById(id).setAttribute("aria-pressed", String(id === options.version));
  }
  for (const iframe of document.querySelectorAll("iframe")) {
    const doc = iframe.contentDocument;
    for (const sheet of doc?.querySelectorAll("[data-proposed-sheet]") || []) sheet.disabled = options.version === "original";
    const production = doc?.getElementById("production-style");
    if (production) production.disabled = options.version !== "original";
    if (doc) doc.documentElement.dataset.reviewVersion = options.version;
    if (iframe.id === "settings") {
      const heading = doc?.querySelector(".page-head");
      const scroll = doc?.querySelector(".page-scroll");
      const detail = doc?.querySelector(".detail-card");
      if (heading && scroll && detail) {
        if (options.version === "proposed" && heading.parentElement !== scroll) {
          scroll.prepend(heading);
          scroll.scrollTop = 0;
        } else if (options.version === "original" && heading.parentElement === scroll) {
          detail.insertBefore(heading, scroll);
          scroll.scrollTop = 0;
        }
      }
    }
  }
}

async function load() {
  loading.textContent = "正在载入设置…";
  loading.hidden = false;
  try {
    const response = await fetch("../../settings/index.html");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const doc = new DOMParser().parseFromString(await response.text(), "text/html");
    doc.querySelector('link[href="./styles.css"]').id = "production-style";
    const base = doc.createElement("base");
    base.href = new URL("../../settings/", location.href).href;
    doc.head.prepend(base);
    doc.querySelector('script[src="./settings.js"]').remove();
    for (const file of ["support.css", "layered-settings.css", "dist/components.css", "visual.css"]) {
      const link = doc.createElement("link");
      link.rel = "stylesheet";
      link.href = new URL(file, location.href).href;
      if (file !== "support.css") link.setAttribute("data-proposed-sheet", "");
      doc.head.append(link);
    }
    const script = doc.createElement("script");
    script.type = "module";
    script.src = new URL("frame.js", location.href).href;
    doc.body.append(script);
    frame.srcdoc = "<!doctype html>\n" + doc.documentElement.outerHTML;
    frame.onload = () => window.__SAKURA_VISUAL_REVIEW__.update({});
  } catch (error) {
    window.__SAKURA_VISUAL_REVIEW__.failed("设置载入失败：" + error.message);
  }
}

for (const id of ["original", "proposed"]) document.getElementById(id).onclick = () => window.__SAKURA_VISUAL_REVIEW__.update({version:id});
document.getElementById("reset").onclick = () => { state = createState("portrait"); Object.assign(options, readThemeColors(state.appearance.themeTokens)); void load(); };
await load();
await import("./dist/components.js");
const historyResponse = await fetch("../../history/index.html");
const historyDoc = new DOMParser().parseFromString(await historyResponse.text(), "text/html");
const historyBase = historyDoc.createElement("base");
historyBase.href = new URL("../../history/", location.href).href;
historyDoc.head.prepend(historyBase);
historyDoc.querySelector('link[href="./styles.css"]').id = "production-style";
historyDoc.querySelector('script[src="./history.js"]').remove();
historyDoc.body.dataset.demoPage = "history";
for (const path of ["support.css", "layered-history.css", "dist/components.css", "history.css"]) {
  const link = historyDoc.createElement("link"); link.rel = "stylesheet"; link.href = new URL(path, location.href).href;
  if (path !== "support.css") link.setAttribute("data-proposed-sheet", "");
  historyDoc.head.append(link);
}
const historyScript = historyDoc.createElement("script"); historyScript.type = "module"; historyScript.src = new URL("history-frame.js", location.href).href;
historyDoc.body.append(historyScript);
const historyFrame = document.getElementById("history");
historyFrame.onload = () => window.__SAKURA_VISUAL_REVIEW__.update({});
historyFrame.srcdoc = "<!doctype html>\n" + historyDoc.documentElement.outerHTML;

const logResponse = await fetch("../../runtime-log/index.html");
const logDoc = new DOMParser().parseFromString(await logResponse.text(), "text/html");
const logBase = logDoc.createElement("base");
logBase.href = new URL("../../runtime-log/", location.href).href;
logDoc.head.prepend(logBase);
logDoc.querySelector('link[href="./styles.css"]').id = "production-style";
logDoc.querySelector('script[src="./runtime-log.js"]').remove();
logDoc.body.dataset.demoPage = "logs";
for (const path of ["support.css", "layered-logs.css", "dist/components.css", "logs.css"]) {
  const link = logDoc.createElement("link");
  link.rel = "stylesheet";
  link.href = new URL(path, location.href).href;
  if (path !== "support.css") link.setAttribute("data-proposed-sheet", "");
  logDoc.head.append(link);
}
const logScript = logDoc.createElement("script");
logScript.type = "module";
logScript.src = new URL("logs-frame.js", location.href).href;
logDoc.body.append(logScript);
const logFrame = document.getElementById("logs");
logFrame.onload = () => window.__SAKURA_VISUAL_REVIEW__.update({});
logFrame.srcdoc = "<!doctype html>\n" + logDoc.documentElement.outerHTML;
