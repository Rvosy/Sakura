import { createBubbleAutoHide } from "./bubble-auto-hide.js";

const BUSY_PHASES = new Set(["booting", "thinking", "typing", "reconnecting"]);

export function createPresentationVisibility({
  autoHideEnabled = true,
  delayMs = 12000,
  setTimer,
  clearTimer,
  onBubbleHidden = () => {},
  onBubbleShown = () => {},
  onComposerChanged = () => {},
  onIdle = () => {},
} = {}) {
  let composerOpen = false;
  let composing = false;
  let draft = "";
  let disposed = false;

  const autoHide = createBubbleAutoHide({
    delayMs,
    ...(setTimer ? { setTimer } : {}),
    ...(clearTimer ? { clearTimer } : {}),
    onHidden: () => {
      composerOpen = false;
      onComposerChanged(false);
      onBubbleHidden();
      onIdle();
    },
    onShown: onBubbleShown,
  });
  autoHide.configure(autoHideEnabled);

  function updateDeferral() {
    autoHide.setDeferred(composing || draft.length > 0);
  }

  function setComposerOpen(value) {
    if (disposed) return false;
    autoHide.show();
    composerOpen = Boolean(value);
    onComposerChanged(composerOpen);
    return composerOpen;
  }

  return Object.freeze({
    configureAutoHide(enabled) {
      if (!disposed) autoHide.configure(enabled);
    },
    syncPhase(phase) {
      if (disposed) return;
      if (phase === "settled") autoHide.notifySettled();
      else if (BUSY_PHASES.has(phase)) autoHide.notifyBusy();
    },
    setHovered(value) {
      if (!disposed) autoHide.setHovered(value);
    },
    setInputState({ draft: nextDraft = draft, composing: nextComposing = composing } = {}) {
      if (disposed) return;
      draft = String(nextDraft ?? "");
      composing = Boolean(nextComposing);
      updateDeferral();
    },
    revealComposer() {
      return setComposerOpen(true);
    },
    toggleComposer() {
      return setComposerOpen(!composerOpen);
    },
    showBubble() {
      if (!disposed) autoHide.show();
    },
    restart() {
      if (!disposed) autoHide.notifyBusy();
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      autoHide.dispose();
    },
    snapshot() {
      return Object.freeze({ composerOpen, composing, draft, disposed, autoHide: autoHide.snapshot() });
    },
  });
}
