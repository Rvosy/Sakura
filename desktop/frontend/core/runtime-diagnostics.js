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

function controlledEntry(input) {
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
  return Object.freeze(entry);
}

function eventForCommand(command, outcome) {
  if (command.startsWith("settings_memory_")) return "webview.memory.request";
  if (command.startsWith("settings_tools_")) return "webview.tools.request";
  if (outcome === "started") return "webview.command.started";
  if (outcome === "completed") return "webview.command.completed";
  if (outcome === "cancelled") return "webview.command.cancelled";
  return "webview.command.failed";
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
        record({
          level: "warn",
          event: eventForCommand(command, "failed"),
          command,
          outcome: "failed",
          code: "INVOKE_FAILED",
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
