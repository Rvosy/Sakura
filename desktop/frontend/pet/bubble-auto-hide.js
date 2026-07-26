export function createBubbleAutoHide({
  delayMs = 12000,
  setTimer = (callback, delay) => window.setTimeout(callback, delay),
  clearTimer = (timer) => window.clearTimeout(timer),
  onHidden = () => {},
  onShown = () => {},
} = {}) {
  const delay = Math.max(1000, Math.min(120000, Number(delayMs) || 12000));
  let timer = null;
  let enabled = true;
  let settled = false;
  let hovered = false;
  let hidden = false;

  function clear() {
    if (timer != null) clearTimer(timer);
    timer = null;
  }

  function schedule() {
    clear();
    if (!enabled || !settled || hovered || hidden) return;
    timer = setTimer(() => {
      timer = null;
      if (!enabled || !settled || hovered) return;
      hidden = true;
      onHidden();
    }, delay);
  }

  return Object.freeze({
    configure(nextEnabled) {
      enabled = Boolean(nextEnabled);
      if (!enabled && hidden) {
        hidden = false;
        onShown();
      }
      schedule();
    },
    notifyBusy() {
      settled = false;
      clear();
      if (hidden) {
        hidden = false;
        onShown();
      }
    },
    notifySettled() {
      settled = true;
      schedule();
    },
    setHovered(value) {
      hovered = Boolean(value);
      schedule();
    },
    show() {
      clear();
      if (hidden) {
        hidden = false;
        onShown();
      }
      schedule();
    },
    dispose() {
      clear();
    },
    snapshot() {
      return Object.freeze({ enabled, settled, hovered, hidden, scheduled: timer != null });
    },
  });
}
