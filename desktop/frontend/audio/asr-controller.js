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
  const code = typeof error === "string" ? error : error?.errorCode || error?.code || "";
  const messages = {
    ASR_NO_SPEECH: "没有检测到人声，请再试一次。",
    ASR_BUSY: "另一次语音输入尚未结束，请稍后重试。",
    ASR_DEVICE_UNAVAILABLE: "麦克风不可用，请检查所选输入设备和麦克风权限。",
    ASR_MICROPHONE_UNAVAILABLE: "麦克风不可用，请检查所选输入设备和麦克风权限。",
    ASR_MICROPHONE_DISCONNECTED: "麦克风连接已中断，本次录音已取消。",
    ASR_CAPTURE_INTERRUPTED: "录音已中断，请重新开始。",
    ASR_AUDIO_FORMAT_UNSUPPORTED: "所选麦克风的音频格式暂不支持，请调整设备格式或更换输入设备。",
    ASR_CAPTURE_FAILED: "录音已中止，请检查麦克风连接和系统权限。",
    ASR_INPUT_DEVICE_UNAVAILABLE: "麦克风不可用，请检查所选输入设备和麦克风权限。",
    ASR_INPUT_DEVICE_NOT_FOUND: "所选麦克风未连接，请重新连接或在插件设置中选择其他设备。",
    ASR_PROVIDER_UNAVAILABLE: "所选语音输入引擎不可用，请在插件设置中检查引擎。",
    ASR_HUB_UNAVAILABLE: "语音输入服务不可用，请在插件设置中启用 ASR Hub。",
    ASR_SERVICE_UNAVAILABLE: "语音输入服务不可用，请在插件设置中检查 ASR Hub。",
    ASR_PROVIDER_NOT_SELECTED: "尚未选择语音输入引擎，请在插件设置中选择。",
    ASR_RESOURCES_MISSING: "语音输入模型尚未安装，请在插件设置中安装资源。",
    ASR_MODEL_MISSING: "语音输入模型尚未安装，请在插件设置中安装资源。",
    ASR_MODEL_INVALID: "语音输入模型无法加载，请在插件设置中重试安装。",
    ASR_DEPENDENCY_UNAVAILABLE: "语音输入引擎的依赖无法加载，请在插件设置中检查安装状态。",
  };
  return Object.entries(messages).find(([key]) => code.includes(key))?.[1]
    || "语音输入失败，请检查插件中的识别引擎与麦克风设置后重试。";
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
      finish(task, result.state === "failed" ? result.errorCode || "ASR_FAILED" : null);
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
          finish(task, payload.errorCode || "ASR_CAPTURE_FAILED"); void cancelNative(task);
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
