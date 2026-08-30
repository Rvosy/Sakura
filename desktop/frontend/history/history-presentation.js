const ENTRY_KINDS = new Set(["human", "assistant", "observation", "system"]);
const SCHEDULED_SCREEN_DISPLAY_TEXT = "刚才留意了一下屏幕状态。";
const SCHEDULED_SCREEN_TRIGGER_PREFIX = "定时屏幕观察已提交给对话模型";

function text(value) {
  return typeof value === "string" ? value : "";
}

export function formatHistoryTime(value, locale = "zh-CN") {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return text(value);
  return new Intl.DateTimeFormat(locale, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

function systemRoleName(entry) {
  if (entry.kind === "system") return "系统记录";
  if (entry.origin === "manual_screen") return "屏幕记录";
  if (entry.origin === "scheduled_screen") return "屏幕观察";
  return "观察记录";
}

function scheduledScreenBubble(entries, { formatTime }) {
  const first = entries[0];
  const detailsContent = entries
    .map((entry) => text(entry.payload.text).trim())
    .filter((content) => (
      content
      && content !== SCHEDULED_SCREEN_DISPLAY_TEXT
      && !content.startsWith(SCHEDULED_SCREEN_TRIGGER_PREFIX)
    ))
    .join("\n\n");
  return {
    entryId: first.entryId,
    turnId: first.turnId,
    role: "system",
    group: "system:屏幕观察",
    align: "center",
    roleName: "屏幕观察",
    createdAt: formatTime(first.createdAt),
    content: SCHEDULED_SCREEN_DISPLAY_TEXT,
    detailsContent,
  };
}

function entryBubbles(entry, { assistantName, subtitleLanguage, formatTime }) {
  const createdAt = formatTime(entry.createdAt);
  if (entry.kind === "assistant") {
    return entry.payload.segments.map((segment) => ({
      entryId: entry.entryId,
      turnId: entry.turnId,
      role: "assistant",
      group: "assistant",
      align: "left",
      roleName: assistantName,
      createdAt,
      content:
        subtitleLanguage === "zh" && text(segment.translation).trim()
          ? segment.translation.trim()
          : text(segment.text),
    }));
  }
  if (entry.kind === "human") {
    return [{
      entryId: entry.entryId,
      turnId: entry.turnId,
      role: "human",
      group: "human",
      align: "right",
      roleName: "你",
      createdAt,
      content: text(entry.payload.text),
    }];
  }
  const roleName = systemRoleName(entry);
  return [{
    entryId: entry.entryId,
    turnId: entry.turnId,
    role: "system",
    group: `system:${roleName}`,
    align: "center",
    roleName,
    createdAt,
    content: text(entry.payload.text),
  }];
}

export function projectHistoryEntries(
  entries,
  {
    assistantName = "Sakura",
    subtitleLanguage = "zh",
    formatTime = formatHistoryTime,
  } = {},
) {
  if (!Array.isArray(entries)) throw new Error("HISTORY_ENTRIES_INVALID");
  const scheduledEntriesByTurn = new Map();
  for (const entry of entries) {
    if (!entry || !ENTRY_KINDS.has(entry.kind) || !entry.payload) {
      throw new Error("HISTORY_ENTRY_INVALID");
    }
    if (entry.kind === "observation" && entry.origin === "scheduled_screen") {
      const turnEntries = scheduledEntriesByTurn.get(entry.turnId) || [];
      turnEntries.push(entry);
      scheduledEntriesByTurn.set(entry.turnId, turnEntries);
    }
  }
  const bubbles = [];
  const consumedScheduledTurns = new Set();
  for (const entry of entries) {
    if (entry.kind === "observation" && entry.origin === "scheduled_screen") {
      if (consumedScheduledTurns.has(entry.turnId)) continue;
      consumedScheduledTurns.add(entry.turnId);
      bubbles.push(scheduledScreenBubble(
        scheduledEntriesByTurn.get(entry.turnId),
        { formatTime },
      ));
      continue;
    }
    bubbles.push(...entryBubbles(entry, { assistantName, subtitleLanguage, formatTime }));
  }
  let previousGroup = "";
  return bubbles.map((bubble) => {
    const showMeta = bubble.group !== previousGroup;
    previousGroup = bubble.group;
    return Object.freeze({
      ...bubble,
      showMeta,
      metaText: `${bubble.roleName} · ${bubble.createdAt}`,
    });
  });
}

export function validateHistoryPage(page) {
  if (
    !page
    || page.schemaVersion !== 1
    || typeof page.coreGenerationId !== "string"
    || !page.coreGenerationId
    || typeof page.characterId !== "string"
    || !page.characterId
    || !Number.isSafeInteger(page.totalCount)
    || page.totalCount < 0
    || !Array.isArray(page.entries)
    || typeof page.hasMore !== "boolean"
    || (page.hasMore ? typeof page.beforeCursor !== "string" || !page.beforeCursor : page.beforeCursor !== null)
  ) {
    throw new Error("HISTORY_PAGE_INVALID");
  }
  return page;
}

export function preservePrependScroll(previous, currentScrollHeight) {
  const top = Number(previous?.scrollTop) || 0;
  const height = Number(previous?.scrollHeight) || 0;
  return Math.max(0, top + Math.max(0, Number(currentScrollHeight) - height));
}
