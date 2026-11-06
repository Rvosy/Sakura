import { waitForRuntimeFonts } from "../core/font-loader.js";
import { createDataTransferMenu } from "../core/data-transfer.js";
import { createDateRangePicker, formatDate } from "../core/date-range-picker.js";
import { createHistorySearch } from "./history-search.js";
import { createIcon, installClickIconMotion } from "../core/icons.js";
import { installDevtoolsShortcutGuard } from "../core/devtools-guard.js";
import { applyTheme } from "../core/theme.js";
import { createPlaybackActionIndicator } from "../audio/playback-action-indicator.js";
import { createErrorDialog } from "../core/error-dialog.js";
import { renderSubtitleText } from "../pet/multilingual-text.js";
import { createTtsControllerHost } from "../audio/tts-controller.js";
import {
  preservePrependScroll,
  projectHistoryEntries,
} from "./history-presentation.js";
import {
  createHistoryLoadGuard,
  historyRefreshAction,
  subscribeHistoryRefresh,
} from "./history-load-guard.js";

installDevtoolsShortcutGuard();
installClickIconMotion(document);

const invoke = window.__TAURI__?.core?.invoke;
const listen = window.__TAURI__?.event?.listen;
const character = document.querySelector("#history-character");
const status = document.querySelector("#history-status");
const scroll = document.querySelector("#history-scroll");
const list = document.querySelector("#history-list");
const empty = document.querySelector("#history-empty");
const loadMore = document.querySelector("#load-more");
const loadNewer = document.querySelector("#load-newer");
const latest = document.querySelector("#history-latest");
latest.prepend(createIcon(document, "chevron-down"));
const refresh = document.createElement("button");
refresh.id = "refresh";
const close = document.createElement("button");
const errors = createErrorDialog({ document });
window.addEventListener("pagehide", () => errors.dispose(), { once: true });

let loading = false;
let closed = false;
let entries = [];
let identity = null;
let lastScrollTop = 0;
const datePicker = createDateRangePicker({
  document, id: "history-jump", singleDate: true,
  isCurrent: () => !closed && identity !== null,
  getInitialDate: () => {
    const top = scroll.getBoundingClientRect().top;
    const visible = [...list.children].find(row => row.getBoundingClientRect().bottom > top);
    const entry = entries.find(item => item.entryId === visible?.dataset.entryId);
    return formatDate(new Date(entry?.createdAt || Date.now()));
  },
  loadDates: loadHistoryDates,
  onChange: () => void jumpToRecord({ fromDate: datePicker.getValue().startDate }),
});
datePicker.trigger.disabled = true;
document.querySelector(".history-head-actions").append(datePicker.element);
const operations = createDataTransferMenu({ document, invoke,
  characterId: () => identity?.characterId,
  identityKey: () => `${identity?.coreGenerationId}/${identity?.characterId}`,
  transfer: payload => invoke("history_transfer", { payload: { ...payload, expectedGenerationId: identity.coreGenerationId } }),
  loadDates: loadHistoryDates,
  onImported: () => loadInitial(),
});
const operationsMenu = operations.querySelector('[role="menu"]');
for (const [button, icon, label] of [[refresh, "refresh-cw", "刷新记录"], [close, "x", "关闭窗口"]]) {
  button.type = "button";
  button.setAttribute("role", "menuitem");
  button.append(createIcon(document, icon), label);
  button.addEventListener("click", () => operationsMenu.hidePopover());
}
operationsMenu.prepend(refresh);
operationsMenu.append(close);
document.querySelector(".history-head-actions").append(operations);

async function loadHistoryDates(startDate, endDate) {
  const result = await invoke("history_dates", { payload: {
    expectedGenerationId: identity.coreGenerationId, expectedCharacterId: identity.characterId,
    startDate, endDate,
  } });
  return result.dates;
}

let assistantName = "Sakura";
let subtitleLanguage = "zh";
const expandedEntries = new Set();
const loadGuard = createHistoryLoadGuard();
const languageGuard = createHistoryLoadGuard();
const search = createHistorySearch({
  document, invoke, getIdentity: () => identity, getAssistantName: () => assistantName,
  onOpen: () => datePicker.dispose(),
  onJump: (entryId, current) => jumpToRecord({ fromEntryId: entryId }, current),
  onViewChange: visible => {
    scroll.inert = visible;
    scroll.style.visibility = visible ? "hidden" : "";
    if (visible) status.textContent = "";
    updateLatestButton();
  },
});
let initialReloadPending = false;
const runtimeFontsReady = waitForRuntimeFonts();
let revealPromise = null;
let playbackState = { state: "idle" };
const playbackIndicators = new Map();
function clearPlaybackIndicators() {
  for (const indicator of playbackIndicators.values()) indicator.dispose();
  playbackIndicators.clear();
}
window.addEventListener("pagehide", clearPlaybackIndicators, { once: true });
const ttsController = invoke && listen ? createTtsControllerHost({
  invoke, listen,
  onDiagnostic: error => errors.show({
    title: "语音播放失败",
    message: playbackErrorMessage(error),
    error,
  }),
  onPlaybackState: state => {
    playbackState = state;
    updatePlaybackButtons();
  },
}) : null;

function updatePlaybackButtons() {
  for (const [button, indicator] of playbackIndicators) {
    const active = playbackState.state !== "idle"
      && playbackState.historyEntryId === button.dataset.historyEntryId
      && playbackState.segmentIndex === Number(button.dataset.segmentIndex);
    indicator.setState(active ? playbackState.state : "idle");
  }
}

function revealInitialWindow() {
  if (!invoke) return Promise.resolve();
  if (!revealPromise) {
    revealPromise = runtimeFontsReady.then(() => invoke("reveal_history_window"));
  }
  return revealPromise;
}

async function revealCurrentInitialLoad(revision) {
  await runtimeFontsReady;
  if (!loadGuard.isCurrent(revision)) return false;
  await revealInitialWindow();
  return true;
}

function setLoading(active, message) {
  loading = active;
  scroll.setAttribute("aria-busy", String(active));
  refresh.classList.toggle("is-loading", active);
  refresh.setAttribute("aria-busy", String(active));
  refresh.disabled = active;
  latest.disabled = active;
  loadMore.disabled = active;
  loadNewer.disabled = active;
  datePicker.trigger.disabled = active || !identity;
  search.trigger.disabled = active || !identity;
  if (active) status.textContent = message || "";
}

function updateLatestButton() {
  latest.hidden = scroll.inert || !entries.length || (!identity?.afterCursor
    && scroll.scrollHeight - scroll.clientHeight - scroll.scrollTop < 120);
}

function render({ animateRecent = false } = {}) {
  clearPlaybackIndicators();
  const fragment = document.createDocumentFragment();
  const pendingText = [];
  const bubbles = projectHistoryEntries(entries, { assistantName, subtitleLanguage });
  const recentStart = animateRecent ? Math.max(0, bubbles.length - 8) : bubbles.length;
  let enteringIndex = 0;
  for (const [index, item] of bubbles.entries()) {
    const row = document.createElement("article");
    row.className = `history-entry history-entry-${item.align}`;
    row.dataset.entryId = item.entryId;
    if (index >= recentStart) {
      row.classList.add("is-entering");
      row.style.setProperty("--entry-delay", `${Math.min(enteringIndex, 7) * 18}ms`);
      enteringIndex += 1;
    }

    const column = document.createElement("div");
    column.className = `entry-column entry-column-${item.role}`;
    if (item.showMeta) {
      const meta = document.createElement("p");
      meta.className = "entry-meta";
      meta.textContent = item.metaText;
      column.append(meta);
    }
    const expandable = item.detailsContent || item.actionLabel;
    const bubble = document.createElement(expandable ? "details" : "p");
    bubble.className = `entry-bubble entry-bubble-${item.role}`;
    if (expandable) {
      bubble.dataset.entryId = item.entryId;
      bubble.open = expandedEntries.has(item.entryId);
      const summary = document.createElement("summary");
      summary.className = "entry-summary";
      summary.textContent = item.content;
      const detailsContent = document.createElement("div");
      detailsContent.className = "entry-details-content";
      detailsContent.textContent = item.detailsContent;
      if (item.actionLabel) {
        const open = document.createElement("button");
        open.type = "button";
        open.className = "button button-secondary";
        open.textContent = item.actionLabel;
        open.addEventListener("click", async () => {
          open.disabled = true;
          try {
            await invoke("history_open_record", { request: {
              coreGenerationId: identity.coreGenerationId,
              characterId: identity.characterId, entryId: item.entryId,
            } });
          } catch (error) {
            errors.show({ title: "无法打开记录", message: String(error).includes("HISTORY_PLUGIN_UNAVAILABLE")
              ? "请启用对应插件后再试。" : "记录未能打开，请查看错误详情。", error });
          } finally {
            open.disabled = false;
          }
        });
        detailsContent.append(document.createElement("br"), open);
      }
      bubble.append(summary, detailsContent);
      bubble.addEventListener("toggle", () => {
        if (bubble.open) expandedEntries.add(item.entryId);
        else expandedEntries.delete(item.entryId);
      });
    } else {
      if (item.subtitleTracks?.length === 2) bubble.classList.add("entry-bubble-bilingual");
      pendingText.push([bubble, item]);
    }
    if (ttsController && item.role === "assistant" && !item.suppressTts) {
      const segment = document.createElement("div");
      segment.className = "entry-segment";
      if (item.subtitleTracks?.length === 2) segment.classList.add("entry-segment-bilingual");
      const read = document.createElement("button");
      read.type = "button";
      read.className = "entry-read-button";
      const icon = document.createElement("span");
      icon.className = "sakura-morph-icon";
      icon.setAttribute("aria-hidden", "true");
      read.append(icon);
      playbackIndicators.set(read, createPlaybackActionIndicator({ button: read }));
      read.dataset.historyEntryId = item.historyEntryId;
      read.dataset.segmentIndex = String(item.segmentIndex);
      read.addEventListener("click", () => {
        const active = playbackState.state !== "idle" && playbackState.historyEntryId === item.historyEntryId
          && playbackState.segmentIndex === item.segmentIndex;
        if (active) ttsController.stop();
        else void ttsController.playHistorySegment(item);
      });
      segment.append(bubble, read);
      column.append(segment);
    } else {
      column.append(bubble);
    }
    row.append(column);
    fragment.append(row);
  }
  list.replaceChildren(fragment);
  updatePlaybackButtons();
  for (const [bubble, item] of pendingText) renderSubtitleText(bubble, item.content, item.subtitleTracks, item.subtitleTracks, subtitleLanguage);
  empty.hidden = entries.length !== 0;
}

let historyWidth = list.clientWidth;
const historyResize = new ResizeObserver(() => {
  if (list.clientWidth === historyWidth) return;
  historyWidth = list.clientWidth;
  render();
  updateLatestButton();
});
historyResize.observe(list);
window.addEventListener("pagehide", () => historyResize.disconnect(), { once: true });

function errorMessage(error) {
  const raw = String(error || "HISTORY_READ_FAILED");
  const code = raw.split("|")[0].split(":")[0];
  if (["HISTORY_IDENTITY_MISMATCH", "HISTORY_CHARACTER_MISMATCH", "TIMELINE_CURSOR_INVALID"].includes(code)) {
    return "角色或记录已变化，请刷新。";
  }
  if (["HISTORY_NOT_READY", "SETTINGS_CORE_UNAVAILABLE"].includes(code)) {
    return "聊天记录仍在准备，请稍后刷新。";
  }
  if (code === "TIMELINE_ENTRY_NOT_FOUND") return "这条记录已不存在，请重新搜索。";
  return "聊天记录读取失败，请稍后刷新。";
}

function playbackErrorMessage(error) {
  const code = String(error?.code || error?.message || error).split(/[|:\n]/, 1)[0];
  return {
    TTS_SERVICE_UNAVAILABLE: "当前角色的语音服务不可用。",
    TTS_DISABLED: "当前角色的语音已关闭。",
    TTS_SYNTHESIS_TIMEOUT: "语音生成超时。",
    TTS_SEGMENT_NOT_AUTHORIZED: "这条回复已不可用，请刷新聊天记录。",
    AUDIO_RECORDING_INVALID: "这条语音缓存无法读取。",
  }[code] || "这条回复暂时无法播放。";
}

function applyPage(page, direction) {
  if (
    identity
    && (page.coreGenerationId !== identity.coreGenerationId || page.characterId !== identity.characterId)
  ) {
    throw new Error("HISTORY_IDENTITY_MISMATCH");
  }
  identity = Object.freeze({
    coreGenerationId: page.coreGenerationId,
    characterId: page.characterId,
    beforeCursor: direction === "after" ? identity.beforeCursor : page.beforeCursor,
    afterCursor: direction === "before" ? identity.afterCursor : page.afterCursor,
  });
  loadMore.hidden = !identity.beforeCursor;
  loadNewer.hidden = !identity.afterCursor;
}

function moveScroll(top) {
  scroll.scrollTop = top;
  lastScrollTop = scroll.scrollTop;
  updateLatestButton();
}

async function loadInitial() {
  if (closed) return;
  if (loading) {
    initialReloadPending = true;
    return;
  }
  if (!invoke) {
    status.textContent = "读取失败";
    errors.show({ title: "无法读取聊天记录", message: "无法连接 Sakura，请重新打开聊天记录。", error: "HISTORY_HOST_UNAVAILABLE" });
    return;
  }
  const revision = loadGuard.begin();
  const languageRevision = languageGuard.begin();
  search.reset();
  setLoading(true, "正在读取聊天记录…");
  try {
    const bootstrap = await invoke("history_bootstrap");
    if (!loadGuard.isCurrent(revision)) return;
    applyTheme(bootstrap?.themeTokens);
    if (!await revealCurrentInitialLoad(revision)) return;
    assistantName = typeof bootstrap?.assistantName === "string" && bootstrap.assistantName
      ? bootstrap.assistantName
      : "Sakura";
    character.textContent = assistantName;
    character.title = assistantName;
    if (languageGuard.isCurrent(languageRevision)) {
      subtitleLanguage = ["zh", "ja", "bilingual", "bilingual_ja"].includes(bootstrap?.subtitleLanguage) ? bootstrap.subtitleLanguage : "zh";
    }
    identity = Object.freeze({
      coreGenerationId: bootstrap?.coreGenerationId,
      characterId: bootstrap?.characterId,
      beforeCursor: null,
      afterCursor: null,
    });
    const page = await invoke("history_page", {
      request: {
        coreGenerationId: identity.coreGenerationId,
        characterId: identity.characterId,
        beforeCursor: null,
      },
    });
    if (!loadGuard.isCurrent(revision)) return;
    await ttsController?.rebind(page.coreGenerationId);
    if (!loadGuard.isCurrent(revision)) return;
    const firstPaint = entries.length === 0;
    entries = page.entries.slice();
    applyPage(page);
    render({ animateRecent: firstPaint });
    status.textContent = "";
    requestAnimationFrame(() => {
      if (loadGuard.isCurrent(revision)) moveScroll(scroll.scrollHeight);
    });
  } catch (error) {
    if (!loadGuard.isCurrent(revision)) return;
    status.textContent = "读取失败";
    errors.show({ title: "无法读取聊天记录", message: errorMessage(error), error });
    // Bootstrap failures still reveal a usable error state with the product
    // fallback theme instead of leaving an unreachable hidden window. A stale
    // character load leaves revealing to the already-pending current reload.
    if (loadGuard.isCurrent(revision)) {
      void revealCurrentInitialLoad(revision).catch(() => {});
    }
  } finally {
    finishLoading();
  }
}

function finishLoading() {
  if (closed) return;
  setLoading(false);
  if (initialReloadPending) {
    initialReloadPending = false;
    void loadInitial();
  }
}

function resetForCharacterSwitch() {
  ttsController?.cancel();
  loadGuard.invalidate();
  entries = [];
  identity = null;
  search.reset();
  search.trigger.disabled = true;
  datePicker.dispose();
  datePicker.trigger.disabled = true;
  expandedEntries.clear();
  character.textContent = "";
  character.title = "";
  status.textContent = "正在读取新角色的聊天记录…";
  loadMore.hidden = true;
  loadNewer.hidden = true;
  render();
}

async function jumpToRecord(anchor, isCurrent = () => true) {
  if (!invoke || loading || !identity) return;
  const date = anchor.fromDate;
  const revision = loadGuard.begin();
  const current = () => loadGuard.isCurrent(revision) && isCurrent();
  setLoading(true, date ? `正在读取 ${date} 的聊天记录…` : "正在定位聊天记录…");
  try {
    const page = await invoke("history_page", {
      request: {
        coreGenerationId: identity.coreGenerationId,
        characterId: identity.characterId,
        beforeCursor: null,
        ...anchor,
      },
    });
    if (!current()) return;
    const first = page.entries[0];
    if (date && (!first || formatDate(new Date(first.createdAt)) !== date)) {
      status.textContent = `${date} 没有聊天记录，请选择其他日期。`;
      return;
    }
    applyPage(page);
    entries = page.entries.slice();
    if (anchor.fromEntryId) expandedEntries.add(anchor.fromEntryId);
    render();
    search.showContext();
    status.textContent = "";
    await new Promise(requestAnimationFrame);
    if (!current()) return;
    const target = list.firstElementChild;
    moveScroll(scroll.scrollTop + target.getBoundingClientRect().top
      - scroll.getBoundingClientRect().top - scroll.clientTop - parseFloat(getComputedStyle(scroll).paddingTop));
    return target;
  } catch (error) {
    if (!current()) return;
    status.textContent = "记录定位失败";
    errors.show({ title: "无法定位聊天记录", message: errorMessage(error), error });
  } finally {
    finishLoading();
  }
}

async function loadAdjacent(direction) {
  const cursorKey = `${direction}Cursor`;
  if (!invoke || loading || !identity?.[cursorKey]) return;
  const earlier = direction === "before";
  const label = earlier ? "更早" : "后续";
  const button = earlier ? loadMore : loadNewer;
  const revision = loadGuard.begin();
  setLoading(true);
  button.textContent = "正在加载…";
  try {
    const page = await invoke("history_page", {
      request: {
        coreGenerationId: identity.coreGenerationId,
        characterId: identity.characterId,
        [cursorKey]: identity[cursorKey],
      },
    });
    if (!loadGuard.isCurrent(revision)) return;
    const previous = { scrollTop: scroll.scrollTop, scrollHeight: scroll.scrollHeight };
    applyPage(page, direction);
    entries = earlier ? [...page.entries, ...entries] : [...entries, ...page.entries];
    render();
    moveScroll(earlier ? preservePrependScroll(previous, scroll.scrollHeight) : previous.scrollTop);
  } catch (error) {
    if (!loadGuard.isCurrent(revision)) return;
    status.textContent = `${label}记录未加载`;
    errors.show({ title: `无法读取${label}记录`, message: errorMessage(error), error });
  } finally {
    button.textContent = `加载${label}记录`;
    finishLoading();
  }
}

refresh.addEventListener("click", () => void loadInitial());
latest.addEventListener("click", () => void loadInitial());
loadMore.addEventListener("click", () => void loadAdjacent("before"));
loadNewer.addEventListener("click", () => void loadAdjacent("after"));
scroll.addEventListener("scroll", () => {
  const top = scroll.scrollTop;
  if (!loading) {
    if (top < lastScrollTop && top < 120) void loadAdjacent("before");
    else if (top > lastScrollTop && scroll.scrollHeight - scroll.clientHeight - top < 120) void loadAdjacent("after");
  }
  lastScrollTop = top;
  updateLatestButton();
});
close.addEventListener("click", () => {
  loadGuard.invalidate();
  search.reset();
  datePicker.dispose();
  ttsController?.cancel();
  void invoke?.("close_history_window");
});
window.addEventListener("pagehide", () => {
  closed = true;
  loadGuard.invalidate();
  search.reset();
  datePicker.dispose();
  ttsController?.dispose();
}, { once: true });

// Install the native listener before the first history request. Otherwise an
// A -> B reset can race the asynchronous listen() registration and an already
// opened window can paint A after both reset/ready events were missed.
await subscribeHistoryRefresh(listen, (event) => {
  const action = historyRefreshAction(event?.payload);
  if (action.reset) resetForCharacterSwitch();
  if (action.reload) void loadInitial();
});
await listen?.("sakura://subtitle-language-changed", (event) => {
  if (!["zh", "ja", "bilingual", "bilingual_ja"].includes(event?.payload)) return;
  languageGuard.invalidate();
  subtitleLanguage = event.payload;
  render();
});
await loadInitial();
