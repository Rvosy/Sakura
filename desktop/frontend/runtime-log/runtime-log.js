import { waitForRuntimeFonts } from "../core/font-loader.js";
import { applyTheme } from "../core/theme.js";
import {
  applyViewerSnapshot,
  collapseViewerRecords,
  validateViewerBootstrap,
  validateViewerSnapshot,
  viewerCopyText,
  viewerInlineSummary,
  viewerScopeCounts,
} from "./runtime-log-presentation.js";

const invoke = window.__TAURI__?.core?.invoke;
const listen = window.__TAURI__?.event?.listen;
const POLL_INTERVAL_MS = 700;

const liveSignal = document.querySelector("#live-signal");
const liveText = document.querySelector("#live-text");
const summary = document.querySelector("#log-summary");
const status = document.querySelector("#log-status");
const scroll = document.querySelector("#log-scroll");
const list = document.querySelector("#log-list");
const empty = document.querySelector("#log-empty");
const softwareCount = document.querySelector("#count-software");
const ttsCount = document.querySelector("#count-tts");
const autoScroll = document.querySelector("#auto-scroll");
const refresh = document.querySelector("#refresh");
const copy = document.querySelector("#copy");
const close = document.querySelector("#close");
const tabs = [...document.querySelectorAll(".log-tab")];

let viewerState = null;
let activeScope = "software";
let selectedCollapseKey = null;
let pollActive = false;

function setConnected(connected) {
  liveSignal.classList.toggle("is-connected", connected);
  liveText.textContent = connected ? "本次运行 · 实时更新" : "更新暂时中断";
}

function appendText(parent, className, text) {
  const element = document.createElement("span");
  element.className = className;
  element.textContent = text;
  parent.append(element);
  return element;
}

function detailsPanel(item) {
  const panel = document.createElement("dl");
  panel.className = "record-details";
  const pairs = [
    ["事件代码", item.record.eventCode],
    ...item.record.details.map((detail) => [detail.label, detail.value]),
    ...(item.record.correlationId ? [["关联编号", item.record.correlationId]] : []),
  ];
  for (const [label, value] of pairs) {
    const row = document.createElement("div");
    row.className = "detail-row";
    const term = document.createElement("dt");
    term.textContent = label;
    const description = document.createElement("dd");
    description.textContent = value;
    row.append(term, description);
    panel.append(row);
  }
  return panel;
}

function recordMain(item) {
  const main = document.createElement("div");
  main.className = "record-main";
  appendText(main, "record-time", item.record.timestamp);
  appendText(main, "record-category", item.record.category);
  if (item.record.severity !== "info") {
    appendText(main, `record-level record-level-${item.record.severity}`, item.record.severity === "error" ? "错误" : "提醒");
  }
  appendText(main, "record-message", item.record.message);
  const inline = viewerInlineSummary(item.record);
  if (inline) appendText(main, "record-inline", inline);
  if (item.repeatCount > 1) appendText(main, "record-repeat", `×${item.repeatCount}`);
  return main;
}

function render(newAfterSequence = Number.MAX_SAFE_INTEGER) {
  const records = viewerState?.records || [];
  const counts = viewerScopeCounts(records);
  softwareCount.textContent = String(counts.software);
  ttsCount.textContent = String(counts.tts);
  const visible = collapseViewerRecords(records, activeScope);
  summary.textContent = `${activeScope === "software" ? "软件" : "TTS"}：${visible.length} 条可见记录`;

  const fragment = document.createDocumentFragment();
  let selectedItem = null;
  for (const item of visible) {
    const card = document.createElement("article");
    card.className = `log-record severity-${item.record.severity}`;
    card.dataset.collapseKey = item.collapseKey;
    card.tabIndex = 0;
    card.setAttribute("aria-selected", String(item.collapseKey === selectedCollapseKey));
    if (item.record.sequence > newAfterSequence) {
      card.classList.add(item.repeatCount > 1 ? "is-updated" : "is-new");
    }

    const signal = document.createElement("span");
    signal.className = "record-signal";
    signal.setAttribute("aria-hidden", "true");
    card.append(signal, recordMain(item));

    if (item.record.severity !== "info") {
      const disclosure = document.createElement("details");
      disclosure.className = "record-disclosure";
      disclosure.open = item.record.severity === "error";
      const disclosureLabel = document.createElement("summary");
      disclosureLabel.textContent = item.record.severity === "error" ? "错误详情" : "查看详情";
      disclosure.append(disclosureLabel, detailsPanel(item));
      card.append(disclosure);
    }

    const select = () => {
      selectedCollapseKey = item.collapseKey;
      copy.disabled = false;
      copy.dataset.copyText = viewerCopyText(item);
      for (const candidate of list.querySelectorAll(".log-record")) {
        candidate.setAttribute("aria-selected", String(candidate === card));
      }
    };
    card.addEventListener("click", select);
    card.addEventListener("focus", select);
    if (item.collapseKey === selectedCollapseKey) selectedItem = item;
    fragment.append(card);
  }
  list.replaceChildren(fragment);
  empty.hidden = visible.length !== 0;
  if (!selectedItem) {
    selectedCollapseKey = null;
    copy.disabled = true;
  }
  copy.dataset.copyText = selectedItem ? viewerCopyText(selectedItem) : "";
}

function applySnapshot(snapshot, { animateAfter = Number.MAX_SAFE_INTEGER } = {}) {
  viewerState = applyViewerSnapshot(viewerState, validateViewerSnapshot(snapshot));
  render(animateAfter);
}

function scrollToLatest() {
  if (!autoScroll.checked) return;
  requestAnimationFrame(() => { scroll.scrollTop = scroll.scrollHeight; });
}

async function bootstrap() {
  if (!invoke) {
    setConnected(false);
    status.textContent = "运行日志界面未连接到 Sakura，请关闭后重新打开。";
    return;
  }
  refresh.disabled = true;
  status.textContent = "正在读取本次运行记录…";
  try {
    const result = validateViewerBootstrap(await invoke("runtime_log_viewer_bootstrap"));
    applyTheme(result.themeTokens);
    viewerState = null;
    selectedCollapseKey = null;
    applySnapshot(result.snapshot);
    setConnected(true);
    status.textContent = result.snapshot.records.length
      ? "已显示本次启动以来可观察到的运行事件。"
      : "等待新的运行事件。";
    scrollToLatest();
  } catch {
    setConnected(false);
    status.textContent = "运行日志读取失败，请稍后刷新。";
  } finally {
    refresh.disabled = false;
  }
}

async function poll() {
  if (!invoke || !viewerState || pollActive) return;
  pollActive = true;
  const previousLatest = viewerState.latestSequence;
  try {
    const snapshot = await invoke("runtime_log_viewer_snapshot", { afterSequence: previousLatest });
    applySnapshot(snapshot, { animateAfter: previousLatest });
    setConnected(true);
    if (viewerState.latestSequence > previousLatest) {
      status.textContent = "已收到新的运行事件。";
      scrollToLatest();
    }
  } catch {
    setConnected(false);
    status.textContent = "日志更新暂时中断，Sakura 会继续尝试连接。";
  } finally {
    pollActive = false;
  }
}

for (const tab of tabs) {
  tab.addEventListener("click", () => {
    activeScope = tab.dataset.scope;
    selectedCollapseKey = null;
    for (const candidate of tabs) {
      const active = candidate === tab;
      candidate.classList.toggle("is-active", active);
      candidate.setAttribute("aria-selected", String(active));
    }
    render();
    scrollToLatest();
  });
}

scroll.addEventListener("scroll", (event) => {
  if (!event.isTrusted || !autoScroll.checked) return;
  const distanceFromBottom = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight;
  if (distanceFromBottom > 72) {
    autoScroll.checked = false;
    status.textContent = "已暂停自动滚动，勾选后可继续跟随最新记录。";
  }
});

autoScroll.addEventListener("change", () => {
  if (autoScroll.checked) {
    status.textContent = "已继续跟随最新记录。";
    scrollToLatest();
  }
});

refresh.addEventListener("click", () => void bootstrap());
copy.addEventListener("click", async () => {
  const text = copy.dataset.copyText || "";
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    status.textContent = "已复制选中的日志详情。";
  } catch {
    status.textContent = "复制失败，请重新选择后再试。";
  }
});
close.addEventListener("click", () => void invoke?.("close_runtime_log_viewer"));

if (listen) {
  try {
    void Promise.resolve(listen("sakura://runtime-log-refresh-requested", () => void bootstrap())).catch(() => {});
    void Promise.resolve(listen("sakura://character-appearance-changed", (event) => {
      const theme = event?.payload?.values?.themeTokens;
      if (theme) applyTheme(theme);
    })).catch(() => {});
  } catch {
    // Event subscriptions are optional; polling and manual refresh remain available.
  }
}

await Promise.all([waitForRuntimeFonts(), bootstrap()]);
window.setInterval(() => void poll(), POLL_INTERVAL_MS);
