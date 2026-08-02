function normalizeSegments(segments) {
  return Array.isArray(segments)
    ? segments.filter((segment) => segment && typeof segment === "object")
    : [];
}

function normalizeLanguage(language) {
  return language === "ja" ? "ja" : "zh";
}

export function selectSegmentText(segment, language = "zh") {
  const text = typeof segment?.text === "string" ? segment.text : "";
  const translation = typeof segment?.translation === "string" ? segment.translation : "";
  return normalizeLanguage(language) === "zh" && translation.trim() ? translation : text;
}

export function createTypewriter({
  intervalMs = 28,
  segmentPauseMs = 160,
  setTimer = (callback, delay) => window.setTimeout(callback, delay),
  clearTimer = (timer) => window.clearTimeout(timer),
  language = "zh",
  reducedMotion = false,
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
  let selectedLanguage = normalizeLanguage(language);

  function clearActiveTimer() {
    if (timer != null) clearTimer(timer);
    timer = null;
  }

  function complete(run) {
    if (run.sequence !== sequence) return;
    clearActiveTimer();
    active = null;
    onComplete(Object.freeze({ skipped: run.skipped }));
  }

  function scheduleNextSegment(run) {
    if (run.sequence !== sequence) return;
    if (run.segmentIndex + 1 >= run.segments.length) return complete(run);
    timer = setTimer(() => {
      timer = null;
      if (run.sequence !== sequence) return;
      run.segmentIndex += 1;
      typeSegment(run);
    }, run.pauseDelay);
  }

  function typeSegment(run) {
    if (run.sequence !== sequence) return;
    const segment = run.segments[run.segmentIndex];
    if (!segment) return complete(run);
    const segmentRevision = ++run.segmentRevision;
    const begin = () => {
      if (run.sequence !== sequence || segmentRevision !== run.segmentRevision) return;
      run.text = selectSegmentText(segment, selectedLanguage);
      run.visible = "";
      run.characters = Array.from(run.text);
      run.characterIndex = 0;
      onText("", Object.freeze({ reason: "segment", forceEnd: true }));
      if (reducedMotion || run.characters.length === 0) {
        run.visible = run.text;
        if (run.text) onText(run.visible, Object.freeze({ reason: "typing", forceEnd: true }));
        scheduleNextSegment(run);
        return;
      }
      const tick = () => {
        timer = null;
        if (run.sequence !== sequence || segmentRevision !== run.segmentRevision) return;
        run.visible += run.characters[run.characterIndex++];
        onText(run.visible, Object.freeze({ reason: "typing", forceEnd: false }));
        if (run.characterIndex >= run.characters.length) scheduleNextSegment(run);
        else timer = setTimer(tick, run.typingDelay);
      };
      timer = setTimer(tick, run.typingDelay);
    };
    const prepared = onSegment(segment, run.segmentIndex);
    if (prepared && typeof prepared.then === "function") {
      Promise.resolve(prepared).then(begin, begin);
    } else begin();
  }

  return Object.freeze({
    start(segments) {
      sequence += 1;
      clearActiveTimer();
      const normalized = normalizeSegments(segments);
      onStart();
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
        text: "",
        characters: [],
        characterIndex: 0,
        segmentRevision: 0,
        skipped: false,
        typingDelay,
        pauseDelay,
      };
      typeSegment(active);
      return true;
    },
    skip() {
      if (!active) return false;
      const run = active;
      clearActiveTimer();
      run.segmentRevision += 1;
      run.skipped = true;
      run.characterIndex = run.characters.length;
      run.visible = run.text;
      onText(run.visible, Object.freeze({ reason: "skip", forceEnd: true }));
      scheduleNextSegment(run);
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
    updateLanguage(nextLanguage) {
      const normalized = normalizeLanguage(nextLanguage);
      if (normalized === selectedLanguage) return false;
      selectedLanguage = normalized;
      if (!active) return true;
      clearActiveTimer();
      typeSegment(active);
      return true;
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
