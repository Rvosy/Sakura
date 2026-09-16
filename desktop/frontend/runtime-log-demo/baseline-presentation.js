const SCOPES = new Set(["software", "tts", "plugins"]);
const SEVERITIES = new Set(["trace", "debug", "info", "warning", "error"]);
const VIEW_MODES = new Set(["all", "problems"]);
const MAX_RECORDS = 400;
const INLINE_DETAIL_LABELS = new Set([
  "状态", "服务", "模型", "工具", "耗时", "数据量", "数量", "进度", "分辨率",
  "当前版本", "目标版本", "检测到的版本",
]);
const TTS_LIFECYCLE_EVENTS = new Set([
  "tts.service.started",
  "tts.service.waiting_ready",
  "tts.service.ready",
  "tts.service.failed",
  "tts.weights.loading",
  "tts.weights.ready",
  "tts.weights.failed",
]);

function isObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function exactKeys(value, expected) {
  const actual = Object.keys(value).sort();
  return actual.length === expected.length
    && actual.every((key, index) => key === [...expected].sort()[index]);
}

function viewerError() {
  return new Error("RUNTIME_LOG_VIEWER_RESPONSE_INVALID");
}

export function validateViewerRecord(value) {
  const required = [
    "sequence", "timestamp", "scopes", "severity", "category", "eventCode", "message", "details", "source",
  ];
  const keys = new Set(required);
  keys.add("pluginId");
  keys.add("pluginName");
  keys.add("description");
  keys.add("correlationId");
  if (!isObject(value) || Object.keys(value).some((key) => !keys.has(key))) throw viewerError();
  if (required.some((key) => !(key in value))) throw viewerError();
  if (!Number.isSafeInteger(value.sequence) || value.sequence < 1) throw viewerError();
  if (typeof value.timestamp !== "string" || !/^\d{2}:\d{2}:\d{2}$/.test(value.timestamp)) throw viewerError();
  if (!Array.isArray(value.scopes) || value.scopes.length < 1 || value.scopes.some((scope) => !SCOPES.has(scope))) {
    throw viewerError();
  }
  if (!["rust", "core", "webview", "plugin"].includes(value.source)) throw viewerError();
  if (value.source === "plugin") {
    if (typeof value.pluginId !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(value.pluginId)
      || !value.scopes.some(scope => scope === "plugins" || scope === "tts")) throw viewerError();
    if ("pluginName" in value && (typeof value.pluginName !== "string" || !value.pluginName)) throw viewerError();
  } else if ("pluginId" in value || "pluginName" in value || value.scopes.includes("plugins")) throw viewerError();
  if (value.scopes.includes("tts") && value.scopes.length !== 1) throw viewerError();
  if (!SEVERITIES.has(value.severity)) throw viewerError();
  if ([value.category, value.eventCode, value.message].some((text) => typeof text !== "string" || !text)) {
    throw viewerError();
  }
  if (!["warning", "error"].includes(value.severity)) {
    if ("description" in value) throw viewerError();
  } else if (
    typeof value.description !== "string"
    || !value.description.trim()
    || value.description.length > 192
  ) {
    throw viewerError();
  }
  if (!Array.isArray(value.details) || value.details.length > 12 || value.details.some((detail) => (
    !isObject(detail)
    || !exactKeys(detail, ["label", "value"])
    || typeof detail.label !== "string"
    || !detail.label
    || typeof detail.value !== "string"
    || !detail.value
  ))) throw viewerError();
  if ("correlationId" in value && (typeof value.correlationId !== "string" || !value.correlationId)) {
    throw viewerError();
  }
  return value;
}

export function validateViewerSnapshot(value) {
  if (!isObject(value) || !exactKeys(value, [
    "schemaVersion", "runId", "latestSequence", "resetRequired", "records", "failedFiles",
  ])) throw viewerError();
  if (value.schemaVersion !== 3 || typeof value.runId !== "string" || !value.runId) throw viewerError();
  if (!Number.isSafeInteger(value.latestSequence) || value.latestSequence < 0) throw viewerError();
  if (typeof value.resetRequired !== "boolean" || !Array.isArray(value.records) || value.records.length > MAX_RECORDS) {
    throw viewerError();
  }
  if (!Array.isArray(value.failedFiles) || value.failedFiles.length > 2
    || value.failedFiles.some((name) => !["runtime", "plugins"].includes(name))) throw viewerError();
  let previous = 0;
  for (const record of value.records) {
    validateViewerRecord(record);
    if (record.sequence <= previous || record.sequence > value.latestSequence) throw viewerError();
    previous = record.sequence;
  }
  return value;
}

export function validateViewerBootstrap(value) {
  if (!isObject(value) || !exactKeys(value, ["schemaVersion", "themeTokens", "snapshot"])) throw viewerError();
  if (value.schemaVersion !== 3 || !isObject(value.themeTokens)) throw viewerError();
  validateViewerSnapshot(value.snapshot);
  return value;
}

export function applyViewerSnapshot(state, snapshot) {
  validateViewerSnapshot(snapshot);
  const replace = !state || snapshot.resetRequired || state.runId !== snapshot.runId;
  const records = replace
    ? snapshot.records.slice()
    : [
      ...state.records,
      ...snapshot.records.filter((record) => record.sequence > state.latestSequence),
    ].slice(-MAX_RECORDS);
  return Object.freeze({
    runId: snapshot.runId,
    latestSequence: Math.max(snapshot.latestSequence, replace ? 0 : state.latestSequence),
    records: Object.freeze(records),
    failedFiles: Object.freeze(snapshot.failedFiles.slice()),
  });
}

function collapseKey(record) {
  return JSON.stringify([
    record.source,
    record.pluginId || "",
    record.pluginName || "",
    record.scopes,
    record.severity,
    record.category,
    record.eventCode,
    record.message,
    record.description || "",
    record.details,
    record.correlationId || "",
  ]);
}

export function collapseViewerRecords(records, scope) {
  if (!SCOPES.has(scope)) throw viewerError();
  const collapsed = [];
  for (const record of records.filter((candidate) => candidate.scopes.includes(scope))) {
    const key = collapseKey(record);
    const previous = collapsed.at(-1);
    if (previous?.collapseKey === key) {
      previous.record = record;
      previous.repeatCount += 1;
      previous.firstSequence = Math.min(previous.firstSequence, record.sequence);
      continue;
    }
    collapsed.push({
      collapseKey: key,
      record,
      repeatCount: 1,
      firstSequence: record.sequence,
    });
  }
  return collapsed;
}

export function viewerItemKey(item, scope) {
  if (!SCOPES.has(scope) || !Number.isSafeInteger(item?.firstSequence) || item.firstSequence < 1) {
    throw viewerError();
  }
  return `${scope}:${item.firstSequence}`;
}

export function viewerScopeCounts(records) {
  return Object.freeze({
    software: records.filter((record) => record.scopes.includes("software")).length,
    tts: records.filter((record) => record.scopes.includes("tts")).length,
    plugins: records.filter((record) => record.scopes.includes("plugins")).length,
  });
}

export function filterViewerRecords(records, scope, mode = "all", pluginId = "") {
  if (!SCOPES.has(scope) || !VIEW_MODES.has(mode)) throw viewerError();
  return records.filter((record) => (
    record.scopes.includes(scope)
    && (!pluginId || record.pluginId === pluginId)
    && (mode === "all" || ["warning", "error"].includes(record.severity))
  ));
}

export function viewerProblemCount(records, scope, pluginId = "") {
  return filterViewerRecords(records, scope, "problems", pluginId).length;
}

export function viewerInlineSummary(record, limit = 3) {
  if (TTS_LIFECYCLE_EVENTS.has(record.eventCode)) {
    const elapsed = record.details.find((detail) => detail.label === "耗时");
    return elapsed ? `${elapsed.label}=${elapsed.value}` : "";
  }
  return record.details
    .filter((detail) => (
      INLINE_DETAIL_LABELS.has(detail.label)
      && !(record.eventCode.startsWith("ipc.request.") && detail.label === "状态")
    ))
    .slice(0, Math.max(0, limit))
    .map((detail) => `${detail.label}=${detail.value}`)
    .join(" · ");
}

export function viewerCopyText(item) {
  const { record, repeatCount = 1 } = item;
  const level = { info: "信息", warning: "提醒", error: "错误" }[record.severity] || record.severity;
  const lines = [
    `[${record.timestamp}] [${record.category}] [${level}] ${record.message}`,
  ];
  lines.push(`来源：${record.source}`);
  if (record.pluginId) lines.push(`插件：${viewerPluginName(record)}`, `插件标识：${record.pluginId}`);
  if (record.description) lines.push(`说明：${record.description}`);
  lines.push(`事件代码：${record.eventCode}`);
  for (const detail of record.details) lines.push(`${detail.label}：${detail.value}`);
  if (record.correlationId) lines.push(`关联编号：${record.correlationId}`);
  if (repeatCount > 1) lines.push(`连续重复：${repeatCount} 次`);
  return lines.join("\n");
}


export function viewerPluginName(record) {
  return record.pluginName || "未命名插件";
}

export function viewerPluginOptions(records) {
  const names = new Map();
  for (const record of records) {
    if (record.pluginId && record.scopes.includes("plugins")) names.set(record.pluginId, viewerPluginName(record));
  }
  return [...names].map(([id, name]) => ({ id, name }))
    .sort((a, b) => a.name.localeCompare(b.name, "zh-CN") || a.id.localeCompare(b.id));
}
