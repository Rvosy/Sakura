const TRACE_COMMAND = "record_interaction_latency_trace";
const TRACE_BATCH_LIMIT = 64;
const TRACE_FLUSH_DELAY_MS = 160;

function finite(value) {
  return Number.isFinite(value) ? value : null;
}

function normalizedEventTime(event, timeOrigin) {
  const value = finite(event?.timeStamp);
  if (value === null || value < 0) return null;
  // Chromium normally exposes a time-origin-relative DOMHighResTimeStamp. Keep compatibility
  // with older epoch-based implementations without putting any event payload in diagnostics.
  return value > 1_000_000_000_000 ? value - timeOrigin : value;
}

function validToken(value) {
  return typeof value === "string" && /^[a-z0-9][a-z0-9.-]{0,79}$/.test(value);
}

export function createInteractionLatencyTracer({
  source,
  invoke,
  enabled = false,
  now = () => performance.now(),
  timeOrigin = Number(performance.timeOrigin),
  setTimer = (callback, delay) => window.setTimeout(callback, delay),
  clearTimer = (timer) => window.clearTimeout(timer),
  consoleObject = globalThis.console,
} = {}) {
  if (!validToken(source)) throw new Error("interaction latency trace source is invalid");
  if (typeof invoke !== "function") throw new Error("interaction latency trace requires invoke");
  const active = enabled === true && Number.isFinite(timeOrigin);
  let gestureSequence = 0;
  let pending = [];
  let flushTimer = null;
  let sending = false;

  function timestampContext(gestureId, revision) {
    if (!active) return null;
    const perfMs = now();
    return Object.freeze({
      gestureId,
      revision,
      clientPerfMs: perfMs,
      clientEpochMs: timeOrigin + perfMs,
    });
  }

  function createGesture(kind) {
    if (!active) return null;
    if (!validToken(kind)) throw new Error("interaction latency gesture kind is invalid");
    gestureSequence += 1;
    return timestampContext(`${source}-${kind}-${gestureSequence}`, 0);
  }

  function atRevision(context, revision) {
    if (
      !active
      || !context
      || !validToken(context.gestureId)
      || !Number.isSafeInteger(revision)
      || revision < 0
    ) return null;
    return timestampContext(context.gestureId, revision);
  }

  function scheduleFlush() {
    if (!active || flushTimer !== null || sending || pending.length === 0) return;
    flushTimer = setTimer(() => {
      flushTimer = null;
      void flush();
    }, TRACE_FLUSH_DELAY_MS);
  }

  async function flush() {
    if (!active || sending || pending.length === 0) return;
    if (flushTimer !== null) {
      clearTimer(flushTimer);
      flushTimer = null;
    }
    const entries = pending;
    pending = [];
    sending = true;
    try {
      await invoke(TRACE_COMMAND, { entries });
    } catch {
      // The local console copy is intentionally retained as a fallback. Diagnostics must never
      // surface a product error or retry on the interaction-critical path.
    } finally {
      sending = false;
      if (pending.length > 0) scheduleFlush();
    }
  }

  function mark(stage, context, { event = null, elapsedMs = null } = {}) {
    if (!active || !context || !validToken(stage)) return null;
    const perfMs = now();
    const eventPerfMs = normalizedEventTime(event, timeOrigin);
    const entry = Object.freeze({
      source,
      stage,
      gestureId: context.gestureId,
      revision: context.revision,
      perfMs,
      epochMs: timeOrigin + perfMs,
      eventPerfMs,
      eventDelayMs: eventPerfMs === null ? null : Math.max(0, perfMs - eventPerfMs),
      elapsedMs: finite(elapsedMs),
    });
    consoleObject?.debug?.(`[interaction-latency] ${JSON.stringify(entry)}`);
    pending.push(entry);
    if (pending.length >= TRACE_BATCH_LIMIT) void flush();
    else scheduleFlush();
    return entry;
  }

  async function tracedInvoke(command, args, context, stage) {
    if (!active || !context) return invoke(command, args);
    const started = now();
    mark(`${stage}.invoke-start`, context);
    try {
      const result = await invoke(command, { ...args, trace: context });
      mark(`${stage}.invoke-return`, context, { elapsedMs: now() - started });
      return result;
    } catch (error) {
      mark(`${stage}.invoke-error`, context, { elapsedMs: now() - started });
      throw error;
    }
  }

  return Object.freeze({
    enabled: active,
    createGesture,
    atRevision,
    mark,
    tracedInvoke,
    flush,
  });
}
