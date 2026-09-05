import { enhanceSelect, refreshSelect, closeSelects } from "../settings/select-control.js";
import { waitForRuntimeFonts } from "../core/font-loader.js";
import { installDevtoolsShortcutGuard } from "../core/devtools-guard.js";
import { applyTheme } from "../core/theme.js";
import {
  applyViewerSnapshot,
  collapseViewerRecords,
  filterViewerRecords,
  validateViewerBootstrap,
  validateViewerSnapshot,
  viewerCopyText,
  viewerInlineSummary,
  viewerItemKey,
  viewerProblemCount,
  viewerScopeCounts,
  viewerPluginName,
  viewerPluginOptions,
} from "./runtime-log-presentation.js";

installDevtoolsShortcutGuard();

const invoke = window.__TAURI__?.core?.invoke;
const listen = window.__TAURI__?.event?.listen;
const POLL_INTERVAL_MS = 700;

const summary = document.querySelector("#log-summary");
const status = document.querySelector("#log-status");
const scroll = document.querySelector("#log-scroll");
const list = document.querySelector("#log-list");
const empty = document.querySelector("#log-empty");
const emptyTitle = document.querySelector("#log-empty-title");
const emptyHint = document.querySelector("#log-empty-hint");
const softwareCount = document.querySelector("#count-software");
const pluginCount = document.querySelector("#count-plugins");
const pluginFilter = document.querySelector("#plugin-filter");
const pluginFilterControl = document.querySelector("#plugin-filter-control");
enhanceSelect(pluginFilter);
const saveStatus = document.querySelector("#log-save-status");
const ttsCount = document.querySelector("#count-tts");
const autoScroll = document.querySelector("#auto-scroll");
const refresh = document.querySelector("#refresh");
const copy = document.querySelector("#copy");
const close = document.querySelector("#close");
const tabs = [...document.querySelectorAll(".log-tab")];
const problemFilter = document.querySelector("#problem-filter");
const problemCount = document.querySelector("#count-problems");

let viewerState = null;
let activeScope = "software";
let viewMode = "all";
let selectedItemKey = null;
const disclosureStates = new Map();
let pollActive = false;
let bootstrapActive = false;
let requestGeneration = 0;
const runtimeFontsReady = waitForRuntimeFonts();
let revealPromise = null;

function revealInitialWindow() {
  if (!invoke) return Promise.resolve();
  if (!revealPromise) {
    revealPromise = runtimeFontsReady.then(() => invoke("reveal_runtime_log_viewer"));
  }
  return revealPromise;
}

window.addEventListener("contextmenu", (event) => event.preventDefault());

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
    ...(item.record.pluginId ? [["插件标识", item.record.pluginId]] : []),
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

function fillRecordMain(main, item) {
  main.replaceChildren();
  main.className = "record-main";
  const headline = document.createElement("div");
  headline.className = "record-headline";
  appendText(headline, "record-time", item.record.timestamp);
  appendText(headline, "record-category", item.record.category === "PLUGIN" ? "插件" : item.record.category);
  if (item.record.pluginId) appendText(headline, "record-plugin", viewerPluginName(item.record));
  if (["warning", "error"].includes(item.record.severity)) {
    appendText(headline, `record-level record-level-${item.record.severity}`, item.record.severity === "error" ? "错误" : "提醒");
  }
  appendText(headline, "record-message", item.record.message);
  const inline = viewerInlineSummary(item.record);
  if (inline) appendText(headline, "record-inline", inline);
  if (item.repeatCount > 1) appendText(headline, "record-repeat", `×${item.repeatCount}`);
  main.append(headline);
  if (item.record.description) appendText(main, "record-description", item.record.description);
}

function recordMain(item, hasDetails = false) {
  const main = document.createElement(hasDetails ? "summary" : "div");
  fillRecordMain(main, item);
  return main;
}

function selectCard(card) {
  selectedItemKey = card.dataset.itemKey;
  copy.disabled = false;
  copy.dataset.copyText = viewerCopyText(card.viewerItem);
  for (const candidate of list.querySelectorAll(".log-record")) {
    candidate.setAttribute("aria-selected", String(candidate === card));
  }
}

function createRecordCard(item, itemKey) {
  const card = document.createElement("article");
  card.className = `log-record severity-${item.record.severity}`;
  card.dataset.itemKey = itemKey;
  card.dataset.collapseKey = item.collapseKey;
  const hasDetails = Boolean(
    item.record.severity !== "info"
    || item.record.details.length
    || item.record.correlationId
    || item.record.pluginId,
  );

  if (hasDetails) {
    const disclosure = document.createElement("details");
    disclosure.className = "record-disclosure";
    disclosure.open = disclosureStates.get(itemKey) ?? item.record.severity === "error";
    disclosure.append(recordMain(item, true), detailsPanel(item));
    disclosure.addEventListener("toggle", () => disclosureStates.set(itemKey, disclosure.open));
    card.append(disclosure);
  } else {
    card.tabIndex = 0;
    card.append(recordMain(item));
  }

  card.addEventListener("click", () => selectCard(card));
  card.addEventListener("focusin", () => selectCard(card));
  card.addEventListener("animationend", () => card.classList.remove("is-new", "is-updated"));
  return card;
}

function runtimeItemKey(item, scopeName) {
  return `${viewerState.runId}:${viewerItemKey(item, scopeName)}`;
}

function updateRecordCard(card, item, itemKey, newAfterSequence) {
  card.viewerItem = item;
  card.setAttribute("aria-selected", String(itemKey === selectedItemKey));

  const latestSequence = String(item.record.sequence);
  const repeatCount = String(item.repeatCount);
  const changed = card.dataset.latestSequence !== latestSequence
    || card.dataset.repeatCount !== repeatCount;
  if (!changed) return;

  fillRecordMain(card.querySelector(".record-main"), item);
  card.dataset.latestSequence = latestSequence;
  card.dataset.repeatCount = repeatCount;
  card.classList.remove("is-new", "is-updated");
  if (item.record.sequence > newAfterSequence) {
    // Force a repeat animation to restart without recreating the card or its disclosure.
    if (card.isConnected) void card.offsetWidth;
    card.classList.add(item.repeatCount > 1 ? "is-updated" : "is-new");
  }
}

function pruneViewState() {
  const records = viewerState?.records || [];
  const currentKeys = new Set();
  for (const scopeName of ["software", "tts", "plugins"]) {
    for (const item of collapseViewerRecords(records, scopeName)) {
      currentKeys.add(runtimeItemKey(item, scopeName));
    }
  }
  for (const key of disclosureStates.keys()) {
    if (!currentKeys.has(key)) disclosureStates.delete(key);
  }
  if (selectedItemKey && !currentKeys.has(selectedItemKey)) selectedItemKey = null;
}

function render(newAfterSequence = Number.MAX_SAFE_INTEGER) {
  const records = viewerState?.records || [];
  const counts = viewerScopeCounts(records);
  softwareCount.textContent = String(counts.software);
  ttsCount.textContent = String(counts.tts);
  pluginCount.textContent = String(counts.plugins);
  pluginFilterControl.hidden = activeScope !== "plugins";
  if (pluginFilterControl.hidden) closeSelects(pluginFilterControl);
  const options = viewerPluginOptions(records);
  // Preserve both identity and display name when the last record leaves the ring.
  if (pluginFilter.value && !options.some(option => option.id === pluginFilter.value)) {
    options.push({ id: pluginFilter.value, name: pluginFilter.selectedOptions[0].textContent });
  }
  if (pluginFilter.dataset.options !== JSON.stringify(options)) {
    closeSelects(pluginFilterControl);
    const selected = pluginFilter.value;
    pluginFilter.replaceChildren(new Option("全部插件", ""), ...options.map(({ id, name }) => new Option(name, id)));
    pluginFilter.value = selected;
    pluginFilter.dataset.options = JSON.stringify(options);
    refreshSelect(pluginFilter);
  }
  const pluginId = activeScope === "plugins" ? pluginFilter.value : "";
  const failures = viewerState?.failedFiles || [];
  saveStatus.hidden = failures.length === 0;
  saveStatus.textContent = failures.length ? `${failures.map((name) => name === "plugins" ? "插件" : "软件").join("、")}日志文件保存失败，窗口中的记录仍可查看。` : "";
  const problems = viewerProblemCount(records, activeScope, pluginId);
  problemCount.textContent = String(problems);
  const filtered = filterViewerRecords(records, activeScope, viewMode, pluginId);
  const visible = collapseViewerRecords(filtered, activeScope);
  const scopeLabel = { software: "软件", tts: "TTS", plugins: "插件" }[activeScope];
  summary.textContent = viewMode === "problems"
    ? `${scopeLabel}：${visible.length} 条问题记录`
    : `${scopeLabel}：${visible.length} 条记录，${problems} 个问题`;

  const existingCards = new Map(
    [...list.querySelectorAll(":scope > .log-record")].map((card) => [card.dataset.itemKey, card]),
  );
  const desiredCards = [];
  let selectedItem = null;
  for (const item of visible) {
    const itemKey = runtimeItemKey(item, activeScope);
    let card = existingCards.get(itemKey);
    if (card?.dataset.collapseKey !== item.collapseKey) {
      card?.remove();
      card = null;
    }
    if (!card) card = createRecordCard(item, itemKey);
    existingCards.delete(itemKey);
    updateRecordCard(card, item, itemKey, newAfterSequence);
    if (itemKey === selectedItemKey) selectedItem = item;
    desiredCards.push(card);
  }
  for (const [index, card] of desiredCards.entries()) {
    if (list.children[index] !== card) list.insertBefore(card, list.children[index] || null);
  }
  for (const card of existingCards.values()) card.remove();
  empty.hidden = visible.length !== 0;
  emptyTitle.textContent = viewMode === "problems" ? "本次运行暂未发现问题" : "当前还没有可显示的记录";
  emptyHint.textContent = viewMode === "problems"
    ? "新的提醒或错误会出现在这里。"
    : "新的运行事件、提醒或错误会出现在这里。";
  if (!selectedItem) {
    selectedItemKey = null;
    copy.disabled = true;
  }
  copy.dataset.copyText = selectedItem ? viewerCopyText(selectedItem) : "";
}

function applySnapshot(snapshot, { animateAfter = Number.MAX_SAFE_INTEGER } = {}) {
  viewerState = applyViewerSnapshot(viewerState, validateViewerSnapshot(snapshot));
  pruneViewState();
  render(animateAfter);
}

function scrollToLatest() {
  if (!autoScroll.checked) return;
  requestAnimationFrame(() => { scroll.scrollTop = scroll.scrollHeight; });
}

async function bootstrap() {
  if (!invoke || bootstrapActive) {
    if (!invoke) status.textContent = "运行日志界面未连接到 Sakura，请关闭后重新打开。";
    return;
  }
  bootstrapActive = true;
  const generation = ++requestGeneration;
  refresh.disabled = true;
  status.textContent = "正在读取本次运行记录…";
  try {
    const result = validateViewerBootstrap(await invoke("runtime_log_viewer_bootstrap"));
    applyTheme(result.themeTokens);
    await revealInitialWindow();
    const previousRunId = viewerState?.runId;
    if (previousRunId && previousRunId !== result.snapshot.runId) {
      selectedItemKey = null;
      disclosureStates.clear();
    }
    viewerState = null;
    applySnapshot(result.snapshot);
    status.textContent = result.snapshot.records.length
      ? "已显示本次启动以来可观察到的运行事件。"
      : "等待新的运行事件。";
    scrollToLatest();
  } catch {
    status.textContent = "运行日志读取失败，请稍后刷新。";
  } finally {
    // Keep the viewer reachable even when the initial snapshot fails; in that
    // case its already-defined product fallback theme is the correct first frame.
    void revealInitialWindow().catch(() => {});
    if (generation === requestGeneration) refresh.disabled = false;
    bootstrapActive = false;
  }
}

async function poll() {
  if (!invoke || !viewerState || pollActive || bootstrapActive) return;
  pollActive = true;
  const generation = requestGeneration;
  const previousLatest = viewerState.latestSequence;
  try {
    const snapshot = await invoke("runtime_log_viewer_snapshot", { afterSequence: previousLatest });
    if (generation !== requestGeneration) return;
    applySnapshot(snapshot, { animateAfter: previousLatest });
    if (viewerState.latestSequence > previousLatest) {
      status.textContent = "已收到新的运行事件。";
      scrollToLatest();
    }
  } catch {
    if (generation === requestGeneration) {
      status.textContent = "日志更新暂时中断，Sakura 会继续尝试连接。";
    }
  } finally {
    pollActive = false;
  }
}

for (const tab of tabs) {
  tab.addEventListener("click", () => {
    activeScope = tab.dataset.scope;
    selectedItemKey = null;
    for (const candidate of tabs) {
      const active = candidate === tab;
      candidate.classList.toggle("is-active", active);
      candidate.setAttribute("aria-selected", String(active));
    }
    render();
    scrollToLatest();
  });
}

pluginFilter.addEventListener("change", () => { selectedItemKey = null; render(); });

problemFilter.addEventListener("change", () => {
  viewMode = problemFilter.checked ? "problems" : "all";
  selectedItemKey = null;
  render();
  scrollToLatest();
});

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

await bootstrap();
window.setInterval(() => void poll(), POLL_INTERVAL_MS);
