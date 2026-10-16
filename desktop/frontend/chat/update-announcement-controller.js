export const UPDATE_ANNOUNCEMENT_IDLE_MS = 3_000;
export const UPDATE_ANNOUNCEMENT_POLL_MS = 250;

const TERMINALS = new Set(["chat.completed", "chat.failed", "chat.cancelled"]);
const TRANSIENT_DISPATCH_CODES = new Set([
  "CHAT_INTERACTION_ACTIVE",
  "CHAT_NOT_READY",
  "CHAT_BRIDGE_UNAVAILABLE",
  "CHAT_GENERATION_INVALIDATED",
  "CHAT_DISPATCH_ABORTED",
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
  let revision = 0;
  let refreshRequested = false;

  function resetIdle() {
    idleSince = null;
  }

  async function refresh() {
    if (disposed || !enabled) return;
    if (checking || dispatching || operationId) { refreshRequested = true; return; }
    const current = revision;
    checking = true;
    try {
      const snapshot = await check();
      if (disposed || !enabled || current !== revision) return;
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
      if (disposed || current !== revision) return;
      pending = false;
      onDiagnostic("update.auto_check.failed", { code: errorCode(error) });
    } finally {
      checking = false;
      resetIdle();
      if (refreshRequested) { refreshRequested = false; void refresh(); }
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
    const current = revision;
    try {
      const response = await announce();
      if (disposed || current !== revision) return;
      operationId = String(response?.operationId || "");
      if (!operationId) throw new Error("UPDATE_ANNOUNCEMENT_RESPONSE_INVALID");
      onDiagnostic("update.announcement.started", { operationId });
      if (earlyTerminal?.operationId === operationId) {
        const terminal = earlyTerminal;
        earlyTerminal = null;
        settleTerminal(terminal);
      }
    } catch (error) {
      if (disposed || current !== revision) return;
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
      if (refreshRequested && !operationId) { refreshRequested = false; void refresh(); }
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
      if (refreshRequested) { refreshRequested = false; void refresh(); }
      return;
    }
    if (event.error?.code === "CHAT_DELIVERY_UNCONFIRMED") pending = false;
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
      revision += 1;
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
      revision += 1;
      operationId = null;
      earlyTerminal = null;
      resetIdle();
      void refresh();
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
      revision += 1;
      if (timer !== null) clearInterval(timer);
      timer = null;
      pending = false;
      dispatching = false;
      operationId = null;
      earlyTerminal = null;
    },
  });
}
