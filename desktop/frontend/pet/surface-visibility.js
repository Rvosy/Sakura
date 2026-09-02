export const SURFACE_VISIBILITY_FADE_MS = 220;
export const SURFACE_HOVER_EXIT_GRACE_MS = 80;

const BUBBLE_IDLE_PHASES = new Set(["ready", "settled", "error"]);
const BUBBLE_ACTIVE_PHASES = new Set(["thinking", "typing"]);

export function validateBubbleAutoHideSettings(values) {
  if (
    !values
    || typeof values !== "object"
    || Array.isArray(values)
    || typeof values.autoHideEnabled !== "boolean"
    || !Number.isSafeInteger(values.autoHideDelaySeconds)
    || values.autoHideDelaySeconds < 1
    || values.autoHideDelaySeconds > 120
  ) throw new Error("invalid bubble auto-hide settings");
  return Object.freeze({
    autoHideEnabled: values.autoHideEnabled,
    autoHideDelaySeconds: values.autoHideDelaySeconds,
  });
}

export function createSurfaceHoverTracker({
  onHoverChange,
  exitGraceMs = SURFACE_HOVER_EXIT_GRACE_MS,
  setTimer = (callback, delay) => globalThis.setTimeout(callback, delay),
  clearTimer = (handle) => globalThis.clearTimeout(handle),
} = {}) {
  if (typeof onHoverChange !== "function") {
    throw new Error("surface hover tracker requires a hover callback");
  }
  if (!Number.isFinite(exitGraceMs) || exitGraceMs < 0) {
    throw new Error("surface hover exit grace must be non-negative");
  }
  const hovered = new Set();
  let exitTimer = null;
  let published = false;
  let disposed = false;

  function cancelExit() {
    if (exitTimer === null) return;
    clearTimer(exitTimer);
    exitTimer = null;
  }

  function publish(value) {
    const next = Boolean(value);
    if (next === published || disposed) return;
    published = next;
    onHoverChange(next);
  }

  return Object.freeze({
    enter(name) {
      if (disposed) return;
      hovered.add(String(name));
      cancelExit();
      publish(true);
    },
    leave(name) {
      if (disposed) return;
      hovered.delete(String(name));
      if (hovered.size > 0) return;
      cancelExit();
      exitTimer = setTimer(() => {
        exitTimer = null;
        if (hovered.size === 0) publish(false);
      }, exitGraceMs);
    },
    snapshot() {
      return Object.freeze({
        active: published,
        hovered: Object.freeze([...hovered]),
        exitPending: exitTimer !== null,
      });
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      cancelExit();
      hovered.clear();
    },
  });
}

export async function waitForSurfaceFadeCompletion(element, {
  reducedMotion = false,
  fadeMs = SURFACE_VISIBILITY_FADE_MS,
  setTimer = (callback, delay) => globalThis.setTimeout(callback, delay),
  clearTimer = (handle) => globalThis.clearTimeout(handle),
  requestFrame = (callback) => globalThis.requestAnimationFrame(callback),
} = {}) {
  if (!element?.addEventListener || !element?.removeEventListener) {
    throw new Error("surface fade requires an event target");
  }
  if (!reducedMotion) {
    await new Promise((resolve) => {
      let settled = false;
      let timeout = null;
      const handleTransitionEnd = (event) => {
        if (event.target === element && event.propertyName === "opacity") finish();
      };
      const finish = () => {
        if (settled) return;
        settled = true;
        element.removeEventListener("transitionend", handleTransitionEnd);
        element.removeEventListener("transitioncancel", handleTransitionEnd);
        if (timeout !== null) clearTimer(timeout);
        resolve();
      };
      element.addEventListener("transitionend", handleTransitionEnd);
      element.addEventListener("transitioncancel", handleTransitionEnd);
      timeout = setTimer(finish, fadeMs + 100);
    });
  }
  await new Promise((resolve) => requestFrame(() => requestFrame(resolve)));
}

export function createSurfaceVisibilityController({
  settings,
  onVisibilityChange,
  onError = () => {},
  setTimer = (callback, delay) => globalThis.setTimeout(callback, delay),
  clearTimer = (handle) => globalThis.clearTimeout(handle),
} = {}) {
  if (typeof onVisibilityChange !== "function") {
    throw new Error("surface visibility controller requires a visibility callback");
  }
  let currentSettings = validateBubbleAutoHideSettings(settings);
  let started = false;
  let disposed = false;
  let phase = "booting";
  let hoverActive = false;
  let inputPinned = false;
  let settingsAppearanceActive = false;
  let suspended = false;
  let bubbleVisible = true;
  let inputVisible = true;
  let bubbleTimer = null;

  function report(error) {
    try {
      onError(error);
    } catch {
      // Visibility failure reporting must not break the interaction controller.
    }
  }

  function rollbackPublishedVisibility(kind, visible) {
    if (kind === "bubble" && bubbleVisible === visible) bubbleVisible = !visible;
    if (kind === "input" && inputVisible === visible) inputVisible = !visible;
  }

  function publish(kind, visible) {
    try {
      Promise.resolve(onVisibilityChange(kind, visible)).catch((error) => {
        rollbackPublishedVisibility(kind, visible);
        report(error);
      });
    } catch (error) {
      rollbackPublishedVisibility(kind, visible);
      report(error);
    }
  }

  function setBubbleVisible(visible) {
    const next = Boolean(visible);
    if (next === bubbleVisible) return false;
    bubbleVisible = next;
    publish("bubble", next);
    return true;
  }

  function setInputVisible(visible) {
    const next = Boolean(visible);
    if (next === inputVisible) return false;
    inputVisible = next;
    publish("input", next);
    return true;
  }

  function cancelBubbleTimer() {
    if (bubbleTimer === null) return;
    clearTimer(bubbleTimer);
    bubbleTimer = null;
  }

  function scheduleBubbleHide() {
    cancelBubbleTimer();
    if (
      !started
      || disposed
      || !currentSettings.autoHideEnabled
      || !BUBBLE_IDLE_PHASES.has(phase)
      || hoverActive
      || settingsAppearanceActive
      || !bubbleVisible
    ) return;
    bubbleTimer = setTimer(() => {
      bubbleTimer = null;
      if (
        disposed
        || hoverActive
        || settingsAppearanceActive
        || !currentSettings.autoHideEnabled
        || !BUBBLE_IDLE_PHASES.has(phase)
      ) return;
      setBubbleVisible(false);
    }, currentSettings.autoHideDelaySeconds * 1000);
  }

  function syncInputVisibility() {
    if (!started || disposed) return;
    setInputVisible(!suspended && (hoverActive || inputPinned || settingsAppearanceActive));
  }

  return Object.freeze({
    start(initialPhase = phase) {
      if (started || disposed) return;
      started = true;
      phase = String(initialPhase || "booting");
      setBubbleVisible(true);
      syncInputVisibility();
      scheduleBubbleHide();
    },
    setPhase(value) {
      const next = String(value || "booting");
      if (next === phase || disposed) return;
      phase = next;
      cancelBubbleTimer();
      if (BUBBLE_ACTIVE_PHASES.has(phase)) setBubbleVisible(true);
      else scheduleBubbleHide();
    },
    setHoverActive(value) {
      const next = Boolean(value);
      if (next === hoverActive || disposed) return;
      hoverActive = next;
      if (hoverActive) cancelBubbleTimer();
      else scheduleBubbleHide();
      syncInputVisibility();
    },
    setInputPinned(value) {
      const next = Boolean(value);
      if (next === inputPinned || disposed) return;
      inputPinned = next;
      syncInputVisibility();
    },
    setSettingsAppearanceActive(value) {
      const next = Boolean(value);
      if (next === settingsAppearanceActive || disposed) return;
      settingsAppearanceActive = next;
      cancelBubbleTimer();
      if (settingsAppearanceActive && started) setBubbleVisible(true);
      else scheduleBubbleHide();
      syncInputVisibility();
    },
    setSuspended(value) {
      const next = Boolean(value);
      if (next === suspended || disposed) return;
      suspended = next;
      syncInputVisibility();
    },
    activatePet() {
      if (disposed || !started || bubbleVisible) return false;
      setBubbleVisible(true);
      scheduleBubbleHide();
      return true;
    },
    previewBubble() {
      if (disposed || !started) return false;
      cancelBubbleTimer();
      const changed = setBubbleVisible(true);
      scheduleBubbleHide();
      return changed;
    },
    setSettings(values) {
      if (disposed) return;
      currentSettings = validateBubbleAutoHideSettings(values);
      cancelBubbleTimer();
      if (!currentSettings.autoHideEnabled) setBubbleVisible(true);
      else scheduleBubbleHide();
    },
    snapshot() {
      return Object.freeze({
        bubbleVisible,
        inputVisible,
        hoverActive,
        inputPinned,
        settingsAppearanceActive,
        suspended,
        phase,
        settings: currentSettings,
      });
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      cancelBubbleTimer();
    },
  });
}
