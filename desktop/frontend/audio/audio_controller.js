export class AudioController {
  constructor({ store, invoke, setStatus = () => {}, volume = 1 }) {
    this.store = store;
    this.invoke = invoke;
    this.setStatus = setStatus;
    this.volume = Math.max(0, Math.min(1, Number(volume) || 0));
    this.generation = 0;
    this.queue = [];
    this.current = null;
    this.currentSynthesisId = null;
    this.currentPlaybackId = null;
    this.startingSynthesis = false;
  }

  queueSegments(segments) {
    this.#cancelCurrent();
    this.queue = (Array.isArray(segments) ? segments : [])
      .map((segment, index) => ({ segment, index }))
      .filter(({ segment }) => segment?.ja && !segment?.suppressTts);
    this.#next();
  }

  handleAudioReady(payload) {
    if (!this.#matchesSynthesis(payload)) return;
    this.startingSynthesis = false;
    this.currentSynthesisId = null;
    const resourceId = String(payload?.resource?.id || "").trim();
    if (!resourceId) {
      this.store.setAudioState({ synthesisId: null });
      this.#next();
      return;
    }
    const playbackId = `playback-${this.generation}-${this.current.index}-${Date.now()}`;
    this.currentPlaybackId = playbackId;
    this.store.setAudioState({ synthesisId: null, playbackId });
    this.invoke("play_tts_audio", {
      resourceId,
      playbackId,
      volume: this.volume,
    }).catch((error) => {
      if (this.currentPlaybackId !== playbackId) return;
      this.currentPlaybackId = null;
      this.store.setAudioState({ speaking: false, playbackId: null });
      this.setStatus(`语音播放失败：${error}`, "error");
      this.#next();
    });
  }

  handleSynthesisError(payload) {
    if (!this.#matchesSynthesis(payload)) return;
    this.startingSynthesis = false;
    this.currentSynthesisId = null;
    this.store.setAudioState({ synthesisId: null });
    this.setStatus(payload?.error?.message || "语音合成失败，已继续字幕。", "error");
    this.#next();
  }

  handleSynthesisCancelled(payload) {
    if (!this.#matchesSynthesis(payload)) return;
    this.startingSynthesis = false;
    this.currentSynthesisId = null;
    this.store.setAudioState({ synthesisId: null });
  }

  handlePlaybackState(payload) {
    if (!this.currentPlaybackId || payload?.playbackId !== this.currentPlaybackId) return;
    const state = String(payload?.state || "");
    if (state === "started") {
      this.store.setAudioState({ speaking: true });
      return;
    }
    if (!["finished", "stopped", "error"].includes(state)) return;
    this.currentPlaybackId = null;
    this.store.setAudioState({ speaking: false, playbackId: null });
    if (state === "error") this.setStatus(payload?.error || "语音播放失败。", "error");
    if (state !== "stopped") this.#next();
  }

  async stop() {
    const synthesisId = this.currentSynthesisId;
    const hadPlayback = Boolean(this.currentPlaybackId);
    this.#clearState();
    const tasks = [];
    if (synthesisId) {
      tasks.push(this.invoke("tts_cancel", { synthesisId }).catch(() => null));
    }
    if (hadPlayback) tasks.push(this.invoke("stop_tts_audio", {}).catch(() => null));
    await Promise.all(tasks);
  }

  async setVolume(volume) {
    this.volume = Math.max(0, Math.min(1, Number(volume) || 0));
    await this.invoke("set_tts_volume", { volume: this.volume });
  }

  #next() {
    if (this.currentPlaybackId || this.currentSynthesisId || this.startingSynthesis) return;
    const item = this.queue.shift();
    if (!item) {
      this.current = null;
      this.store.setAudioState({ speaking: false, synthesisId: null, playbackId: null });
      return;
    }
    this.current = item;
    const generation = this.generation;
    const segmentId = `audio-segment-${generation}-${item.index}`;
    this.startingSynthesis = true;
    const request = {
      text: item.segment.ja,
      tone: item.segment.tone || null,
      segmentId,
    };
    if (item.segment.audioKey) request.audioKey = item.segment.audioKey;
    this.invoke("tts_synthesize", request).then((accepted) => {
      if (generation !== this.generation || this.current !== item) return;
      const synthesisId = String(accepted?.synthesisId || "").trim();
      if (!this.currentSynthesisId) this.currentSynthesisId = synthesisId;
      this.startingSynthesis = false;
      this.store.setAudioState({ synthesisId: this.currentSynthesisId || null });
    }).catch((error) => {
      if (generation !== this.generation || this.current !== item) return;
      this.startingSynthesis = false;
      this.setStatus(`语音合成请求失败：${error}`, "error");
      this.#next();
    });
  }

  #matchesSynthesis(payload) {
    const synthesisId = String(payload?.synthesisId || "").trim();
    if (!synthesisId || !this.current) return false;
    if (this.currentSynthesisId === synthesisId) return true;
    if (!this.currentSynthesisId && this.startingSynthesis) {
      this.currentSynthesisId = synthesisId;
      this.store.setAudioState({ synthesisId });
      return true;
    }
    return false;
  }

  #cancelCurrent() {
    const synthesisId = this.currentSynthesisId;
    const hadPlayback = Boolean(this.currentPlaybackId);
    this.#clearState();
    if (synthesisId) this.invoke("tts_cancel", { synthesisId }).catch(() => {});
    if (hadPlayback) this.invoke("stop_tts_audio", {}).catch(() => {});
  }

  #clearState() {
    this.generation += 1;
    this.queue = [];
    this.current = null;
    this.currentSynthesisId = null;
    this.currentPlaybackId = null;
    this.startingSynthesis = false;
    this.store.setAudioState({ speaking: false, synthesisId: null, playbackId: null });
  }
}
