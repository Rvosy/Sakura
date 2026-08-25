export const SCREEN_AWARENESS_POLL_INTERVAL_MS = 10_000;
export const SCREEN_AWARENESS_PROMPT = "这是一次由 Sakura 定时截图触发的主动屏幕观察。以下截图按时间顺序展示我最近正在做的事情。请结合最近聊天历史和这些截图，以当前角色的语气自然接话：可以评论变化、接续任务、询问卡点或提供轻量帮助。不要逐张复述，也不要因为时间或久坐机械地提醒休息；如果没有明显变化，就简短说出你能确认的具体内容。";

const RESOLUTIONS = new Set(["fullscreen", "720p", "1080p", "2160p"]);

export function normalizeScreenAwarenessSettings(value) {
  const settings = {
    enabled: value?.enabled === true,
    checkIntervalMinutes: Number(value?.checkIntervalMinutes),
    cooldownMinutes: Number(value?.cooldownMinutes),
    batchLimit: Number(value?.batchLimit),
    resolution: String(value?.resolution || ""),
  };
  if (!Number.isSafeInteger(settings.checkIntervalMinutes)
      || settings.checkIntervalMinutes < 1 || settings.checkIntervalMinutes > 120
      || !Number.isSafeInteger(settings.cooldownMinutes)
      || settings.cooldownMinutes < 1 || settings.cooldownMinutes > 120
      || !Number.isSafeInteger(settings.batchLimit)
      || settings.batchLimit < 1 || settings.batchLimit > 20
      || !RESOLUTIONS.has(settings.resolution)) {
    throw new Error("SCREEN_AWARENESS_SETTINGS_INVALID");
  }
  return Object.freeze(settings);
}

export function createScreenAwarenessController({
  invoke,
  send,
  isIdle,
  generationId,
  now = () => Date.now(),
  setInterval = (callback, delay) => globalThis.setInterval(callback, delay),
  clearInterval = (timer) => globalThis.clearInterval(timer),
  onDiagnostic = () => {},
} = {}) {
  if ([invoke, send, isIdle, generationId].some((value) => typeof value !== "function")) {
    throw new Error("SCREEN_AWARENESS_DEPENDENCY_INVALID");
  }
  let settings = null;
  let timer = null;
  let disposed = false;
  let ticking = false;
  let generation = "";
  let lastActivityAt = now();
  let lastCaptureAt = now();
  let batchStartedAt = null;
  let batchCount = 0;

  function invokeBestEffort(command, args) {
    try { void Promise.resolve(invoke(command, args)).catch(() => {}); }
    catch { /* Native teardown may already have started. */ }
  }

  function resetClock(timestamp = now()) {
    lastActivityAt = timestamp;
    lastCaptureAt = timestamp;
    batchStartedAt = null;
    batchCount = 0;
  }

  function clearBatch(reason, timestamp = now()) {
    invokeBestEffort("clear_screen_awareness_batch");
    resetClock(timestamp);
    onDiagnostic("screen_awareness.batch.cleared", { reason });
  }

  async function fail(stage, error, attachmentId = null) {
    if (attachmentId) {
      invokeBestEffort("release_screen_attachment", { payload: { attachmentId } });
    }
    clearBatch(stage);
    onDiagnostic("screen_awareness.failed", { stage, code: String(error || stage).split("|")[0] });
  }

  async function tick() {
    if (disposed || ticking || !settings) return;
    const currentGeneration = String(generationId() || "");
    if (currentGeneration !== generation) {
      generation = currentGeneration;
      clearBatch("generation_changed");
      return;
    }
    if (!settings.enabled || !currentGeneration || !isIdle()) return;
    ticking = true;
    try {
      const timestamp = now();
      const intervalMs = settings.checkIntervalMinutes * 60_000;
      if (timestamp - lastActivityAt >= intervalMs && timestamp - lastCaptureAt >= intervalMs) {
        try {
          const result = await invoke("capture_screen_awareness_frame", { payload: {
            resolution: settings.resolution,
            batchLimit: settings.batchLimit,
          } });
          if (!Number.isSafeInteger(result?.count) || result.count < 1 || result.count > settings.batchLimit) {
            throw new Error("SCREEN_AWARENESS_CAPTURE_RESPONSE_INVALID");
          }
          lastCaptureAt = timestamp;
          if (batchCount === 0) batchStartedAt = timestamp;
          batchCount = result.count;
        } catch (error) {
          await fail("capture", error);
          return;
        }
      }
      if (batchCount === 0 || batchStartedAt === null
          || timestamp - batchStartedAt < settings.cooldownMinutes * 60_000
          || !isIdle()) return;

      let attachmentId = null;
      try {
        const attached = await invoke("attach_screen_awareness_batch");
        attachmentId = String(attached?.attachmentId || "");
        if (!/^screen-[0-9a-f]{32}$/.test(attachmentId) || attached?.count !== batchCount) {
          throw new Error("SCREEN_AWARENESS_ATTACHMENT_RESPONSE_INVALID");
        }
        await send({ message: SCREEN_AWARENESS_PROMPT, attachmentId });
        resetClock(timestamp);
      } catch (error) {
        await fail("send", error, attachmentId);
      }
    } finally {
      ticking = false;
    }
  }

  return Object.freeze({
    applySettings(value) {
      settings = normalizeScreenAwarenessSettings(value);
      generation = String(generationId() || "");
      clearBatch(settings.enabled ? "settings_changed" : "disabled");
    },
    start() {
      if (disposed || timer !== null) return;
      timer = setInterval(() => { void tick(); }, SCREEN_AWARENESS_POLL_INTERVAL_MS);
    },
    tick,
    noteActivity() {
      lastActivityAt = now();
    },
    noteManualSend() {
      clearBatch("manual_send");
    },
    generationChanged(value = generationId()) {
      generation = String(value || "");
      clearBatch("generation_changed");
    },
    snapshot() {
      return Object.freeze({ settings, generation, lastActivityAt, lastCaptureAt, batchStartedAt, batchCount });
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
