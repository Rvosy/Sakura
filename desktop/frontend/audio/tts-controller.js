import { errorText } from '../core/error-display.js';
function playable(segment) {
  return segment && typeof segment === "object" && segment.suppressTts !== true;
}

function validDescriptor(value) {
  return Boolean(
    value
    && typeof value.opaqueId === "string"
    && /^[0-9a-f]{32}$/i.test(value.opaqueId)
    && (value.recordingId === null || typeof value.recordingId === "string")
    && value.mediaType === "audio/wav"
    && Number.isSafeInteger(value.byteLength)
    && value.byteLength > 0
    && typeof value.expiresAt === "string",
  );
}

export function createTtsController({ invoke, listen, onDiagnostic = () => {}, onPlaybackState = () => {} } = {}) {
  if (typeof invoke !== "function" || typeof listen !== "function") {
    throw new Error("TTS_CONTROLLER_DEPENDENCY_INVALID");
  }
  let disposed = false;
  let epoch = 0;
  let unlisten = null;
  let unlistenOperation = null;
  let reply = null;
  let captureActive = false;
  let playback = null;

  function publishState(state, current = reply, index = 0) {
    const segment = current?.segments[index];
    onPlaybackState({ state, historyEntryId: segment?.historyEntryId ?? null,
      segmentIndex: segment?.segmentIndex ?? index });
  }

  function invokeBestEffort(name, args) {
    try {
      void Promise.resolve(invoke(name, args)).catch(() => {});
    } catch {
      // The native host may already be gone during generation teardown.
    }
  }

  function cancelSynthesis(operationId) {
    if (typeof operationId !== "string" || !operationId) return;
    invokeBestEffort("tts_cancel_synthesis", { payload: { operationId } });
  }

  function stopPlayback(operationId) {
    if (typeof operationId !== "string" || !operationId) return;
    invokeBestEffort("tts_stop_playback", { payload: { operationId } });
  }

  function isCurrent(current) {
    return !disposed && current === reply && current?.epoch === epoch;
  }

  function openPlayback(item, event) {
    if (item.started) return;
    item.started = true;
    if (event.state === "started") publishState("playing", item.reply, item.index);
    if (isCurrent(item.reply)) item.onStarted(event);
    item.resolveStarted();
  }

  function releasePlayback({ fallback = false } = {}) {
    const item = playback;
    if (!item) return;
    playback = null;
    publishState("idle");
    if (fallback) openPlayback(item, { state: "skipped" });
    else item.resolveStarted();
    item.resolveSettled();
  }

  function receive(nativeEvent) {
    const event = nativeEvent?.payload;
    const item = playback;
    if (!item || item.id !== event?.playbackId || !isCurrent(item.reply)) return;
    if (event.state === "started") {
      openPlayback(item, event);
      // Prepare one segment ahead; only the subtitle sequencer may start it.
      void prepare(item.reply, item.index + 1);
    } else if (["finished", "stopped", "failed"].includes(event.state)) {
      openPlayback(item, event);
      releasePlayback();
      if (event.state === "failed") onDiagnostic(errorText(event.error, "AUDIO_PLAYBACK_FAILED"), { history: item.reply.history });
    }
  }

  function silenceReply() {
    if (!reply) return;
    reply.silent = true;
    reply.resolveInterrupted();
    // Later silent segments still wait for their own visual preparation.
    reply.interrupted = new Promise(resolve => { reply.resolveInterrupted = resolve; });
    releasePlayback({ fallback: true });
    cancelSynthesis(reply.operationId);
    publishState("idle");
  }

  function errorCode(error, fallback) {
    return String(error?.message || error || fallback).split("|")[0];
  }

  function prepare(current, index) {
    if (!isCurrent(current) || current.silent || !playable(current.segments[index])) {
      return Promise.resolve(null);
    }
    if (!current.prepared.has(index)) {
      const task = (async () => {
        try {
          if (!await current.initialized || !isCurrent(current) || current.silent) return null;
          const segment = current.segments[index];
          const descriptor = await invoke(current.history ? "tts_prepare_history_segment" : "tts_prepare_segment", { payload: {
            operationId: current.operationId,
            segmentIndex: segment.segmentIndex ?? index,
            ...(current.history ? { historyEntryId: segment.historyEntryId } : {}),
          } });
          if (!isCurrent(current) || current.silent) return null;
          if (!validDescriptor(descriptor)) {
            onDiagnostic("AUDIO_RECORDING_INVALID", { history: current.history });
            return null;
          }
          return descriptor;
        } catch (error) {
          if (!isCurrent(current) || current.silent) return null;
          const code = errorCode(error, "TTS_SERVICE_UNAVAILABLE");
          if (code === "TTS_DISABLED" && !current.history) current.silent = true;
          else onDiagnostic(errorText(error), { history: current.history });
          return null;
        }
      })();
      current.prepared.set(index, task);
    }
    return current.prepared.get(index);
  }

  return Object.freeze({
    async start() {
      if (disposed) throw new Error("TTS_CONTROLLER_DISPOSED");
      unlisten = await listen("sakura://tts-playback-event", receive);
      unlistenOperation = await listen("sakura://tts-operation-started", event => {
        if (!reply) return;
        if (event?.payload?.operationId === reply.operationId) reply.operationStarted = true;
        else if (reply.operationStarted) silenceReply();
      });
    },
    beginReply(operationId, segments, { history = false } = {}) {
      if (disposed) return;
      cancelSynthesis(reply?.operationId);
      reply?.resolveInterrupted();
      epoch += 1;
      releasePlayback();
      let resolveInterrupted;
      const interrupted = new Promise((resolve) => { resolveInterrupted = resolve; });
      reply = {
        epoch,
        operationId,
        history,
        operationStarted: false,
        segments: Array.isArray(segments) ? segments : [],
        prepared: new Map(),
        played: new Set(),
        silent: captureActive,
        interrupted,
        resolveInterrupted,
      };
      const current = reply;
      current.initialized = (async () => {
        try {
          await invoke("tts_begin_reply", { payload: { operationId } });
          return true;
        } catch (error) {
          if (isCurrent(current) && !current.silent) {
            current.silent = true;
            publishState("idle");
            onDiagnostic(errorText(error), { history: current.history });
          }
          return false;
        }
      })();
      void prepare(reply, 0);
    },
    async beforeSegment(segment, index, { prepareVisual = () => {}, onStarted = () => {} } = {}) {
      const current = reply;
      // Greetings are local, suppressed segments without a synthesis operation.
      if (!current || current.segments[index] !== segment) {
        if (!disposed && !playable(segment)) onStarted({ state: "skipped" });
        return;
      }
      if (!current.silent && playable(segment)) publishState("preparing", current, index);
      // Resolve optional visual control alongside synthesis at the presentation
      // boundary. Chat completion is already published; interruption still opens
      // the gate immediately and late preparation cannot start old playback.
      const [descriptor] = await Promise.race([
        Promise.all([prepare(current, index), prepareVisual()]),
        current.interrupted.then(() => []),
      ]);
      if (!isCurrent(current)) return;
      if (!descriptor || current.silent || !playable(segment)) {
        publishState("idle");
        onStarted({ state: "skipped" });
        return;
      }
      const playbackId = current.history ? `tts-${current.operationId}` : `tts-${current.epoch}-${index}`;
      let resolveStarted;
      let resolveSettled;
      const started = new Promise((resolve) => { resolveStarted = resolve; });
      const settled = new Promise((resolve) => { resolveSettled = resolve; });
      const item = {
        id: playbackId, reply: current, index, started: false,
        onStarted, resolveStarted, resolveSettled, settled,
      };
      playback = item;
      current.played.add(index);
      // Playback events, including an early failure, own the visual start gate.
      // Do not keep the gate blocked by a late native command acknowledgement.
      try {
        Promise.resolve(invoke("tts_play_prepared", { payload: {
          opaqueId: descriptor.opaqueId,
          playbackId,
        } })).catch(failed);
      } catch (error) {
        failed(error);
      }
      function failed(error) {
        if (!isCurrent(current) || playback !== item) return;
        openPlayback(item, { state: "failed" });
        releasePlayback();
        onDiagnostic(errorText(error, "AUDIO_PLAYBACK_FAILED"), { history: current.history });
      }
      await started;
    },
    async playHistorySegment({ historyEntryId, segmentIndex }) {
      if (disposed || captureActive) return;
      const segment = { historyEntryId, segmentIndex };
      this.beginReply(`history-${globalThis.crypto.randomUUID()}`, [segment], { history: true });
      await this.beforeSegment(segment, 0);
    },
    async afterSegment(index) {
      const current = reply;
      const item = playback;
      if (item && item.index === index && item.reply === current && isCurrent(current)) {
        await item.settled;
      }
      return Boolean(current?.played.has(index) && isCurrent(current));
    },
    setInputCaptureActive(value) {
      const next = Boolean(value);
      if (captureActive === next) return;
      captureActive = next;
      if (!next) return;
      // Keep the current visual sequence alive, but never resume its audio.
      silenceReply();
      stopPlayback(reply?.operationId);
    },
    stop() {
      const ownsPlayback = reply && !reply.silent;
      silenceReply();
      if (!disposed && ownsPlayback) stopPlayback(reply.operationId);
    },
    cancel() {
      const operationId = reply?.operationId;
      const ownsPlayback = reply && !reply.silent;
      reply?.resolveInterrupted();
      epoch += 1;
      reply = null;
      releasePlayback();
      publishState("idle");
      if (!disposed) {
        cancelSynthesis(operationId);
        if (ownsPlayback) stopPlayback(operationId);
      }
    },
    dispose() {
      if (disposed) return;
      const operationId = reply?.operationId;
      const ownsPlayback = reply && !reply.silent;
      disposed = true;
      reply?.resolveInterrupted();
      epoch += 1;
      reply = null;
      releasePlayback();
      publishState("idle");
      cancelSynthesis(operationId);
      if (ownsPlayback) stopPlayback(operationId);
      try {
        Promise.resolve(unlisten?.()).catch(() => {});
        Promise.resolve(unlistenOperation?.()).catch(() => {});
      } catch {
        // Native event host may already be gone.
      }
    },
  });
}
