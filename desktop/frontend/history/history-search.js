import { createIcon } from "../core/icons.js";
import { createHistoryLoadGuard } from "./history-load-guard.js";
import { projectHistoryEntries } from "./history-presentation.js";

const lower = text => text.replace(/[A-Z]/g, letter => letter.toLowerCase());

function matchingText(entry, query) {
  const payload = entry.payload;
  const texts = entry.kind === "assistant"
    ? payload.segments.flatMap(segment => [segment.text, segment.translation])
    : [payload.text, payload.activity?.details];
  return texts.find(text => text && lower(text).includes(lower(query))) || "";
}

function searchExcerpt(text, query) {
  const index = lower(text).indexOf(lower(query));
  const start = Math.max(0, index - 45);
  const end = Math.min(text.length, Math.max(index, 0) + query.length + 110);
  return `${start ? "…" : ""}${text.slice(start, end)}${end < text.length ? "…" : ""}`;
}

function appendHighlight(element, text, query) {
  const folded = lower(text);
  const needle = lower(query);
  let start = 0;
  let index = folded.indexOf(needle);
  while (index !== -1) {
    element.append(text.slice(start, index));
    const mark = element.ownerDocument.createElement("mark");
    mark.textContent = text.slice(index, index + query.length);
    element.append(mark);
    start = index + query.length;
    index = folded.indexOf(needle, start);
  }
  element.append(text.slice(start));
}

export function createHistorySearch({ document, invoke, getIdentity, getAssistantName, onOpen, onJump, onViewChange }) {
  const trigger = document.querySelector("#search-toggle");
  const reveal = document.querySelector("#history-search");
  const form = reveal.querySelector("form");
  const input = document.querySelector("#history-search-query");
  const panel = document.querySelector("#history-search-results");
  const info = document.querySelector("#history-search-info");
  const summary = document.querySelector("#history-search-summary");
  const results = document.querySelector("#history-search-list");
  const more = document.querySelector("#history-search-more");
  const back = document.querySelector("#history-search-back");
  trigger.append(createIcon(document, "search"));
  document.querySelector("#history-search-submit").append(createIcon(document, "search"));
  const guard = createHistoryLoadGuard();
  let query = "";
  let beforeCursor = null;
  let totalCount = 0;
  let resultsTop = 0;
  let searching = false;

  function clearTarget() {
    for (const row of document.querySelectorAll(".is-search-target")) row.classList.remove("is-search-target");
  }

  function showResults(visible) {
    clearTarget();
    if (!panel.hidden) resultsTop = panel.scrollTop;
    panel.hidden = !visible;
    back.hidden = visible || !query;
    info.hidden = !query;
    onViewChange(visible);
    if (visible) panel.scrollTop = resultsTop;
  }

  function open({ focus = false } = {}) {
    if (!getIdentity()) return;
    onOpen();
    reveal.inert = false;
    reveal.setAttribute("aria-hidden", "false");
    trigger.setAttribute("aria-expanded", "true");
    if (focus) {
      input.focus({ preventScroll: true });
      input.select();
    }
  }

  function clearResults() {
    guard.invalidate();
    query = "";
    beforeCursor = null;
    resultsTop = 0;
    searching = false;
    results.replaceChildren();
    summary.textContent = "";
    panel.setAttribute("aria-busy", "false");
    more.hidden = true;
    showResults(false);
  }

  function reset() {
    clearResults();
    input.value = "";
    reveal.inert = true;
    reveal.setAttribute("aria-hidden", "true");
    trigger.setAttribute("aria-expanded", "false");
  }

  function appendResult(entry) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "history-search-result";
    item.dataset.entryId = entry.entryId;
    const bubble = projectHistoryEntries([entry], { assistantName: getAssistantName() })[0];
    const meta = document.createElement("span");
    meta.className = "history-search-meta";
    const author = document.createElement("span");
    author.className = "history-search-author";
    author.textContent = bubble.roleName;
    const time = document.createElement("time");
    time.dateTime = entry.createdAt;
    time.textContent = bubble.createdAt;
    meta.append(author, time);
    const excerpt = document.createElement("span");
    excerpt.className = "history-search-excerpt";
    appendHighlight(excerpt, searchExcerpt(matchingText(entry, query), query), query);
    item.append(meta, excerpt);
    item.addEventListener("click", async () => {
      if (results.inert) return;
      results.inert = true;
      const revision = guard.invalidate();
      const current = () => guard.isCurrent(revision);
      searching = false;
      panel.setAttribute("aria-busy", "false");
      more.disabled = false;
      summary.textContent = `${totalCount} 条结果`;
      try {
        const target = await onJump(entry.entryId, current);
        if (!current() || !target) return;
        target.tabIndex = -1;
        target.focus({ preventScroll: true });
        for (const row of target.parentElement.children) {
          if (row.dataset.entryId !== entry.entryId) continue;
          row.classList.add("is-search-target");
          row.addEventListener("animationend", event => {
            if (event.animationName === "history-search-target") row.classList.remove("is-search-target");
          });
        }
      } finally {
        results.inert = false;
      }
    });
    results.append(item);
  }

  async function search(append = false) {
    const identity = getIdentity();
    if (!identity || (append && searching)) return;
    const value = input.value.trim();
    if (!value) {
      clearResults();
      return;
    }
    const revision = guard.invalidate();
    const current = () => guard.isCurrent(revision);
    searching = true;
    query = value;
    if (!append) {
      results.replaceChildren();
      resultsTop = 0;
      beforeCursor = null;
    }
    showResults(true);
    panel.setAttribute("aria-busy", "true");
    summary.textContent = "正在搜索…";
    more.disabled = true;
    more.hidden = !append;
    try {
      const page = await invoke("history_page", { request: {
        coreGenerationId: identity.coreGenerationId, characterId: identity.characterId,
        query, beforeCursor: append ? beforeCursor : null,
      } });
      if (!current()) return;
      for (const entry of page.entries.toReversed()) appendResult(entry);
      totalCount = page.totalCount;
      beforeCursor = page.beforeCursor;
      summary.textContent = totalCount ? `${totalCount} 条结果` : "没有找到匹配的聊天记录";
      more.hidden = !beforeCursor;
    } catch (error) {
      if (!current()) return;
      summary.textContent = `搜索失败：${error?.message || error}`;
    } finally {
      if (current()) {
        searching = false;
        panel.setAttribute("aria-busy", "false");
        more.disabled = false;
      }
    }
  }

  trigger.addEventListener("click", event => {
    if (reveal.inert) open({ focus: event.detail === 0 });
    else reset();
  });
  form.addEventListener("submit", event => {
    event.preventDefault();
    void search();
  });
  input.addEventListener("input", clearResults);
  more.addEventListener("click", () => void search(true));
  back.addEventListener("click", () => showResults(true));
  document.addEventListener("pointerdown", clearTarget);
  document.addEventListener("keydown", event => {
    clearTarget();
    if (document.querySelector("dialog[open], :popover-open")) return;
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "f") {
      event.preventDefault();
      open({ focus: true });
    } else if (event.key === "Escape" && !reveal.inert) {
      event.preventDefault();
      reset();
      trigger.focus();
    }
  });
  return { trigger, reset, showContext: () => showResults(false) };
}
