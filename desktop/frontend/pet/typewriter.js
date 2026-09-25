function normalizeSegments(segments) {
  return Array.isArray(segments)
    ? segments.filter((segment) => segment && typeof segment === "object")
    : [];
}

function normalizeLanguage(language) {
  return ["ja", "bilingual", "bilingual_ja"].includes(language) ? language : "zh";
}

export function selectSegmentTracks(segment, language = "zh") {
  const text = typeof segment?.text === "string" ? segment.text : "";
  const translation = typeof segment?.translation === "string" ? segment.translation : "";
  if (language === "bilingual" || language === "bilingual_ja") {
    const tracks = language === "bilingual_ja" ? [text.trim(), translation.trim()] : [translation.trim(), text.trim()];
    return [...new Set(tracks.filter(Boolean))];
  }
  return [normalizeLanguage(language) === "zh" && translation.trim() ? translation : text];
}

export function selectSegmentText(segment, language = "zh") {
  return selectSegmentTracks(segment, language).join("\n");
}

export function createTypewriter({
  intervalMs = 28,
  segmentPauseMs = 160,
  setTimer = (callback, delay) => window.setTimeout(callback, delay),
  clearTimer = (timer) => window.clearTimeout(timer),
  language = "zh",
  onStart = () => {},
  onText = () => {},
  onSegment = () => {},
  onSegmentComplete = () => {},
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
    const revision = run.segmentRevision;
    const advance = () => {
      if (run.sequence !== sequence || revision !== run.segmentRevision) return;
      if (run.segmentIndex + 1 >= run.segments.length) return complete(run);
      timer = setTimer(() => {
        timer = null;
        if (run.sequence !== sequence) return;
        run.segmentIndex += 1;
        typeSegment(run);
      }, run.pauseDelay);
    };
    const gate = onSegmentComplete(run.segments[run.segmentIndex], run.segmentIndex);
    if (gate && typeof gate.then === "function") Promise.resolve(gate).then(advance, advance);
    else advance();
  }

  function typeSegment(run) {
    if (run.sequence !== sequence) return;
    const segment = run.segments[run.segmentIndex];
    if (!segment) return complete(run);
    const segmentRevision = ++run.segmentRevision;
    run.waitingForStart = true;
    const begin = () => {
      if (run.sequence !== sequence || segmentRevision !== run.segmentRevision) return;
      run.waitingForStart = false;
      run.openedIndex = run.segmentIndex;
      const tracks = selectSegmentTracks(segment, selectedLanguage);
      run.text = tracks.join("\n");
      run.visible = "";
      run.characterTracks = tracks.map((text) => Array.from(text));
      run.characterCount = Math.max(0, ...run.characterTracks.map((characters) => characters.length));
      run.characterIndex = 0;
      onText("", Object.freeze({ reason: "segment", forceEnd: true }));
      if (run.characterCount === 0) {
        run.visible = run.text;
        if (run.text) onText(run.visible, Object.freeze({ reason: "typing", forceEnd: true }));
        scheduleNextSegment(run);
        return;
      }
      const tick = () => {
        timer = null;
        if (run.sequence !== sequence || segmentRevision !== run.segmentRevision) return;
        run.characterIndex += 1;
        const subtitleTracks = run.characterTracks
          .map((characters) => characters.slice(0, run.characterIndex).join(""));
        run.visible = subtitleTracks.join("\n");
        onText(run.visible, Object.freeze({ reason: "typing", forceEnd: false, subtitleTracks, fullSubtitleTracks: tracks, subtitleLanguage: selectedLanguage }));
        if (run.characterIndex >= run.characterCount) scheduleNextSegment(run);
        else timer = setTimer(tick, run.typingDelay);
      };
      timer = setTimer(tick, run.typingDelay);
    };
    if (run.gateIndex !== run.segmentIndex) {
      run.gateIndex = run.segmentIndex;
      run.segmentGate = onSegment(segment, run.segmentIndex);
    }
    const prepared = run.openedIndex === run.segmentIndex ? null : run.segmentGate;
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
        characterTracks: [],
        characterCount: 0,
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
      if (!active || active.waitingForStart) return false;
      const run = active;
      clearActiveTimer();
      run.segmentRevision += 1;
      run.skipped = true;
      run.characterIndex = run.characterCount;
      run.visible = run.text;
      onText(run.visible, Object.freeze({
        reason: "skip", forceEnd: true,
        subtitleTracks: run.characterTracks.map((characters) => characters.join("")),
        subtitleLanguage: selectedLanguage,
      }));
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
