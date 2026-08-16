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
  const playback = new Map();

  function settle(item, event) {
    if (item.settled) return;
    item.settled = true;
    item.resolveSettled(event);
  }

  function releaseAll(type = "playback.stopped") {
    for (const item of playback.values()) {
      item.resolveStarted({ type });
      settle(item, { type });
    }
    playback.clear();
  }

  function receive(nativeEvent) {
    const event = nativeEvent?.payload;
    const item = playback.get(event?.playbackId);
    if (!item || item.epoch !== epoch) return;
    if (event.state === "started") {
      item.started = true;
      item.resolveStarted(event);
      if (reply && item.index + 1 < reply.segments.length) void prepare(item.index + 1);
      return;
    }
    if (["finished", "stopped", "failed"].includes(event.state)) {
      item.resolveStarted(event);
      settle(item, event);
      playback.delete(event.playbackId);
      if (event.state === "failed") onDiagnostic(event.error?.code || "AUDIO_PLAYBACK_FAILED");
    }
  }

  function prepare(index) {
    const current = reply;
    if (!current || current.epoch !== epoch || !playable(current.segments[index])) {
      return Promise.resolve(null);
    }
    if (!current.prepared.has(index)) {
      const task = Promise.resolve(invoke("tts_prepare_segment", { payload: {
        operationId: current.operationId,
        segmentIndex: index,
      } })).then((descriptor) => {
        if (current !== reply || current.epoch !== epoch || !validDescriptor(descriptor)) return null;
        return descriptor;
      }).catch((error) => {
        onDiagnostic(String(error || "TTS_SERVICE_UNAVAILABLE").split("|")[0]);
        return null;
      });
      current.prepared.set(index, task);
    }
    return current.prepared.get(index);
  }

  return Object.freeze({
    async start() {
      if (disposed) throw new Error("TTS_CONTROLLER_DISPOSED");
      unlisten = await listen("sakura://tts-playback-event", receive);
    },
    beginReply(operationId, segments) {
      epoch += 1;
      releaseAll();
      reply = Object.freeze({
        epoch,
        operationId,
        segments: Array.isArray(segments) ? segments : [],
        prepared: new Map(),
      });
      void prepare(0);
    },
    async beforeSegment(segment, index) {
      const current = reply;
      const currentEpoch = epoch;
      if (!current || !playable(segment) || current.segments[index] !== segment) return;
      const descriptor = await prepare(index);
      if (!descriptor || disposed || current !== reply || currentEpoch !== epoch) return;
      const playbackId = `tts-${currentEpoch}-${index}`;
      let resolveStarted;
      let resolveSettled;
      const started = new Promise((resolve) => { resolveStarted = resolve; });
      const settled = new Promise((resolve) => { resolveSettled = resolve; });
      const item = {
        epoch: currentEpoch,
        index,
        started: false,
        settled: false,
        resolveStarted,
        resolveSettled,
        settledPromise: settled,
      };
      playback.set(playbackId, item);
      try {
        await invoke("tts_play_prepared", { payload: {
          opaqueId: descriptor.opaqueId,
          playbackId,
        } });
      } catch (error) {
        item.resolveStarted({ state: "failed" });
        settle(item, { state: "failed" });
        playback.delete(playbackId);
        onDiagnostic(String(error || "AUDIO_PLAYBACK_FAILED").split("|")[0]);
      }
      await started;
    },
    async afterSegment(index) {
      const item = playback.get(`tts-${epoch}-${index}`);
      if (item) await item.settledPromise;
    },
    cancel() {
      epoch += 1;
      reply = null;
      releaseAll();
      if (!disposed) void Promise.resolve(invoke("tts_stop_playback")).catch(() => {});
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      epoch += 1;
      reply = null;
      releaseAll();
      void Promise.resolve(invoke("tts_stop_playback")).catch(() => {});
      try {
        Promise.resolve(unlisten?.()).catch(() => {});
      } catch {
        // Native event host may already be gone.
      }
    },
  });
}
