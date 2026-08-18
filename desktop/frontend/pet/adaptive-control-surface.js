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

export function composerInputMetrics({
  value,
  scrollHeight,
  lineHeight,
  paddingBlock,
  frameHeight: composerFrameHeight,
  expanded,
  expandedRows,
  composing = false,
  minExpandedRows,
  maxRows,
  toolbarHeight,
  expandedGap,
}) {
  const safeLineHeight = Math.max(1, Number(lineHeight) || 1);
  const safePadding = Math.max(0, Number(paddingBlock) || 0);
  const minimumRows = clamp(Math.round(Number(minExpandedRows) || 1), 1, 3);
  const maximumRows = clamp(Math.round(Number(maxRows) || 3), minimumRows, 8);
  const contentHeight = Math.max(safeLineHeight, (Number(scrollHeight) || 0) - safePadding);
  const draft = String(value ?? "");
  const measuredRows = Math.ceil((contentHeight - 0.5) / safeLineHeight);
  const explicitRows = draft.split("\n").length;
  const naturalRows = clamp(Math.max(measuredRows, explicitRows), 1, maximumRows + 1);
  // A manual line break is layout intent even before any visible glyph is entered. Once expanded,
  // the latch is released only when the textarea value is genuinely empty.
  const hasManualLineBreak = draft.includes("\n");
  const hasContent = draft.length > 0;
  const nextExpanded = composing
    ? Boolean(expanded)
    : hasContent && (Boolean(expanded) || hasManualLineBreak || naturalRows > 1);
  const visibleRows = nextExpanded
    ? composing
      ? clamp(Math.round(Number(expandedRows) || minimumRows), minimumRows, maximumRows)
      : clamp(naturalRows, minimumRows, maximumRows)
    : 1;
  const textHeight = Math.ceil(safeLineHeight * visibleRows + safePadding);
  const height = Math.ceil(
    textHeight
      + Math.max(0, Number(composerFrameHeight) || 0)
      + (nextExpanded
        ? Math.max(0, Number(toolbarHeight) || 0) + Math.max(0, Number(expandedGap) || 0)
        : 0),
  );
  return Object.freeze({
    expanded: nextExpanded,
    height,
    textHeight,
    overflow: naturalRows > maximumRows,
    state: nextExpanded ? `expanded-${visibleRows}` : "collapsed",
    visibleRows,
  });
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

export function composerMotionDirection(beforeHeight, afterHeight) {
  const delta = Number(afterHeight) - Number(beforeHeight);
  if (!Number.isFinite(delta) || Math.abs(delta) <= 0.5) return "stable";
  return delta > 0 ? "expand" : "contract";
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
  const naturalText = textareaMetrics({
    scrollHeight: naturalScrollHeight,
    lineHeight: px(inputStyle.lineHeight) || px(inputStyle.fontSize) * 1.5,
    paddingBlock: px(inputStyle.paddingTop) + px(inputStyle.paddingBottom),
    maxRows: contract.controlPanel.inputMaxRows,
  });
  input.style.height = visibleInputHeight;
  if (visibleInputOverflow === undefined) delete input.dataset.overflow;
  else input.dataset.overflow = visibleInputOverflow;

  const composerStyle = getStyle(composer);
  const text = composerInputMetrics({
    value: input.value,
    scrollHeight: naturalScrollHeight,
    lineHeight: px(inputStyle.lineHeight) || px(inputStyle.fontSize) * 1.5,
    paddingBlock: px(inputStyle.paddingTop) + px(inputStyle.paddingBottom),
    frameHeight: frameHeight(composerStyle),
    expanded: composer.dataset.inputExpanded === "true",
    expandedRows: Number.parseInt(composer.dataset.inputState?.split("-").at(-1), 10),
    composing: composer.dataset.composing === "true",
    minExpandedRows: contract.controlPanel.inputExpandedMinRows,
    maxRows: contract.controlPanel.inputMaxRows,
    toolbarHeight: contract.controlPanel.inputToolbarHeight,
    expandedGap: contract.controlPanel.inputExpandedGap,
  });
  const inputHeight = clamp(
    text.height,
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
    inputVisual: Object.freeze({
      height: text.textHeight,
      overflow: text.overflow || naturalText.overflow,
      expanded: text.expanded,
      state: text.state,
    }),
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
  let composerAnimation = null;
  let childAnimations = [];

  function captureVisualRects() {
    if (typeof composer.getBoundingClientRect !== "function") return null;
    const controls = typeof composer.querySelectorAll === "function"
      ? [...composer.querySelectorAll("#composer-attachment, #composer-send")]
      : [];
    const elements = [input, ...controls];
    return Object.freeze({
      composer: composer.getBoundingClientRect(),
      children: elements.map((element) => Object.freeze({ element, rect: element.getBoundingClientRect() })),
    });
  }

  function animateCommittedLayout(before) {
    if (disposed || !before || typeof composer.animate !== "function") return;
    const reducedMotion = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches === true;
    composerAnimation?.cancel();
    for (const animation of childAnimations) animation.cancel();
    childAnimations = [];
    composerAnimation = null;
    if (reducedMotion) return;
    try {
      const after = composer.getBoundingClientRect();
      const direction = composerMotionDirection(before.composer.height, after.height);
      if (direction !== "stable") {
        composerAnimation = composer.animate(
          [{ height: `${before.composer.height}px` }, { height: `${after.height}px` }],
          { duration: 220, easing: "cubic-bezier(.22, 1, .36, 1)" },
        );
      }
      for (const item of before.children) {
        const next = item.element.getBoundingClientRect();
        const dx = item.rect.left - next.left;
        const dy = item.rect.top - next.top;
        if (Math.abs(dx) <= 0.5 && Math.abs(dy) <= 0.5) continue;
        childAnimations.push(item.element.animate(
          [{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "translate(0, 0)" }],
          { duration: 220, easing: "cubic-bezier(.22, 1, .36, 1)" },
        ));
      }
    } catch {
      composerAnimation?.cancel();
      for (const animation of childAnimations) animation.cancel();
      composerAnimation = null;
      childAnimations = [];
    }
  }

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
    const adjustments = baseAdjustments;
    const requestKey = JSON.stringify([adjustments, measured, measuredControl.inputVisual]);
    if (requestKey === lastRequest) return Object.freeze({ applied: false, unchanged: true });
    lastRequest = requestKey;
    return layoutController.transition(PRODUCT_LAYOUT_STATE, "adaptive-control-surface", {
      adjustments,
      measurements: measured,
      commitVisual: () => {
        if (disposed) return;
        const visualBefore = captureVisualRects();
        input.style.height = `${measuredControl.inputVisual.height}px`;
        input.dataset.overflow = measuredControl.inputVisual.overflow ? "true" : "false";
        composer.dataset.inputExpanded = measuredControl.inputVisual.expanded ? "true" : "false";
        composer.dataset.inputState = measuredControl.inputVisual.state;
        requestFrame(() => animateCommittedLayout(visualBefore));
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
    setComposing(value) {
      composer.dataset.composing = value ? "true" : "false";
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
      composerAnimation?.cancel();
      for (const animation of childAnimations) animation.cancel();
    },
  });
}
