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

export function createTtsController({ invoke, listen, onDiagnostic = () => {} } = {}) {
  if (typeof invoke !== "function" || typeof listen !== "function") {
    throw new Error("TTS_CONTROLLER_DEPENDENCY_INVALID");
  }
  let disposed = false;
  let epoch = 0;
  let unlisten = null;
  let reply = null;
  let captureActive = false;
  let playback = null;

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

  function isCurrent(current) {
    return !disposed && current === reply && current?.epoch === epoch;
  }

  function openPlayback(item, event) {
    if (item.started) return;
    item.started = true;
    if (isCurrent(item.reply)) item.onStarted(event);
    item.resolveStarted();
  }

  function releasePlayback({ fallback = false } = {}) {
    const item = playback;
    if (!item) return;
    playback = null;
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
      if (!item.reply.replay) void prepare(item.reply, item.index + 1);
    } else if (["finished", "stopped", "failed"].includes(event.state)) {
      openPlayback(item, event);
      releasePlayback();
      if (event.state === "failed") onDiagnostic(event.error?.code || "AUDIO_PLAYBACK_FAILED");
    }
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
          const descriptor = await invoke("tts_prepare_segment", { payload: {
            operationId: current.operationId,
            segmentIndex: current.replay ? current.segments[index].segmentIndex : index,
            ...(current.replay ? { replay: true } : {}),
          } });
          if (!isCurrent(current) || current.silent) return null;
          if (!validDescriptor(descriptor)) {
            onDiagnostic("AUDIO_RECORDING_INVALID");
            return null;
          }
          return descriptor;
        } catch (error) {
          if (!isCurrent(current) || current.silent) return null;
          const code = errorCode(error, "TTS_SERVICE_UNAVAILABLE");
          if (code === "TTS_DISABLED") current.silent = true;
          else onDiagnostic(code);
          return null;
        }
      })();
      current.prepared.set(index, task);
    }
    return current.prepared.get(index);
  }

  function beginReply(operationId, segments, replay = false) {
    if (disposed) return;
    cancelSynthesis(reply?.operationId);
    if (reply) invokeBestEffort("tts_stop_playback");
    reply?.resolveInterrupted();
    epoch += 1;
    releasePlayback();
    let resolveInterrupted;
    const interrupted = new Promise((resolve) => { resolveInterrupted = resolve; });
    reply = {
      epoch,
      operationId,
      replay,
      segments: Array.isArray(segments) ? segments : [],
      prepared: new Map(),
      played: new Set(),
      silent: captureActive,
      interrupted,
      resolveInterrupted,
    };
    void prepare(reply, 0);
  }

  async function playDescriptor(current, descriptor, index, onStarted = () => {}) {
    const playbackId = `tts-${current.epoch}-${current.replay ? "replay" : index}`;
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
      onDiagnostic(errorCode(error, "AUDIO_PLAYBACK_FAILED"));
    }
    await started;
  }

  return Object.freeze({
    async start() {
      if (disposed) throw new Error("TTS_CONTROLLER_DISPOSED");
      unlisten = await listen("sakura://tts-playback-event", receive);
    },
    beginReply,
    async beforeSegment(segment, index, { prepareVisual = () => {}, onStarted = () => {} } = {}) {
      const current = reply;
      // Greetings are local, suppressed segments without a synthesis operation.
      if (!current || current.segments[index] !== segment) {
        if (!disposed && !playable(segment)) onStarted({ state: "skipped" });
        return;
      }
      // Resolve optional visual control alongside synthesis at the presentation
      // boundary. Chat completion is already published; interruption still opens
      // the gate immediately and late preparation cannot start old playback.
      const [descriptor] = await Promise.race([
        Promise.all([prepare(current, index), prepareVisual()]),
        current.interrupted.then(() => []),
      ]);
      if (!isCurrent(current)) return;
      if (!descriptor || current.silent || !playable(segment)) {
        onStarted({ state: "skipped" });
        return;
      }
      await playDescriptor(current, descriptor, index, onStarted);
    },
    async replaySegment(segment) {
      if (disposed || captureActive || !playable(segment)) return;
      if (
        typeof segment.operationId !== "string"
        || !segment.operationId
        || !Number.isInteger(segment.segmentIndex)
        || segment.segmentIndex < 0
      ) return;
      beginReply(segment.operationId, [segment], true);
      const current = reply;
      const descriptor = await Promise.race([prepare(current, 0), current.interrupted]);
      if (!isCurrent(current) || current.silent || !descriptor) return;
      await playDescriptor(current, descriptor, 0);
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
      if (reply) {
        reply.silent = true;
        reply.resolveInterrupted();
        // Release only the current segment's preparation. Later silent segments
        // still prepare their own visual before starting subtitles together.
        reply.interrupted = new Promise(resolve => { reply.resolveInterrupted = resolve; });
      }
      releasePlayback({ fallback: true });
      cancelSynthesis(reply?.operationId);
      invokeBestEffort("tts_stop_playback");
    },
    cancel() {
      const operationId = reply?.operationId;
      reply?.resolveInterrupted();
      epoch += 1;
      reply = null;
      releasePlayback();
      if (!disposed) {
        cancelSynthesis(operationId);
        invokeBestEffort("tts_stop_playback");
      }
    },
    dispose() {
      if (disposed) return;
      const operationId = reply?.operationId;
      disposed = true;
      reply?.resolveInterrupted();
      epoch += 1;
      reply = null;
      releasePlayback();
      cancelSynthesis(operationId);
      invokeBestEffort("tts_stop_playback");
      try {
        Promise.resolve(unlisten?.()).catch(() => {});
      } catch {
        // Native event host may already be gone.
      }
    },
  });
}
