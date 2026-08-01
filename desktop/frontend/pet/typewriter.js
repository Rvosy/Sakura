function normalizeSegments(segments) {
  return Array.isArray(segments)
    ? segments.filter((segment) => segment && typeof segment.text === "string" && segment.text.length > 0)
    : [];
}

export function createTypewriter({
  intervalMs = 28,
  segmentPauseMs = 160,
  setTimer = (callback, delay) => window.setTimeout(callback, delay),
  clearTimer = (timer) => window.clearTimeout(timer),
  onStart = () => {},
  onText = () => {},
  onSegment = () => {},
  onComplete = () => {},
} = {}) {
  let typingDelay = Math.max(5, Math.min(200, Number(intervalMs) || 28));
  let pauseDelay = Math.max(0, Math.min(3000, Number(segmentPauseMs) || 0));
  let sequence = 0;
  let timer = null;
  let active = null;

  function clearActiveTimer() {
    if (timer != null) clearTimer(timer);
    timer = null;
  }

  function complete(run, skipped) {
    if (run.sequence !== sequence) return;
    clearActiveTimer();
    active = null;
    onComplete(Object.freeze({ skipped }));
  }

  function typeSegment(run) {
    if (run.sequence !== sequence) return;
    const segment = run.segments[run.segmentIndex];
    if (!segment) return complete(run, false);
    onSegment(segment, run.segmentIndex);
    const prefix = run.segmentIndex === 0 ? "" : "\n";
    if (prefix) {
      run.visible += prefix;
      onText(run.visible, Object.freeze({ reason: "typing", forceEnd: false }));
    }
    const characters = Array.from(segment.text);
    let characterIndex = 0;
    const tick = () => {
      if (run.sequence !== sequence) return;
      if (characterIndex < characters.length) {
        run.visible += characters[characterIndex++];
        onText(run.visible, Object.freeze({ reason: "typing", forceEnd: false }));
        timer = setTimer(tick, run.typingDelay);
        return;
      }
      run.segmentIndex += 1;
      if (run.segmentIndex >= run.segments.length) return complete(run, false);
      timer = setTimer(() => typeSegment(run), run.pauseDelay);
    };
    timer = setTimer(tick, run.typingDelay);
  }

  return Object.freeze({
    start(segments) {
      sequence += 1;
      clearActiveTimer();
      const normalized = normalizeSegments(segments);
      onStart();
      onText("", Object.freeze({ reason: "start", forceEnd: true }));
      if (!normalized.length) {
        active = null;
        onComplete(Object.freeze({ skipped: false }));
        return false;
      }
      active = {
        sequence,
        segments: normalized,
        segmentIndex: 0,
        visible: "",
        typingDelay,
        pauseDelay,
      };
      typeSegment(active);
      return true;
    },
    skip() {
      if (!active) return false;
      const run = active;
      sequence += 1;
      clearActiveTimer();
      const fullText = run.segments.map((segment) => segment.text).join("\n");
      const last = run.segments.at(-1);
      onSegment(last, run.segments.length - 1);
      onText(fullText, Object.freeze({ reason: "skip", forceEnd: true }));
      active = null;
      onComplete(Object.freeze({ skipped: true }));
      return true;
    },
    cancel(replacement = "") {
      sequence += 1;
      clearActiveTimer();
      active = null;
      onText(String(replacement ?? ""), Object.freeze({ reason: "cancel", forceEnd: false }));
    },
    updateTiming({ intervalMs: nextInterval, segmentPauseMs: nextPause } = {}) {
      typingDelay = Math.max(5, Math.min(200, Number(nextInterval) || 28));
      pauseDelay = Math.max(0, Math.min(3000, Number(nextPause) || 0));
    },
    dispose() {
      sequence += 1;
      clearActiveTimer();
      active = null;
    },
    isActive() {
      return active !== null;
    },
  });
}
