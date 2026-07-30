import { applyControlPanelWidth, PRODUCT_LAYOUT_STATE } from "./layout.js";

function px(value) {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

export function textareaMetrics({ scrollHeight, lineHeight, paddingBlock, maxRows }) {
  const safeLineHeight = Math.max(1, Number(lineHeight) || 1);
  const safePadding = Math.max(0, Number(paddingBlock) || 0);
  const rows = clamp(Math.round(Number(maxRows) || 1), 1, 8);
  const minimum = safeLineHeight + safePadding;
  const maximum = safeLineHeight * rows + safePadding;
  const height = clamp(Math.ceil(Number(scrollHeight) || minimum), Math.ceil(minimum), Math.ceil(maximum));
  return Object.freeze({ height, overflow: Number(scrollHeight) > maximum + 0.5 });
}

export function bubbleSurfaceHeight({ contentHeight, headerHeight, chromeHeight, contentGap, minimum, maximum }) {
  const desired = Math.ceil(
    Math.max(0, Number(contentHeight) || 0)
    + Math.max(0, Number(headerHeight) || 0)
    + Math.max(0, Number(chromeHeight) || 0)
    + Math.max(0, Number(contentGap) || 0),
  );
  return clamp(desired, minimum, maximum);
}

function frameHeight(style) {
  return px(style.paddingTop)
    + px(style.paddingBottom)
    + px(style.borderTopWidth)
    + px(style.borderBottomWidth);
}

function measuredControlHeights({ bubble, bubbleHeader, bubbleCopy, composer, input, contract, getStyle }) {
  const inputStyle = getStyle(input);
  input.style.height = "0px";
  const naturalScrollHeight = input.scrollHeight;
  const text = textareaMetrics({
    scrollHeight: naturalScrollHeight,
    lineHeight: px(inputStyle.lineHeight) || px(inputStyle.fontSize) * 1.5,
    paddingBlock: px(inputStyle.paddingTop) + px(inputStyle.paddingBottom),
    maxRows: contract.controlPanel.inputMaxRows,
  });
  input.style.height = `${text.height}px`;
  input.dataset.overflow = text.overflow ? "true" : "false";

  const composerStyle = getStyle(composer);
  const inputHeight = clamp(
    Math.ceil(text.height + frameHeight(composerStyle)),
    contract.controlPanel.inputBaseHeight,
    contract.controlPanel.inputMaxHeight,
  );

  const bubbleStyle = getStyle(bubble);
  const copyStyle = getStyle(bubbleCopy);
  const bubbleHeight = bubbleSurfaceHeight({
    contentHeight: bubbleCopy.scrollHeight,
    headerHeight: bubbleHeader.offsetHeight,
    chromeHeight: frameHeight(bubbleStyle),
    contentGap: px(copyStyle.marginTop),
    minimum: contract.controlPanel.bubbleMinHeight,
    maximum: contract.controlPanel.bubbleMaxHeight.maximum,
  });
  return Object.freeze({ bubbleHeight, inputHeight });
}

export function createAdaptiveControlSurface({
  root,
  bubble,
  bubbleHeader,
  bubbleCopy,
  composer,
  input,
  contract,
  layoutController,
  readAdjustments,
  getStyle = (element) => window.getComputedStyle(element),
  requestFrame = (callback) => window.requestAnimationFrame(callback),
  cancelFrame = (frame) => window.cancelAnimationFrame(frame),
  ResizeObserverClass = window.ResizeObserver,
} = {}) {
  if (!root || !bubble || !bubbleHeader || !bubbleCopy || !composer || !input || !contract || !layoutController) {
    throw new Error("adaptive control surface requires complete DOM and layout dependencies");
  }
  let disposed = false;
  let pendingFrame = null;
  let refreshPromise = Promise.resolve({ applied: false });
  let lastRequest = "";

  async function refresh() {
    if (disposed) return Object.freeze({ applied: false, disposed: true });
    const adjustments = applyControlPanelWidth(root, contract, readAdjustments());
    const rawMeasured = measuredControlHeights({
      bubble,
      bubbleHeader,
      bubbleCopy,
      composer,
      input,
      contract,
      getStyle,
    });
    const measured = Object.freeze({
      ...rawMeasured,
      bubbleHeight: Math.min(rawMeasured.bubbleHeight, adjustments.bubbleMaxHeight),
    });
    const requestKey = JSON.stringify([adjustments, measured]);
    if (requestKey === lastRequest) return Object.freeze({ applied: false, unchanged: true });
    lastRequest = requestKey;
    return layoutController.transition(PRODUCT_LAYOUT_STATE, "adaptive-control-surface", {
      adjustments,
      measurements: measured,
    });
  }

  function schedule() {
    if (disposed || pendingFrame !== null) return;
    pendingFrame = requestFrame(() => {
      pendingFrame = null;
      refreshPromise = refresh().catch(() => Object.freeze({ applied: false, failed: true }));
    });
  }

  const observer = typeof ResizeObserverClass === "function"
    ? new ResizeObserverClass(schedule)
    : null;
  observer?.observe(bubbleCopy);
  observer?.observe(input);

  return Object.freeze({
    schedule,
    refresh,
    settle: () => refreshPromise,
    resetInput() {
      input.style.height = "";
      input.dataset.overflow = "false";
      lastRequest = "";
      schedule();
    },
    invalidate() {
      lastRequest = "";
      schedule();
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      if (pendingFrame !== null) cancelFrame(pendingFrame);
      pendingFrame = null;
      observer?.disconnect();
    },
  });
}
