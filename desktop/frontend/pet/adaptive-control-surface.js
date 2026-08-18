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

function measuredControlHeights({ bubble, bubbleHeader, bubbleBody, bubbleCopy, composer, input, contract, getStyle }) {
  const inputStyle = getStyle(input);
  const visibleInputHeight = input.style.height;
  const visibleInputOverflow = input.dataset.overflow;
  input.style.height = "0px";
  const naturalScrollHeight = input.scrollHeight;
  const text = textareaMetrics({
    scrollHeight: naturalScrollHeight,
    lineHeight: px(inputStyle.lineHeight) || px(inputStyle.fontSize) * 1.5,
    paddingBlock: px(inputStyle.paddingTop) + px(inputStyle.paddingBottom),
    maxRows: contract.controlPanel.inputMaxRows,
  });
  input.style.height = visibleInputHeight;
  if (visibleInputOverflow === undefined) delete input.dataset.overflow;
  else input.dataset.overflow = visibleInputOverflow;

  const composerStyle = getStyle(composer);
  const accessoryHeight = clamp(Number(composer.dataset.accessoryHeight) || 0, 0, 60);
  const inputHeight = clamp(
    Math.ceil(text.height + frameHeight(composerStyle) + accessoryHeight),
    contract.controlPanel.inputBaseHeight,
    contract.controlPanel.inputMaxHeight,
  );

  const bubbleStyle = getStyle(bubble);
  const bodyStyle = getStyle(bubbleBody);
  const bubbleHeight = bubbleSurfaceHeight({
    contentHeight: bubbleCopy.scrollHeight,
    headerHeight: bubbleHeader.offsetHeight,
    chromeHeight: frameHeight(bubbleStyle),
    contentGap: px(bodyStyle.marginTop),
    minimum: contract.controlPanel.bubbleMinHeight,
    maximum: contract.controlPanel.bubbleMaxHeight.maximum,
  });
  return Object.freeze({
    measurements: Object.freeze({ bubbleHeight, inputHeight }),
    inputVisual: Object.freeze({ height: text.height, overflow: text.overflow }),
    accessoryHeight,
  });
}

export function createAdaptiveControlSurface({
  root,
  bubble,
  bubbleHeader,
  bubbleBody,
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
  if (!root || !bubble || !bubbleHeader || !bubbleBody || !bubbleCopy || !composer || !input || !contract || !layoutController) {
    throw new Error("adaptive control surface requires complete DOM and layout dependencies");
  }
  let disposed = false;
  let pendingFrame = null;
  let refreshPromise = Promise.resolve({ applied: false });
  let lastRequest = "";
  let visualPreviewRequested = false;
  let deferNativeRequested = false;
  let interactionTraceRequested = null;

  async function refresh() {
    if (disposed) return Object.freeze({ applied: false, disposed: true });
    const visualPreview = visualPreviewRequested;
    const deferNative = deferNativeRequested;
    const interactionTrace = interactionTraceRequested;
    visualPreviewRequested = false;
    deferNativeRequested = false;
    interactionTraceRequested = null;
    const baseAdjustments = applyControlPanelWidth(root, contract, readAdjustments());
    const measuredControl = measuredControlHeights({
      bubble,
      bubbleHeader,
      bubbleBody,
      bubbleCopy,
      composer,
      input,
      contract,
      getStyle,
    });
    const measured = Object.freeze({
      ...measuredControl.measurements,
      // The settings value is the exact conversation bubble height. Reply length only controls
      // the inner scrollbar; it must never resize the outer bubble while a conversation is active.
      bubbleHeight: baseAdjustments.bubbleMaxHeight,
    });
    const adjustments = Object.freeze({
      ...baseAdjustments,
      inputBarOffset: Math.min(
        contract.controlPanel.inputBarOffset.maximum,
        baseAdjustments.inputBarOffset + measuredControl.accessoryHeight,
      ),
    });
    const requestKey = JSON.stringify([adjustments, measured, measuredControl.inputVisual]);
    if (requestKey === lastRequest) return Object.freeze({ applied: false, unchanged: true });
    lastRequest = requestKey;
    return layoutController.transition(PRODUCT_LAYOUT_STATE, "adaptive-control-surface", {
      adjustments,
      measurements: measured,
      commitVisual: () => {
        if (disposed) return;
        input.style.height = `${measuredControl.inputVisual.height}px`;
        input.dataset.overflow = measuredControl.inputVisual.overflow ? "true" : "false";
      },
      visualPreview,
      deferNative,
      interactionTrace,
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
    flush({ visualPreview = false, deferNative = false, interactionTrace = null } = {}) {
      visualPreviewRequested ||= Boolean(visualPreview);
      deferNativeRequested ||= Boolean(deferNative);
      interactionTraceRequested = interactionTrace || interactionTraceRequested;
      if (pendingFrame !== null) {
        cancelFrame(pendingFrame);
        pendingFrame = null;
        refreshPromise = refresh().catch(() => Object.freeze({ applied: false, failed: true }));
      }
      return refreshPromise;
    },
    resetInput() {
      lastRequest = "";
      schedule();
    },
    invalidate({ visualPreview = false, deferNative = false, interactionTrace = null } = {}) {
      visualPreviewRequested ||= Boolean(visualPreview);
      deferNativeRequested ||= Boolean(deferNative);
      interactionTraceRequested = interactionTrace || interactionTraceRequested;
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
