import { applyTheme } from "../core/theme.js";
import { waitForRuntimeFonts } from "../core/font-loader.js";
import { createErrorDialog } from "../core/error-dialog.js";
import { installDevtoolsShortcutGuard } from "../core/devtools-guard.js";

installDevtoolsShortcutGuard();
applyTheme();
const invoke = window.__TAURI__.core.invoke;
const errorDialog = createErrorDialog({ document });
const $ = id => document.getElementById(id);
let ready = false;
let closing = false;
let unlisten;
const content = $("content");
const contentInner = document.querySelector(".updates-content-inner");
async function fitWindow() {
  const style = getComputedStyle(content);
  const height = Math.ceil(
    document.querySelector(".updates-heading").getBoundingClientRect().height
    + contentInner.getBoundingClientRect().height
    + parseFloat(style.paddingTop) + parseFloat(style.paddingBottom)
    + document.querySelector(".updates-actions").getBoundingClientRect().height,
  );
  await invoke("resize_whats_new", { height });
}
const contentObserver = new ResizeObserver(() => {
  if (ready) void fitWindow().catch(error => report("无法调整更新摘要窗口大小。", error));
});
contentObserver.observe(contentInner);

function report(message, error) { errorDialog.show({ title: "更新摘要", message, error }); }
async function openDetails(version = null) {
  try { await invoke("whats_new_open_details", { version }); }
  catch (error) { report("无法打开更新日志。", error); }
}
async function close() {
  if (closing) return;
  closing = true;
  $("close").disabled = true;
  try { await invoke("close_whats_new", { acknowledge: ready }); }
  catch (error) { report("无法关闭更新摘要。", error); }
  finally { closing = false; $("close").disabled = false; }
}
$("close").addEventListener("click", close);
$("full").addEventListener("click", () => openDetails());
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && !document.querySelector("dialog[open]")) { event.preventDefault(); void close(); }
});

function renderVersion(entry, showDetails) {
  const section = document.createElement("section");
  section.className = "version";
  const heading = document.createElement("div");
  heading.className = "version-heading";
  const title = document.createElement("h2");
  title.textContent = `v${entry.version}`;
  heading.append(title);
  if (showDetails) {
    const details = document.createElement("button");
    details.type = "button";
    details.className = "text-button";
    details.innerHTML = '详情 <span class="sakura-icon icon-external-link" aria-hidden="true"></span>';
    details.setAttribute("aria-label", `查看 v${entry.version} 详细更新`);
    details.addEventListener("click", () => openDetails(entry.version));
    heading.append(details);
  }
  const list = document.createElement("ul");
  for (const item of entry.items) {
    const row = document.createElement("li");
    const kind = document.createElement("span");
    kind.className = `kind kind-${item.type}`;
    kind.textContent = { new: "新增", improve: "改进", fix: "修复" }[item.type];
    const text = document.createElement("span");
    text.className = "update-text";
    text.textContent = item.text;
    row.append(kind, text);
    list.append(row);
  }
  section.append(heading, list);
  return section;
}

async function start() {
  unlisten = await window.__TAURI__.event.listen("sakura://whats-new-close-requested", close);
  const startup = new URLSearchParams(location.search).has("startup");
  const snapshot = await invoke("whats_new_bootstrap", { startup });
  applyTheme(snapshot.themeTokens);
  $("title").textContent = startup ? `已更新至 v${snapshot.version}` : "近期更新";
  $("subtitle").textContent = startup && snapshot.seenVersion ? `自 v${snapshot.seenVersion} 以来的更新` : `V${snapshot.version}`;
  $("subtitle").hidden = false;
  const entries = snapshot.entries;
  $("status").hidden = entries.length > 0;
  $("status").textContent = "此版本暂无更新摘要，可查看完整更新日志。";
  const renderEntry = entry => renderVersion(entry, entries.length > 1);
  $("versions").replaceChildren(...entries.slice(0, 3).map(renderEntry));
  if (entries.length > 3) {
    $("earlier").hidden = false;
    $("earlier").textContent = `展开更早的 ${entries.length - 3} 个版本`;
    $("earlier").addEventListener("click", () => {
      const expanded = $("earlier").getAttribute("aria-expanded") === "true";
      const scrollTop = content.scrollTop;
      if (expanded) [...$("versions").children].slice(3).forEach(element => element.remove());
      else $("versions").append(...entries.slice(3).map(renderEntry));
      $("earlier").setAttribute("aria-expanded", String(!expanded));
      $("earlier").textContent = expanded ? `展开更早的 ${entries.length - 3} 个版本` : "收起更早版本";
      content.scrollTop = scrollTop;
    });
  }
  await waitForRuntimeFonts();
  await fitWindow();
  ready = true;
  await invoke("reveal_whats_new");
}
window.addEventListener("pagehide", () => { contentObserver.disconnect(); unlisten?.(); errorDialog.dispose(); }, { once: true });
start().catch(async error => {
  applyTheme();
  $("status").textContent = "无法读取更新摘要。";
  await invoke("reveal_whats_new");
  report("无法读取更新摘要。", error);
});
