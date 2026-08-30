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

  return Object.freeze({
    beginReply() {
      if (disposed) return;
      following = true;
      scrollToEnd();
    },
    updateText(text, { forceEnd = false } = {}) {
      if (disposed) return;
      const wasNearEnd = isNearEnd();
      const shouldFollow = Boolean(forceEnd) || (following && wasNearEnd);
      renderText(viewport, String(text ?? ""));
      if (shouldFollow) scrollToEnd();
      else following = false;
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      viewport.removeEventListener("scroll", handleScroll);
    },
    snapshot() {
      return Object.freeze({ following, nearEnd: isNearEnd(), disposed });
    },
  });
}
