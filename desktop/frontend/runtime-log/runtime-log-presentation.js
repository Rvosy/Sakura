const SCOPES = new Set(["software", "tts"]);
const SEVERITIES = new Set(["info", "warning", "error"]);
const MAX_RECORDS = 400;

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
    "sequence", "timestamp", "scopes", "severity", "category", "eventCode", "message", "details",
  ];
  const keys = new Set(required);
  keys.add("correlationId");
  if (!isObject(value) || Object.keys(value).some((key) => !keys.has(key))) throw viewerError();
  if (required.some((key) => !(key in value))) throw viewerError();
  if (!Number.isSafeInteger(value.sequence) || value.sequence < 1) throw viewerError();
  if (typeof value.timestamp !== "string" || !/^\d{2}:\d{2}:\d{2}$/.test(value.timestamp)) throw viewerError();
  if (!Array.isArray(value.scopes) || value.scopes.length < 1 || value.scopes.some((scope) => !SCOPES.has(scope))) {
    throw viewerError();
  }
  if (!SEVERITIES.has(value.severity)) throw viewerError();
  if ([value.category, value.eventCode, value.message].some((text) => typeof text !== "string" || !text)) {
    throw viewerError();
  }
  if (!Array.isArray(value.details) || value.details.length > 9 || value.details.some((detail) => (
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
    "schemaVersion", "runId", "latestSequence", "resetRequired", "records",
  ])) throw viewerError();
  if (value.schemaVersion !== 1 || typeof value.runId !== "string" || !value.runId) throw viewerError();
  if (!Number.isSafeInteger(value.latestSequence) || value.latestSequence < 0) throw viewerError();
  if (typeof value.resetRequired !== "boolean" || !Array.isArray(value.records) || value.records.length > MAX_RECORDS) {
    throw viewerError();
  }
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
  if (value.schemaVersion !== 1 || !isObject(value.themeTokens)) throw viewerError();
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
  });
}

function collapseKey(record) {
  return JSON.stringify([
    record.scopes,
    record.severity,
    record.category,
    record.eventCode,
    record.message,
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

export function viewerScopeCounts(records) {
  return Object.freeze({
    software: records.filter((record) => record.scopes.includes("software")).length,
    tts: records.filter((record) => record.scopes.includes("tts")).length,
  });
}

export function viewerInlineSummary(record, limit = 3) {
  return record.details.slice(0, Math.max(0, limit)).map((detail) => `${detail.label}=${detail.value}`).join(" · ");
}

export function viewerCopyText(item) {
  const { record, repeatCount = 1 } = item;
  const level = { info: "信息", warning: "提醒", error: "错误" }[record.severity] || record.severity;
  const lines = [
    `[${record.timestamp}] [${record.category}] [${level}] ${record.message}`,
    `事件代码：${record.eventCode}`,
  ];
  for (const detail of record.details) lines.push(`${detail.label}：${detail.value}`);
  if (record.correlationId) lines.push(`关联编号：${record.correlationId}`);
  if (repeatCount > 1) lines.push(`连续重复：${repeatCount} 次`);
  return lines.join("\n");
}
