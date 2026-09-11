const DIAGNOSTICS_COMMAND = "record_runtime_diagnostics";
const BATCH_LIMIT = 64;
const PENDING_LIMIT = 256;
const FLUSH_DELAY_MS = 100;
const LEVELS = new Set(["trace", "debug", "info", "warn", "warning", "error"]);
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

function safeErrorText(value, maximum = 4096) {
  let text = String(value).replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, "");
  text = text
    .replace(/\b(api[_-]?key|authorization|cookie|password|secret|(?:access[_-]?|refresh[_-]?)?token|credential)["']?\s*[:=]\s*(?:bearer\s+)?(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;}]+)/gi, "$1=[REDACTED]")
    .replace(/\bbearer\s+[^\s,;}]+/gi, "Bearer [REDACTED]")
    .replace(/\bsk-[A-Za-z0-9._-]{6,}/gi, "[REDACTED]");
  const urls = [];
  text = text.replace(/[a-z][a-z0-9+.-]*:\/\/[^\s<>"']+/gi, value => {
    try { const url = new URL(value); url.username = ""; url.password = ""; url.search = ""; url.hash = ""; urls.push(url.protocol === "file:" ? `<路径>/${url.pathname.split("/").at(-1)}` : url.href); }
    catch { urls.push("[URL]"); }
    return `<url-${urls.length - 1}>`;
  });
  const path = value => `<路径>/${value.replace(/\\/g, "/").split("/").filter(Boolean).at(-1) || ""}`;
  text = text.replace(/(["'])((?:[a-z]:[\\/]|\/|\\\\)[^\r\n]*?)\1/gi, (_, quote, value) => quote + path(value) + quote)
    .replace(/(?:[a-z]:[\\/]|\\\\)[^\s"'<>|,;]*/gi, path)
    .replace(/(^|[\s(\[])\/[^\s"'<>|,;]*/g, value => (value.match(/^[\s(\[]/)?.[0] || "") + path(value.trim()))
    .replace(/[\x00-\x08\x0b-\x1f\x7f]/g, "");
  urls.forEach((url, index) => { text = text.replaceAll(`<url-${index}>`, url); });
  return text.length <= maximum ? text : text.slice(0, maximum - 48) + `\n[truncated: ${text.length} characters]`;
}

function safeDiagnostic(error) {
  const source = typeof error === "string" ? error : typeof error?.message === "string" ? error.message : "";
  const publicError = source.match(/^([A-Z][A-Z0-9_]{0,63})\|[^|\r\n]*\|[^|\r\n]*\|([\s\S]*)$/);
  const coded = source.match(/^([A-Z][A-Z0-9_]{0,63})(?::\s*([\s\S]*))?$/);
  const code = stableCode(error?.code) ? error.code : publicError?.[1] || coded?.[1] || "INVOKE_FAILED";
  const raw = publicError?.[2] || coded?.[2] || source;
  const prefix = !publicError && !coded && typeof error?.name === "string" ? `${error.name}: ` : "";
  let diagnostic = safeErrorText(prefix + raw) || "未记录底层原因";
  const stacks = [];
  const seen = new Set();
  for (let current = error; current && typeof current === "object" && !seen.has(current) && seen.size < 8; current = current.cause) {
    seen.add(current);
    if (current !== error && typeof current.message === "string") diagnostic = safeErrorText(`${current.name || "Error"}: ${current.message}`);
    if (typeof current.stack === "string") stacks.push(current.stack.split("\n").slice(0, 33).join("\n"));
  }
  const stack = safeErrorText(stacks.join("\nCaused by:\n"), 8192);
  return {code, diagnostic, ...(stack ? {exceptionStack: stack} : {})};
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

const ERROR_TYPES = new Set(["Error", "TypeError", "ReferenceError", "SyntaxError", "RangeError", "URIError", "EvalError", "AggregateError", "DOMException"]);
function applicationFile(value) {
  if (typeof value !== "string") return undefined;
  try {
    const url = new URL(value, "tauri://localhost/");
    if (!["tauri:", "http:", "https:"].includes(url.protocol) || !["localhost", "tauri.localhost"].includes(url.hostname)) return undefined;
    const path = url.pathname.replace(/^\//, "");
    if (!/^(?:(?:core|chat|settings|history|studio|onboarding|runtime-log|pet|shared|styles)\/)*[A-Za-z0-9_.-]+\.(?:js|css|html)$/.test(path) || path.includes("..")) return undefined;
    return `desktop/frontend/${path}`;
  } catch { return undefined; }
}
function exceptionDetails(event, rejection = false) {
  const error = rejection ? event?.reason : event?.error;
  const detail = { stage: rejection ? "promise" : event?.target && !event?.error && !event?.filename ? "resource" : "javascript" };
  if (ERROR_TYPES.has(error?.name)) detail.causeType = error.name;
  let file = applicationFile(event?.filename || event?.target?.src || event?.target?.href);
  let line = event?.lineno, column = event?.colno;
  if (!file && typeof error?.stack === "string") {
    for (const frame of error.stack.split("\n").slice(0, 17)) {
      const match = frame.match(/((?:https?:\/\/tauri\.localhost|tauri:\/\/localhost|https?:\/\/localhost(?::\d+)?)[^\s)]+):(\d+):(\d+)/);
      if (match && (file = applicationFile(match[1]))) { line = Number(match[2]); column = Number(match[3]); break; }
    }
  }
  if (file) {
    detail.file = file;
    if (Number.isSafeInteger(line) && line > 0 && line <= 10000000) detail.line = line;
    if (Number.isSafeInteger(column) && column > 0 && column <= 10000000) detail.column = column;
  }
  return detail;
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
  if (input.stage !== undefined && !token(input.stage, 96)) return null;
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
  if (input.details !== undefined && input.event === "webview.error.unhandled") {
    const d = input.details;
    if (!d || typeof d !== "object" || !["javascript","promise","resource"].includes(d.stage)) return null;
    entry.details = { stage: d.stage };
    if (ERROR_TYPES.has(d.causeType)) entry.details.causeType = d.causeType;
    if (typeof d.file === "string" && d.file.startsWith("desktop/frontend/") && applicationFile(d.file.slice(17)) === d.file) {
      entry.details.file = d.file;
      for (const key of ["line","column"]) if (Number.isSafeInteger(d[key]) && d[key] > 0 && d[key] <= 10000000) entry.details[key] = d[key];
    }
  }
  if (input.command !== undefined) entry.command = input.command;
  if (input.outcome !== undefined) entry.outcome = input.outcome;
  if (input.code !== undefined) entry.code = input.code;
  if (input.stage !== undefined) entry.stage = input.stage;
  if (input.elapsedMs !== undefined) entry.elapsedMs = input.elapsedMs;
  if (input.operationId !== undefined) entry.operationId = input.operationId;
  if (input.revision !== undefined) entry.revision = input.revision;
  if (input.diagnostic !== undefined) {
    if (typeof input.diagnostic !== "string" || input.diagnostic.length > 4096) {
      return null;
    }
    entry.diagnostic = input.diagnostic;
  }
  if (input.exceptionStack !== undefined) {
    if (typeof input.exceptionStack !== "string" || input.exceptionStack.length > 8192) return null;
    entry.exceptionStack = input.exceptionStack;
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
  const reportedErrors = new WeakSet();

  function reportError(error, { command, code, stage = command, level = "warn" } = {}) {
    if (error && typeof error === "object" && reportedErrors.has(error)) return false;
    const diagnostic = safeDiagnostic(error);
    const accepted = record({ ...diagnostic, level, event: "webview.command.failed", outcome: "failed",
      command, stage, code: diagnostic.code === "INVOKE_FAILED" ? code || diagnostic.code : diagnostic.code });
    if (accepted && error && typeof error === "object") reportedErrors.add(error);
    return accepted;
  }

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
    // Attribute Studio calls by their controlled method, never by request data.
    const logCommand = command === "studio_request" && token(args?.method, 96) ? args.method : command;
    if (token(command, 96)) {
      record({
        level: "debug",
        event: eventForCommand(command, "started"),
        command: logCommand,
        outcome: "started",
      });
    }
    try {
      const result = await nativeInvoke(command, args);
      if (token(command, 96)) {
        record({
          level: logCommand === "studio.visual.catalog" ? "debug" : "info",
          event: eventForCommand(command, "completed"),
          command: logCommand,
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
          command: logCommand,
          outcome: "failed",
          code: diagnostic?.code || "INVOKE_FAILED",
          ...diagnostic,
          elapsedMs: Math.max(0, now() - started),
        });
        if (error && typeof error === "object") reportedErrors.add(error);
      }
      throw error;
    }
  }

  const onError = (event) => record({
    ...safeDiagnostic(event?.error || event?.message),
    details: exceptionDetails(event),
    level: "error",
    event: "webview.error.unhandled",
    outcome: "failed",
    code: "WEBVIEW_UNHANDLED_ERROR",
  });
  const onUnhandledRejection = (event) => record({
    ...safeDiagnostic(event?.reason),
    details: exceptionDetails(event, true),
    level: "error",
    event: "webview.error.unhandled",
    outcome: "failed",
    code: "WEBVIEW_UNHANDLED_REJECTION",
  });
  windowObject?.addEventListener?.("error", onError, true);
  windowObject?.addEventListener?.("unhandledrejection", onUnhandledRejection);

  function dispose({ settings = false } = {}) {
    if (disposed) return;
    record({
      level: "info",
      event: settings ? "webview.settings.closed" : "webview.lifecycle.unloading",
      outcome: "completed",
    });
    disposed = true;
    windowObject?.removeEventListener?.("error", onError, true);
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
    reportError,
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
