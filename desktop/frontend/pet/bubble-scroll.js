export function createBubbleScroll({
  viewport,
  bottomThresholdPx = 12,
  renderText = (target, text) => {
    target.textContent = text;
  },
} = {}) {
  if (!viewport || typeof viewport.addEventListener !== "function") {
    throw new Error("bubble scroll viewport is required");
  }
  const threshold = Math.max(0, Math.min(64, Number(bottomThresholdPx) || 0));
  let following = true;
  let disposed = false;
  let lastText = "";
  let lastOptions = {};
  let lastWidth = viewport.clientWidth;
  const ResizeObserverClass = viewport.ownerDocument?.defaultView?.ResizeObserver;
  const resizeObserver = ResizeObserverClass ? new ResizeObserverClass(() => {
    if (disposed || viewport.clientWidth === lastWidth) return;
    lastWidth = viewport.clientWidth;
    updateText(lastText, lastOptions);
  }) : null;
  resizeObserver?.observe(viewport);

  function distanceFromEnd() {
    return Math.max(0, Number(viewport.scrollHeight) - Number(viewport.clientHeight) - Number(viewport.scrollTop));
  }

  function isNearEnd() {
    return distanceFromEnd() <= threshold;
  }

  function scrollToEnd() {
    viewport.scrollTop = Math.max(0, Number(viewport.scrollHeight) - Number(viewport.clientHeight));
    following = true;
  }

  function handleScroll() {
    if (!disposed) following = isNearEnd();
  }

  viewport.addEventListener("scroll", handleScroll, { passive: true });

  function updateText(text, options = {}) {
    if (disposed) return;
    const { forceEnd = false, subtitleTracks = [], fullSubtitleTracks = subtitleTracks, subtitleLanguage } = options;
    lastText = String(text ?? "");
    lastOptions = { ...options, forceEnd: false };
    const wasNearEnd = isNearEnd();
    const shouldFollow = Boolean(forceEnd) || (following && wasNearEnd);
    renderText(viewport, lastText, subtitleTracks, fullSubtitleTracks, subtitleLanguage);
    if (shouldFollow) scrollToEnd();
    else following = false;
  }

  return Object.freeze({
    beginReply() {
      if (disposed) return;
      following = true;
      scrollToEnd();
    },
    updateText,
    refresh() { updateText(lastText, lastOptions); },
    dispose() {
      if (disposed) return;
      disposed = true;
      viewport.removeEventListener("scroll", handleScroll);
      resizeObserver?.disconnect();
    },
    snapshot() {
      return Object.freeze({ following, nearEnd: isNearEnd(), disposed });
    },
  });
}
