export const UPDATE_ANNOUNCEMENT_IDLE_MS = 3_000;
export const UPDATE_ANNOUNCEMENT_POLL_MS = 250;

const TERMINALS = new Set(["chat.completed", "chat.failed", "chat.cancelled"]);
const TRANSIENT_DISPATCH_CODES = new Set([
  "CHAT_INTERACTION_ACTIVE",
  "CHAT_NOT_READY",
  "CHAT_BRIDGE_UNAVAILABLE",
  "CHAT_GENERATION_INVALIDATED",
  "CHAT_GENERATION_MISMATCH",
  "CHAT_DISPATCH_ABORTED",
  "CHAT_START_TIMEOUT",
]);

function errorCode(error) {
  const value = error instanceof Error ? error.message : error;
  return String(value || "UPDATE_ANNOUNCEMENT_FAILED").split(/[|:]/)[0].trim();
}

export function createUpdateAnnouncementController({
  check,
  announce,
  isIdle,
  now = () => Date.now(),
  setInterval = (callback, delay) => globalThis.setInterval(callback, delay),
  clearInterval = (timer) => globalThis.clearInterval(timer),
  onDiagnostic = () => {},
} = {}) {
  if ([check, announce, isIdle].some((value) => typeof value !== "function")) {
    throw new Error("UPDATE_ANNOUNCEMENT_DEPENDENCY_INVALID");
  }
  let disposed = false;
  let enabled = true;
  let pending = false;
  let checking = false;
  let dispatching = false;
  let operationId = null;
  let earlyTerminal = null;
  let failedAttempts = 0;
  let idleSince = null;
  let timer = null;

  function resetIdle() {
    idleSince = null;
  }

  async function refresh() {
    if (disposed || checking || !enabled) return;
    checking = true;
    try {
      const snapshot = await check();
      if (!enabled) {
        pending = false;
        return;
      }
      pending = snapshot?.status === "pending";
      if (!pending) {
        operationId = null;
        failedAttempts = 0;
      }
      onDiagnostic("update.auto_check.completed", {
        status: String(snapshot?.status || "unavailable"),
        version: typeof snapshot?.version === "string" ? snapshot.version : null,
      });
    } catch (error) {
      pending = false;
      onDiagnostic("update.auto_check.failed", { code: errorCode(error) });
    } finally {
      checking = false;
      resetIdle();
    }
  }

  async function tick() {
    if (disposed || !enabled || !pending || checking || dispatching || operationId) return;
    if (!isIdle()) {
      resetIdle();
      return;
    }
    const timestamp = now();
    if (idleSince === null) {
      idleSince = timestamp;
      return;
    }
    if (timestamp - idleSince < UPDATE_ANNOUNCEMENT_IDLE_MS) return;
    resetIdle();
    dispatching = true;
    try {
      const response = await announce();
      operationId = String(response?.operationId || "");
      if (!operationId) throw new Error("UPDATE_ANNOUNCEMENT_RESPONSE_INVALID");
      onDiagnostic("update.announcement.started", { operationId });
      if (earlyTerminal?.operationId === operationId) {
        const terminal = earlyTerminal;
        earlyTerminal = null;
        settleTerminal(terminal);
      }
    } catch (error) {
      const code = errorCode(error);
      if (code === "UPDATE_ANNOUNCEMENT_NOT_PENDING") pending = false;
      if (!TRANSIENT_DISPATCH_CODES.has(code) && code !== "UPDATE_ANNOUNCEMENT_NOT_PENDING") {
        failedAttempts += 1;
        if (failedAttempts >= 2) pending = false;
      }
      onDiagnostic("update.announcement.dispatch_failed", { code, failedAttempts });
    } finally {
      dispatching = false;
      earlyTerminal = null;
    }
  }

  function settleTerminal(event) {
    const completed = event.type === "chat.completed";
    operationId = null;
    resetIdle();
    if (completed) {
      pending = false;
      failedAttempts = 0;
      onDiagnostic("update.announcement.completed", {});
      return;
    }
    if (event.type === "chat.failed") failedAttempts += 1;
    if (failedAttempts >= 2) pending = false;
    onDiagnostic("update.announcement.retry_waiting", {
      terminal: event.type,
      failedAttempts,
      pending,
    });
  }

  return Object.freeze({
    start() {
      if (disposed || timer !== null) return;
      timer = setInterval(() => { void tick(); }, UPDATE_ANNOUNCEMENT_POLL_MS);
      void refresh();
    },
    refresh,
    tick,
    applyPreferences(snapshot) {
      enabled = snapshot?.autoCheckEnabled === true;
      if (!enabled) {
        pending = false;
        resetIdle();
        return;
      }
      void refresh();
    },
    handleChatEvent(event) {
      if (!TERMINALS.has(event?.type)) return;
      if (!operationId) {
        if (dispatching && typeof event.operationId === "string" && event.operationId) {
          earlyTerminal = event;
        }
        return;
      }
      if (event.operationId === operationId) settleTerminal(event);
    },
    noteActivity() {
      resetIdle();
    },
    generationChanged() {
      operationId = null;
      earlyTerminal = null;
      resetIdle();
    },
    isPending() {
      return pending || dispatching || Boolean(operationId);
    },
    snapshot() {
      return Object.freeze({
        enabled,
        pending,
        checking,
        dispatching,
        operationId,
        failedAttempts,
        idleSince,
      });
    },
    dispose() {
      disposed = true;
      if (timer !== null) clearInterval(timer);
      timer = null;
      pending = false;
      dispatching = false;
      operationId = null;
      earlyTerminal = null;
    },
  });
}
