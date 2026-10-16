import { selectSegmentText, selectSegmentTracks } from "../pet/typewriter.js";

const SCHEDULED_SCREEN_DISPLAY_TEXT = "刚才留意了一下屏幕状态。";
const SCHEDULED_SCREEN_TRIGGER_PREFIX = "定时屏幕观察已提交给对话模型";
const LEGACY_MANUAL_SCREEN_TEXT = /^用户手动选择的 (\d+) 张屏幕截图已提交给对话模型。$/u;
const LEGACY_PLUGIN_SCREEN_TEXT = /^插件分享了 \d+ 张图片。$/u;

function isScreenObservation(entry) {
  return entry.kind === "observation" && (entry.origin === "scheduled_screen"
    || (entry.origin === "host" && typeof entry.payload.sourcePluginId === "string"
      && entry.payload.sourcePluginId && entry.payload.visual?.imageCount > 0));
}

function entryDisplayText(entry) {
  const content = text(entry.payload.text);
  if (entry.kind === "system") {
    if (entry.payload.eventType === "app.started") return "桌宠已启动";
    if (entry.payload.eventType === "app.closed") return "桌宠正在退出";
  }
  if (entry.kind === "observation" && entry.origin === "host" && entry.payload.sourcePluginId
      && !entry.payload.visual) {
    if (entry.payload.notificationKind === "update") return content;
    return `由「${text(entry.payload.sourcePluginName) || entry.payload.sourcePluginId}」发起`;
  }
  if (entry.kind !== "observation" || entry.origin !== "manual_screen") return content;
  const legacy = LEGACY_MANUAL_SCREEN_TEXT.exec(content);
  return legacy ? `你分享了 ${legacy[1]} 张屏幕截图。` : content;
}

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
  if (entry.origin === "host" && entry.payload.sourcePluginId && !entry.payload.visual) {
    return entry.payload.notificationKind === "update" ? "更新提醒" : "插件消息";
  }
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
      && !LEGACY_PLUGIN_SCREEN_TEXT.test(content)
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
    return entry.payload.segments.map((segment, segmentIndex) => ({
      entryId: entry.entryId,
      historyEntryId: entry.entryId,
      segmentIndex,
      suppressTts: segment.suppressTts === true,
      turnId: entry.turnId,
      role: "assistant",
      group: "assistant",
      align: "left",
      roleName: assistantName,
      createdAt,
      content: selectSegmentText({ text: text(segment.text), translation: text(segment.translation).trim() }, subtitleLanguage),
      subtitleTracks: selectSegmentTracks(segment, subtitleLanguage),
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
    content: entryDisplayText(entry),
    detailsContent: text(entry.payload.activity?.details),
    actionLabel: text(entry.payload.activity?.actionLabel),
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
  const scheduledEntriesByTurn = new Map();
  for (const entry of entries) {
    if (isScreenObservation(entry)) {
      const turnEntries = scheduledEntriesByTurn.get(entry.turnId) || [];
      turnEntries.push(entry);
      scheduledEntriesByTurn.set(entry.turnId, turnEntries);
    }
  }
  const bubbles = [];
  const consumedScheduledTurns = new Set();
  for (const entry of entries) {
    if (entry.kind === "system" && entry.payload.eventType === "app.reconnected") continue;
    if (isScreenObservation(entry)) {
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
    const showMeta = bubble.role === "system" || bubble.group !== previousGroup;
    previousGroup = bubble.group;
    return Object.freeze({
      ...bubble,
      showMeta,
      metaText: `${bubble.roleName} · ${bubble.createdAt}`,
    });
  });
}

export function preservePrependScroll(previous, currentScrollHeight) {
  const top = Number(previous?.scrollTop) || 0;
  const height = Number(previous?.scrollHeight) || 0;
  return Math.max(0, top + Math.max(0, Number(currentScrollHeight) - height));
}
