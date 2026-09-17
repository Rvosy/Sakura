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

  function releasePlayback() {
    playback?.resolveSettled();
    playback = null;
  }

  function receive(nativeEvent) {
    const event = nativeEvent?.payload;
    if (!playback || playback.id !== event?.playbackId || playback.epoch !== epoch) return;
    if (["finished", "stopped", "failed"].includes(event.state)) {
      releasePlayback();
      if (event.state === "failed") onDiagnostic(event.error?.code || "AUDIO_PLAYBACK_FAILED");
    }
  }

  function isCurrent(current) {
    return !disposed && !captureActive && current === reply && current.epoch === epoch;
  }

  function errorCode(error, fallback) {
    return String(error?.message || error || fallback).split("|")[0];
  }

  async function runAudio(current) {
    for (let index = 0; index < current.segments.length && isCurrent(current); index += 1) {
      if (!playable(current.segments[index])) continue;
      let descriptor;
      try {
        descriptor = await invoke("tts_prepare_segment", { payload: {
          operationId: current.operationId,
          segmentIndex: index,
        } });
      } catch (error) {
        if (!isCurrent(current)) return;
        const code = errorCode(error, "TTS_SERVICE_UNAVAILABLE");
        if (code === "TTS_DISABLED") return;
        onDiagnostic(code);
        continue;
      }
      if (!isCurrent(current)) return;
      if (!validDescriptor(descriptor)) {
        onDiagnostic("AUDIO_RECORDING_INVALID");
        continue;
      }
      const playbackId = `tts-${current.epoch}-${index}`;
      let resolveSettled;
      const settled = new Promise((resolve) => { resolveSettled = resolve; });
      const item = { id: playbackId, epoch: current.epoch, resolveSettled };
      playback = item;
      try {
        await invoke("tts_play_prepared", { payload: {
          opaqueId: descriptor.opaqueId,
          playbackId,
        } });
      } catch (error) {
        if (!isCurrent(current)) return;
        if (playback === item) releasePlayback();
        onDiagnostic(errorCode(error, "AUDIO_PLAYBACK_FAILED"));
      }
      await settled;
    }
  }

  return Object.freeze({
    async start() {
      if (disposed) throw new Error("TTS_CONTROLLER_DISPOSED");
      unlisten = await listen("sakura://tts-playback-event", receive);
    },
    beginReply(operationId, segments) {
      if (disposed) return;
      cancelSynthesis(reply?.operationId);
      if (reply) invokeBestEffort("tts_stop_playback");
      epoch += 1;
      releasePlayback();
      reply = Object.freeze({
        epoch,
        operationId,
        segments: Array.isArray(segments) ? segments : [],
      });
      void runAudio(reply);
    },
    setInputCaptureActive(value) {
      const next = Boolean(value);
      if (captureActive === next) return;
      captureActive = next;
      if (!next) return;
      const operationId = reply?.operationId;
      epoch += 1;
      reply = null;
      releasePlayback();
      cancelSynthesis(operationId);
      invokeBestEffort("tts_stop_playback");
    },
    cancel() {
      const operationId = reply?.operationId;
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
