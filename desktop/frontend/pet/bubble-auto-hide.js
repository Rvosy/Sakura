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
  let deferred = false;
  let hidden = false;
  let revision = 0;
  let disposed = false;

  function clear() {
    revision += 1;
    if (timer != null) clearTimer(timer);
    timer = null;
  }

  function schedule() {
    clear();
    if (disposed || !enabled || !settled || hovered || deferred || hidden) return;
    const scheduledRevision = revision;
    timer = setTimer(() => {
      if (scheduledRevision !== revision || disposed) return;
      timer = null;
      if (!enabled || !settled || hovered || deferred) return;
      hidden = true;
      onHidden();
    }, delay);
  }

  return Object.freeze({
    configure(nextEnabled) {
      if (disposed) return;
      enabled = Boolean(nextEnabled);
      if (!enabled && hidden) {
        hidden = false;
        onShown();
      }
      schedule();
    },
    notifyBusy() {
      if (disposed) return;
      settled = false;
      clear();
      if (hidden) {
        hidden = false;
        onShown();
      }
    },
    notifySettled() {
      if (disposed) return;
      settled = true;
      schedule();
    },
    setHovered(value) {
      if (disposed) return;
      hovered = Boolean(value);
      schedule();
    },
    setDeferred(value) {
      if (disposed) return;
      deferred = Boolean(value);
      schedule();
    },
    show() {
      if (disposed) return;
      clear();
      if (hidden) {
        hidden = false;
        onShown();
      }
      schedule();
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      clear();
    },
    snapshot() {
      return Object.freeze({ enabled, settled, hovered, deferred, hidden, scheduled: timer != null, disposed });
    },
  });
}
