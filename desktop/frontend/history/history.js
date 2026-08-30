import { waitForRuntimeFonts } from "../core/font-loader.js";
import { installDevtoolsShortcutGuard } from "../core/devtools-guard.js";
import { applyTheme } from "../core/theme.js";
import {
  preservePrependScroll,
  projectHistoryEntries,
  validateHistoryPage,
} from "./history-presentation.js";
import {
  createHistoryLoadGuard,
  historyRefreshAction,
  subscribeHistoryRefresh,
} from "./history-load-guard.js";

installDevtoolsShortcutGuard();

const invoke = window.__TAURI__?.core?.invoke;
const listen = window.__TAURI__?.event?.listen;
const shell = document.querySelector(".history-shell");
const count = document.querySelector("#history-count");
const status = document.querySelector("#history-status");
const scroll = document.querySelector("#history-scroll");
const list = document.querySelector("#history-list");
const empty = document.querySelector("#history-empty");
const emptyAssistantName = document.querySelector("#empty-assistant-name");
const loadMore = document.querySelector("#load-more");
const refresh = document.querySelector("#refresh");
const close = document.querySelector("#close");

let loading = false;
let entries = [];
let identity = null;
let assistantName = "Sakura";
let subtitleLanguage = "zh";
const expandedEntries = new Set();
const loadGuard = createHistoryLoadGuard();
let initialReloadPending = false;
const runtimeFontsReady = waitForRuntimeFonts();
let revealPromise = null;

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
  shell.dataset.loading = String(active);
  scroll.setAttribute("aria-busy", String(active));
  refresh.classList.toggle("is-loading", active);
  refresh.setAttribute("aria-busy", String(active));
  refresh.disabled = active;
  loadMore.disabled = active;
  if (message) status.textContent = message;
}

function render({ animateRecent = false, animatedEntryIds = null } = {}) {
  const fragment = document.createDocumentFragment();
  const bubbles = projectHistoryEntries(entries, { assistantName, subtitleLanguage });
  const recentStart = animateRecent ? Math.max(0, bubbles.length - 8) : bubbles.length;
  let enteringIndex = 0;
  for (const [index, item] of bubbles.entries()) {
    const row = document.createElement("article");
    row.className = `history-entry history-entry-${item.align}`;
    row.dataset.entryId = item.entryId;
    if (index >= recentStart || animatedEntryIds?.has(item.entryId)) {
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
    const bubble = document.createElement(item.detailsContent ? "details" : "p");
    bubble.className = `entry-bubble entry-bubble-${item.role}`;
    if (item.detailsContent) {
      bubble.dataset.entryId = item.entryId;
      bubble.open = expandedEntries.has(item.entryId);
      const summary = document.createElement("summary");
      summary.className = "entry-summary";
      summary.textContent = item.content;
      const detailsContent = document.createElement("div");
      detailsContent.className = "entry-details-content";
      detailsContent.textContent = item.detailsContent;
      bubble.append(summary, detailsContent);
      bubble.addEventListener("toggle", () => {
        if (bubble.open) expandedEntries.add(item.entryId);
        else expandedEntries.delete(item.entryId);
      });
    } else {
      bubble.textContent = item.content;
    }
    column.append(bubble);
    row.append(column);
    fragment.append(row);
  }
  list.replaceChildren(fragment);
  empty.hidden = entries.length !== 0;
}

function errorMessage(error) {
  const raw = String(error || "HISTORY_READ_FAILED");
  const code = raw.split("|")[0].split(":")[0];
  if (["HISTORY_IDENTITY_MISMATCH", "HISTORY_CHARACTER_MISMATCH", "TIMELINE_CURSOR_INVALID"].includes(code)) {
    return "当前角色或记录已经变化，请刷新后再查看。";
  }
  if (["HISTORY_NOT_READY", "SETTINGS_CORE_UNAVAILABLE"].includes(code)) {
    return "聊天记录仍在准备，请稍后刷新。";
  }
  return "历史记录读取失败，请稍后刷新。";
}

function applyPage(page) {
  validateHistoryPage(page);
  if (
    identity
    && (page.coreGenerationId !== identity.coreGenerationId || page.characterId !== identity.characterId)
  ) {
    throw new Error("HISTORY_IDENTITY_MISMATCH");
  }
  identity = Object.freeze({
    coreGenerationId: page.coreGenerationId,
    characterId: page.characterId,
    beforeCursor: page.beforeCursor,
  });
  count.textContent = `${page.totalCount} 条记录`;
  loadMore.hidden = !page.hasMore;
}

async function loadInitial() {
  if (loading) {
    initialReloadPending = true;
    return;
  }
  if (!invoke) {
    count.textContent = "读取失败";
    status.textContent = "历史记录界面未连接到 Sakura，请关闭后重新打开。";
    return;
  }
  const revision = loadGuard.begin();
  setLoading(true, "正在读取历史记录…");
  try {
    const bootstrap = await invoke("history_bootstrap");
    if (!loadGuard.isCurrent(revision)) return;
    applyTheme(bootstrap?.themeTokens);
    if (!await revealCurrentInitialLoad(revision)) return;
    assistantName = typeof bootstrap?.assistantName === "string" && bootstrap.assistantName
      ? bootstrap.assistantName
      : "Sakura";
    emptyAssistantName.textContent = assistantName;
    subtitleLanguage = bootstrap?.subtitleLanguage === "ja" ? "ja" : "zh";
    identity = Object.freeze({
      coreGenerationId: bootstrap?.coreGenerationId,
      characterId: bootstrap?.characterId,
      beforeCursor: null,
    });
    const page = validateHistoryPage(await invoke("history_page", {
      request: {
        coreGenerationId: identity.coreGenerationId,
        characterId: identity.characterId,
        beforeCursor: null,
      },
    }));
    if (!loadGuard.isCurrent(revision)) return;
    const firstPaint = entries.length === 0;
    entries = page.entries.slice();
    applyPage(page);
    render({ animateRecent: firstPaint });
    status.textContent = entries.length ? `已显示 ${entries.length} 条最近记录` : "这里还没有对话记录。";
    requestAnimationFrame(() => { scroll.scrollTop = scroll.scrollHeight; });
  } catch (error) {
    status.textContent = errorMessage(error);
    // Bootstrap failures still reveal a usable error state with the product
    // fallback theme instead of leaving an unreachable hidden window. A stale
    // character load leaves revealing to the already-pending current reload.
    if (loadGuard.isCurrent(revision)) {
      void revealCurrentInitialLoad(revision).catch(() => {});
    }
  } finally {
    setLoading(false);
    if (initialReloadPending) {
      initialReloadPending = false;
      void loadInitial();
    }
  }
}

function resetForCharacterSwitch() {
  loadGuard.invalidate();
  entries = [];
  identity = null;
  expandedEntries.clear();
  count.textContent = "正在切换角色…";
  status.textContent = "正在读取新角色的聊天记录…";
  loadMore.hidden = true;
  render();
}

async function loadEarlier() {
  if (!invoke || loading || !identity?.beforeCursor) return;
  const revision = loadGuard.begin();
  const previous = { scrollTop: scroll.scrollTop, scrollHeight: scroll.scrollHeight };
  setLoading(true, "正在读取更早记录…");
  try {
    const page = validateHistoryPage(await invoke("history_page", {
      request: {
        coreGenerationId: identity.coreGenerationId,
        characterId: identity.characterId,
        beforeCursor: identity.beforeCursor,
      },
    }));
    if (!loadGuard.isCurrent(revision)) return;
    applyPage(page);
    const newEntryIds = new Set(page.entries.map((entry) => entry.entryId));
    entries = [...page.entries, ...entries];
    render({ animatedEntryIds: newEntryIds });
    requestAnimationFrame(() => {
      scroll.scrollTop = preservePrependScroll(previous, scroll.scrollHeight);
    });
    status.textContent = `已显示 ${entries.length} 条记录`;
  } catch (error) {
    status.textContent = errorMessage(error);
  } finally {
    setLoading(false);
  }
}

refresh.addEventListener("click", () => void loadInitial());
loadMore.addEventListener("click", () => void loadEarlier());
close.addEventListener("click", () => void invoke?.("close_history_window"));

// Install the native listener before the first history request. Otherwise an
// A -> B reset can race the asynchronous listen() registration and an already
// opened window can paint A after both reset/ready events were missed.
await subscribeHistoryRefresh(listen, (event) => {
  const action = historyRefreshAction(event?.payload);
  if (action.reset) resetForCharacterSwitch();
  if (action.reload) void loadInitial();
});
await loadInitial();
