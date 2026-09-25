import { errorText } from '../core/error-display.js';
const ACTIVE = new Set(["preparing", "recording", "recognizing"]);

export function insertTranscript(draft, saved, text) {
  const current = String(draft.value ?? "");
  const unchanged = draft.version === saved.version && current === saved.value;
  const position = unchanged ? Math.min(current.length, saved.selectionEnd) : current.length;
  const left = current.slice(0, position);
  const right = current.slice(position);
  const insertion = `${left && !/\s$/.test(left) ? " " : ""}${text}${right && !/^\s/.test(right) ? " " : ""}`;
  return { value: left + insertion + right, caret: left.length + insertion.length };
}

export function asrErrorMessage(error) {
  return errorText(error);
}

// The consumer never sends a message. It owns only one complete draft insertion.
export function createAsrController({
  invoke, listen, readContext, readDraft, writeDraft, restoreSelection = () => {},
  onState = () => {}, onLevel = () => {}, onError = () => {},
  prepareOptions = () => ({}),
  id = () => globalThis.crypto.randomUUID(),
  schedule = (fn) => setTimeout(fn, 120), unschedule = clearTimeout,
}) {
  let disposed = false;
  let active = null;
  let timer = null;
  const unlisteners = [];

  function valid(task) {
    return !disposed && active === task && task.contextId === readContext();
  }
  function clearTimer() { if (timer !== null) unschedule(timer); timer = null; }
  function cancelNative(task) {
    return Promise.resolve().then(() => invoke("asr_cancel", { payload: { recordingId: task.recordingId } })).catch(() => {});
  }
  function state(task, phase) {
    if (task.phase === phase) return;
    task.phase = phase;
    onState({ state: phase, recordingId: task.recordingId });
  }
  function finish(task, error = null, restore = true) {
    if (active !== task) return;
    active = null;
    clearTimer();
    onState({ state: "idle", recordingId: null });
    if (restore && task.contextId === readContext()) {
      const current = readDraft();
      restoreSelection(current.version === task.saved.version && current.value === task.saved.value
        ? task.saved : null);
    }
    if (error) onError(asrErrorMessage(error));
  }
  function nextPoll(task) {
    if (!valid(task) || timer !== null || task.polling) return;
    timer = schedule(async () => {
      timer = null;
      if (!valid(task)) { if (active === task) void cancel({ restore: false }); return; }
      task.polling = true;
      try {
        const result = await invoke("asr_poll", { payload: { recordingId: task.recordingId } });
        await accept(task, result);
      } catch (error) {
        if (valid(task)) { finish(task, error); void cancelNative(task); }
      } finally {
        task.polling = false;
        nextPoll(task);
      }
    });
  }
  async function accept(task, result) {
    if (!valid(task)) {
      if (active === task) finish(task, null, false);
      void cancelNative(task); return;
    }
    if (!result || (result.recordingId && result.recordingId !== task.recordingId)
        || (result.contextId && result.contextId !== task.contextId)
        || (result.purpose && result.purpose !== task.purpose)) {
      finish(task, "ASR_RESPONSE_INVALID"); void cancelNative(task); return;
    }
    if (result.state === "ready" && !task.captureStarted) {
      task.captureStarted = true;
      const started = await invoke("asr_capture_start", { payload: { recordingId: task.recordingId } });
      if (!valid(task)) { void cancelNative(task); return; }
      if (started?.state !== "recording") return accept(task, started);
      state(task, "recording");
    } else if (result.state === "succeeded") {
      if (typeof result.text !== "string" || !result.text.trim()) {
        finish(task, "ASR_NO_SPEECH"); return;
      }
      const inserted = insertTranscript(readDraft(), task.saved, result.text.trim());
      // Invalidate first, before the write callback can trigger another event or poll.
      finish(task, null, false);
      writeDraft(inserted);
      return;
    } else if (["failed", "cancelled", "consumed"].includes(result.state)) {
      finish(task, result.state === "failed" ? result : null);
      void cancelNative(task);
      return;
    } else if (ACTIVE.has(result.state)) {
      // A poll begun just before stop may still describe the previous capture phase.
      if (!(task.phase === "recognizing" && result.state !== "recognizing")) state(task, result.state);
    } else if (result.state !== "ready") {
      finish(task, "ASR_RESPONSE_INVALID"); void cancelNative(task); return;
    }
    nextPoll(task);
  }
  async function cancel({ restore = true } = {}) {
    const task = active;
    if (!task) return;
    finish(task, null, restore);
    await cancelNative(task);
  }
  return Object.freeze({
    active: () => active !== null,
    state: () => active?.phase || "idle",
    async connect() {
      unlisteners.push(await listen("sakura://asr-level", ({ payload }) => {
        const task = active;
        if (!task || !valid(task) || task.phase !== "recording"
            || payload?.recordingId !== task.recordingId
            || !Number.isSafeInteger(payload.sequence) || payload.sequence <= task.sequence
            || !Number.isFinite(payload.level) || payload.level < 0 || payload.level > 1) return;
        task.sequence = payload.sequence;
        onLevel(payload.level);
      }));
      unlisteners.push(await listen("sakura://asr-capture", ({ payload }) => {
        const task = active;
        if (!task || payload?.recordingId !== task.recordingId) return;
        if (!valid(task)) { void cancel({ restore: false }); return; }
        if (payload.state === "recognizing") { state(task, "recognizing"); nextPoll(task); }
        else if (payload.state === "failed") {
          finish(task, payload); void cancelNative(task);
        }
      }));
    },
    async start() {
      if (active || disposed) return;
      const options = prepareOptions();
      const task = {
        recordingId: id(), contextId: readContext(), saved: { ...readDraft() },
        purpose: options.purpose || "draft",
        phase: null, captureStarted: false, sequence: -1, polling: false,
      };
      active = task;
      state(task, "preparing");
      try {
        const result = await invoke("asr_prepare", { payload: {
          ...options,
          recordingId: task.recordingId, contextId: task.contextId,
          draftVersion: task.saved.version, selectionStart: task.saved.selectionStart,
          selectionEnd: task.saved.selectionEnd,
        } });
        await accept(task, result);
      } catch (error) {
        if (valid(task)) finish(task, error);
        void cancelNative(task);
      }
    },
    async stop() {
      const task = active;
      if (!task || task.phase !== "recording") return;
      state(task, "recognizing");
      try {
        await accept(task, await invoke("asr_capture_stop", { payload: { recordingId: task.recordingId } }));
      } catch (error) {
        if (valid(task)) finish(task, error);
        void cancelNative(task);
      }
    },
    cancel,
    dispose() {
      void cancel({ restore: false });
      disposed = true;
      for (const unlisten of unlisteners.splice(0)) unlisten?.();
    },
  });
}
