const DIAGNOSTICS_COMMAND = "record_runtime_diagnostics";
const BATCH_LIMIT = 64;
const PENDING_LIMIT = 256;
const FLUSH_DELAY_MS = 100;
const LEVELS = new Set(["trace", "debug", "info", "warn", "warning", "error"]);
const DIAGNOSTIC_CODES = new Set([
  "REQUEST_DEADLINE_EXCEEDED",
  "REQUEST_CANCELLED",
  "GENERATION_INVALIDATED",
  "SETTINGS_CORE_GENERATION_MISMATCH",
  "SETTINGS_CORE_UNAVAILABLE",
  "SETTINGS_TRANSPORT_UNAVAILABLE",
  "TRANSPORT_UNAVAILABLE",
  "RESPONSE_INVALID",
  "PROTOCOL_ERROR",
  "CORE_CRASHED",
  "INVALID_REQUEST",
  "PLUGIN_SETTINGS_NOT_READY",
  "PLUGIN_COLLECTION_REQUEST_INVALID",
  "PLUGIN_COLLECTION_RESPONSE_INVALID",
  "SETTINGS_COLLECTION_FAILED",
  "SETTINGS_COLLECTION_UNAVAILABLE",
  "SETTINGS_COLLECTION_INVALID",
  "SETTINGS_COLLECTION_QUERY_INVALID",
  "SETTINGS_COLLECTION_VALUES_INVALID",
  "SETTINGS_COLLECTION_OPERATION_UNAVAILABLE",
  "SETTINGS_COLLECTION_OPERATION_INVALID",
  "SETTINGS_COLLECTION_RESULT_INVALID",
  "SETTINGS_COLLECTION_ITEM_INVALID",
  "PLUGIN_CALLBACK_TIMEOUT",
  "PLUGIN_CALLBACK_DATA_INVALID",
  "PLUGIN_CALLBACK_IO_FAILED",
  "PLUGIN_CALLBACK_FAILED",
  "PLUGIN_DEPENDENCY_UNAVAILABLE",
  "CHARACTER_PRESENTATION_NOT_READY",
  "CHARACTER_PRESENTATION_UNAVAILABLE",
]);
const EVENTS = new Set([
  "webview.lifecycle.ready",
  "webview.lifecycle.unloading",
  "webview.error.unhandled",
  "webview.command.started",
  "webview.command.completed",
  "webview.command.failed",
  "webview.command.cancelled",
  "webview.chat.send",
  "webview.chat.terminal",
  "webview.settings.opened",
  "webview.settings.closed",
  "webview.memory.request",
  "webview.tools.request",
  "webview.interaction.stage",
]);

function token(value, maximum) {
  return typeof value === "string"
    && value.length > 0
    && value.length <= maximum
    && /^[A-Za-z0-9][A-Za-z0-9._:-]*$/.test(value);
}

function stableCode(value) {
  return typeof value === "string" && /^[A-Z][A-Z0-9_]{0,63}$/.test(value);
}

function safeDiagnostic(error) {
  const source = typeof error === "string"
    ? error
    : typeof error?.message === "string"
      ? error.message
      : "";
  const publicError = source.match(/^([A-Z][A-Z0-9_]{2,63})\|[^\r\n|]{0,120}\|[^\r\n|]{0,120}\|[^\r\n]{0,240}$/);
  if (publicError) {
    return DIAGNOSTIC_CODES.has(publicError[1])
      ? Object.freeze({ code: publicError[1], diagnostic: publicError[1] })
      : null;
  }
  const match = source.match(/^([A-Z][A-Z0-9_]{2,63})(?::\s*([^\r\n]{1,240}))?$/);
  if (!match || !DIAGNOSTIC_CODES.has(match[1])) return null;
  let detail = match[2] || "";
  detail = detail
    .replace(/\b(api[_-]?key|authorization|cookie|password|secret|token)\s*[:=]\s*(?:bearer\s+)?[^\s,;]+/gi, "$1=[REDACTED]")
    .replace(/\bbearer\s+[^\s,;]+/gi, "Bearer [REDACTED]")
    .replace(/\bsk-[A-Za-z0-9._-]{6,}/gi, "[REDACTED]")
    .replace(/\b([a-z][a-z0-9+.-]*:\/\/)[^/@\s]+@/gi, "$1[REDACTED]@")
    .trim();
  return Object.freeze({ code: match[1], diagnostic: detail || match[1] });
}

function logText(value, maximum) {
  const text = value.replace(/\x1b\[[0-9;]*[A-Za-z]/g, "").replace(/\s+/g, " ").trim();
  if (/(?:[a-z]:[\\/]|(?:^|\s)\/|:\/\/|bearer\s|sk-|(?:api[_-]?key|token|password|secret|authorization|cookie)\s*[:=])/i.test(text)) return "[REDACTED]";
  const bytes = new TextEncoder().encode(text);
  if (bytes.length <= maximum) return text || "[empty]";
  return new TextDecoder("utf-8", { fatal: false }).decode(bytes.slice(0, maximum - 16)).replace(/\ufffd$/, "") + " [truncated]";
}

function logFields(input) {
  let budget = 32;
  function clean(value, depth) {
    if (depth > 3 || budget-- <= 0) return "[truncated]";
    if (value === null || typeof value === "boolean") return value;
    if (typeof value === "number") return Number.isFinite(value) ? value : null;
    if (typeof value === "string") return logText(value, 256);
    if (Array.isArray(value)) {
      const result = value.slice(0, 8).map(item => clean(item, depth + 1));
      if (value.length > 8) result.push("[truncated]");
      return result;
    }
    if (value && typeof value === "object") {
      const result = Object.create(null);
      let count = 0;
      for (const key in value) {
        if (!Object.hasOwn(value, key)) continue;
        if (count++ >= 8) { result.record_truncated = true; break; }
        if (!token(key, 64) || /(?:api.?key|authorization|cookie|password|secret|token|content|prompt|messages|arguments|payload|body|path)/i.test(key)) {
          result.redacted = "[REDACTED]";
        } else {
          result[key] = clean(value[key], depth + 1);
        }
      }
      return result;
    }
    return "[unsupported]";
  }
  const result = clean(input, 0);
  return new TextEncoder().encode(JSON.stringify(result)).length <= 1800 ? result : { record_truncated: true };
}

function controlledEntry(input) {
  if (input?.event === "runtime.message") {
    if (!LEVELS.has(input.level) || typeof input.message !== "string" || !input.message.trim()) return null;
    try {
      const rawFields = input.fields ?? {};
      if (!rawFields || Array.isArray(rawFields) || typeof rawFields !== "object") return null;
      return Object.freeze({ level: input.level, event: input.event, message: logText(input.message, 1024), fields: logFields(rawFields) });
    } catch { return null; }
  }
  if (!input || !LEVELS.has(input.level) || !EVENTS.has(input.event)) return null;
  if (input.command !== undefined && (!token(input.command, 96) || input.command === DIAGNOSTICS_COMMAND)) {
    return null;
  }
  if (
    input.outcome !== undefined
    && !["started", "completed", "failed", "cancelled"].includes(input.outcome)
  ) return null;
  if (input.code !== undefined && !stableCode(input.code)) return null;
  if (
    input.elapsedMs !== undefined
    && (!Number.isFinite(input.elapsedMs) || input.elapsedMs < 0 || input.elapsedMs > 3_600_000)
  ) return null;
  if (input.operationId !== undefined && !token(input.operationId, 128)) return null;
  if (
    input.revision !== undefined
    && (!Number.isSafeInteger(input.revision) || input.revision < 0)
  ) return null;

  const entry = { level: input.level, event: input.event };
  if (input.command !== undefined) entry.command = input.command;
  if (input.outcome !== undefined) entry.outcome = input.outcome;
  if (input.code !== undefined) entry.code = input.code;
  if (input.elapsedMs !== undefined) entry.elapsedMs = input.elapsedMs;
  if (input.operationId !== undefined) entry.operationId = input.operationId;
  if (input.revision !== undefined) entry.revision = input.revision;
  if (input.diagnostic !== undefined) {
    if (typeof input.diagnostic !== "string" || input.diagnostic.length > 240 || /[\r\n]/.test(input.diagnostic)) {
      return null;
    }
    entry.diagnostic = input.diagnostic;
  }
  return Object.freeze(entry);
}

function eventForCommand(command, outcome) {
  if (command.startsWith("settings_tools_")) return "webview.tools.request";
  if (outcome === "started") return "webview.command.started";
  if (outcome === "completed") return "webview.command.completed";
  if (outcome === "cancelled") return "webview.command.cancelled";
  return "webview.command.failed";
}

function isExpectedReadinessRetry(command, code) {
  return ["current_character_presentation", "settings_character_appearance_get"].includes(command)
    && ["CHARACTER_PRESENTATION_NOT_READY", "CHARACTER_PRESENTATION_UNAVAILABLE"].includes(code);
}

export function createRuntimeDiagnostics({
  invoke: nativeInvoke,
  now = () => performance.now(),
  setTimer = (callback, delay) => window.setTimeout(callback, delay),
  clearTimer = (timer) => window.clearTimeout(timer),
  windowObject = globalThis.window,
} = {}) {
  if (typeof nativeInvoke !== "function") throw new Error("runtime diagnostics requires invoke");
  let pending = [];
  let timer = null;
  let sending = false;
  let disposed = false;

  function schedule() {
    if (disposed || sending || timer !== null || pending.length === 0) return;
    timer = setTimer(() => {
      timer = null;
      void flush();
    }, FLUSH_DELAY_MS);
  }

  function record(input) {
    if (disposed) return false;
    const entry = controlledEntry(input);
    if (!entry) return false;
    if (pending.length >= PENDING_LIMIT) pending.shift();
    pending.push(entry);
    if (pending.length >= BATCH_LIMIT) void flush();
    else schedule();
    return true;
  }

  async function flush() {
    if (sending || pending.length === 0) return;
    if (timer !== null) {
      clearTimer(timer);
      timer = null;
    }
    const entries = pending.splice(0, BATCH_LIMIT);
    sending = true;
    try {
      await nativeInvoke(DIAGNOSTICS_COMMAND, { entries });
    } catch {
      // Local diagnostics are best effort and never become a product failure.
    } finally {
      sending = false;
      if (!disposed && pending.length > 0) {
        if (pending.length >= BATCH_LIMIT) void flush();
        else schedule();
      }
    }
  }

  async function observedInvoke(command, args) {
    if (command === DIAGNOSTICS_COMMAND) return nativeInvoke(command, args);
    const started = now();
    if (token(command, 96)) {
      record({
        level: "debug",
        event: eventForCommand(command, "started"),
        command,
        outcome: "started",
      });
    }
    try {
      const result = await nativeInvoke(command, args);
      if (token(command, 96)) {
        record({
          level: "info",
          event: eventForCommand(command, "completed"),
          command,
          outcome: "completed",
          elapsedMs: Math.max(0, now() - started),
        });
      }
      return result;
    } catch (error) {
      if (token(command, 96)) {
        const diagnostic = safeDiagnostic(error);
        const expectedRetry = diagnostic && isExpectedReadinessRetry(command, diagnostic.code);
        record({
          level: expectedRetry ? "debug" : "warn",
          event: eventForCommand(command, "failed"),
          command,
          outcome: "failed",
          code: diagnostic?.code || "INVOKE_FAILED",
          ...(diagnostic ? { diagnostic: diagnostic.diagnostic } : {}),
          elapsedMs: Math.max(0, now() - started),
        });
      }
      throw error;
    }
  }

  const onError = () => record({
    level: "error",
    event: "webview.error.unhandled",
    outcome: "failed",
    code: "WEBVIEW_UNHANDLED_ERROR",
  });
  const onUnhandledRejection = () => record({
    level: "error",
    event: "webview.error.unhandled",
    outcome: "failed",
    code: "WEBVIEW_UNHANDLED_REJECTION",
  });
  windowObject?.addEventListener?.("error", onError);
  windowObject?.addEventListener?.("unhandledrejection", onUnhandledRejection);

  function dispose({ settings = false } = {}) {
    if (disposed) return;
    record({
      level: "info",
      event: settings ? "webview.settings.closed" : "webview.lifecycle.unloading",
      outcome: "completed",
    });
    disposed = true;
    windowObject?.removeEventListener?.("error", onError);
    windowObject?.removeEventListener?.("unhandledrejection", onUnhandledRejection);
    if (timer !== null) {
      clearTimer(timer);
      timer = null;
    }
    void flush();
  }

  return Object.freeze({
    invoke: observedInvoke,
    message(level, message, fields = {}) {
      return record({ level, event: "runtime.message", message, fields });
    },
    record,
    flush,
    markReady({ settings = false } = {}) {
      return record({
        level: "info",
        event: settings ? "webview.settings.opened" : "webview.lifecycle.ready",
        outcome: "completed",
      });
    },
    dispose,
  });
}

export const RUNTIME_DIAGNOSTICS_COMMAND = DIAGNOSTICS_COMMAND;
