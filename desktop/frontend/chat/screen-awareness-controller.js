export const SCREEN_AWARENESS_POLL_INTERVAL_MS = 10_000;

// UI reports activity/idle facts and executes Core decisions. It owns no
// sampling clocks, settings policy, batch count, or Assistant prompt.
export function createScreenAwarenessController({
  invoke,
  send,
  isIdle,
  generationId,
  setInterval = (callback, delay) => globalThis.setInterval(callback, delay),
  clearInterval = (timer) => globalThis.clearInterval(timer),
  onDiagnostic = () => {},
} = {}) {
  if ([invoke, send, isIdle, generationId].some((value) => typeof value !== "function")) {
    throw new Error("SCREEN_AWARENESS_DEPENDENCY_INVALID");
  }
  let timer = null;
  let disposed = false;
  let ticking = false;
  let generation = "";
  let activity = true;
  let reset = true;
  let epoch = 0;

  function bestEffort(command, args) {
    try { void Promise.resolve(invoke(command, args)).catch(() => {}); }
    catch { /* Native teardown may already have started. */ }
  }

  function clearBatch(reason) {
    epoch += 1;
    reset = true;
    activity = true;
    bestEffort("clear_screen_awareness_batch");
    onDiagnostic("screen_awareness.batch.cleared", { reason });
  }

  function facts(extra = {}) {
    const payload = { idle: isIdle(), activity, reset, ...extra };
    activity = false;
    reset = false;
    return payload;
  }

  async function tick() {
    if (disposed || ticking) return;
    const currentGeneration = String(generationId() || "");
    if (currentGeneration !== generation) {
      generation = currentGeneration;
      clearBatch("generation_changed");
    }
    if (!currentGeneration) return;
    ticking = true;
    const currentEpoch = epoch;
    let attachmentId = null;
    const stale = () => currentEpoch !== epoch || disposed;
    try {
      let plan = await invoke("screen_awareness_step", { payload: facts() });
      if (stale()) return;
      if (plan?.action === "capture") {
        if (!isIdle() || activity) return;
        const result = await invoke("capture_screen_awareness_frame", { payload: {
          resolution: plan.resolution, batchLimit: plan.batchLimit,
        } });
        if (stale()) {
          bestEffort("clear_screen_awareness_batch");
          return;
        }
        plan = await invoke("screen_awareness_step", { payload: facts({
          revision: plan.revision, count: result.count,
        }) });
        if (stale()) return;
      }
      if (plan?.action === "clear") {
        bestEffort("clear_screen_awareness_batch");
        return;
      }
      if (plan?.action !== "submit" || !isIdle() || activity) return;
      const attached = await invoke("attach_screen_awareness_batch");
      attachmentId = String(attached?.attachmentId || "");
      if (stale() || !isIdle() || activity) {
        if (attachmentId) bestEffort("release_screen_attachment", { payload: { attachmentId } });
        clearBatch("activity_changed");
        return;
      }
      if (!/^screen-[0-9a-f]{32}$/.test(attachmentId) || attached?.count !== plan.count) {
        throw new Error("SCREEN_AWARENESS_ATTACHMENT_RESPONSE_INVALID");
      }
      await send({ attachmentId });
      clearBatch("submitted");
    } catch (error) {
      if (attachmentId) bestEffort("release_screen_attachment", { payload: { attachmentId } });
      if (!stale()) {
        clearBatch("failed");
        onDiagnostic("screen_awareness.failed", { code: String(error).split("|")[0] });
      }
    } finally {
      ticking = false;
    }
  }

  return Object.freeze({
    applySettings() {
      generation = String(generationId() || "");
      clearBatch("settings_changed");
    },
    start() {
      if (disposed || timer !== null) return;
      timer = setInterval(() => { void tick(); }, SCREEN_AWARENESS_POLL_INTERVAL_MS);
    },
    tick,
    noteActivity() { activity = true; },
    noteManualSend() { clearBatch("manual_send"); },
    generationChanged(value = generationId()) {
      generation = String(value || "");
      clearBatch("generation_changed");
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      if (timer !== null) clearInterval(timer);
      timer = null;
      clearBatch("dispose");
    },
  });
}
