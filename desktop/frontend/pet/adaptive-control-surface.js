import { applyControlPanelWidth, PRODUCT_LAYOUT_STATE } from "./layout.js";

export const COMPOSER_MOTION_DURATION_MS = 260;
const COMPOSER_MOTION_EASING = "cubic-bezier(.22, 1, .36, 1)";
const COMPOSER_MOTION_START_LEAD_MS = 40;

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
  attachmentCount = 0,
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
  // WebView2 在分数 DPI 和自定义字体下会把单行 scrollHeight 向上取整 1–2px。
  // 行高本身是离散的，因此按最近行数取整，避免几个字也被误判为换行。
  const measuredRows = Math.max(1, Math.round(contentHeight / safeLineHeight));
  const explicitRows = draft.split("\n").length;
  const naturalRows = clamp(Math.max(measuredRows, explicitRows), 1, maximumRows + 1);
  // A manual line break is layout intent even before any visible glyph is entered. Once expanded,
  // the latch is released only when the textarea value is genuinely empty.
  const hasManualLineBreak = draft.includes("\n");
  const hasContent = draft.length > 0;
  const hasAttachments = Number(attachmentCount) > 0;
  const nextExpanded = hasAttachments || (composing
    ? Boolean(expanded)
    : hasContent && (Boolean(expanded) || hasManualLineBreak || naturalRows > 1));
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

export function composerStagingHeight({
  beforeHeight,
  afterHeight,
  baseHeight,
  toolbarHeight,
  expandedGap,
}) {
  const before = Number(beforeHeight);
  const after = Number(afterHeight);
  const base = Number(baseHeight);
  const toolbarBand = Math.max(0, Number(toolbarHeight) || 0)
    + Math.max(0, Number(expandedGap) || 0);
  if (![before, after, base].every(Number.isFinite) || toolbarBand <= 0) return null;
  const beforeIsBase = Math.abs(before - base) <= 0.75;
  if (!beforeIsBase) return after > before ? before : null;
  if (after <= base) return null;
  const staging = after - toolbarBand;
  return staging >= base && staging < after ? staging : null;
}

function translate(dx, dy) {
  return `translate(${dx}px, ${dy}px)`;
}

function nativeMotionReady(start) {
  try {
    return Promise.resolve(typeof start === "function" ? start() : false).catch(() => false);
  } catch {
    return Promise.resolve(false);
  }
}

function scheduledMotionStart(now) {
  return now() + COMPOSER_MOTION_START_LEAD_MS;
}

function composerMotionTiming(startAtUnixMs, now) {
  return {
    duration: COMPOSER_MOTION_DURATION_MS,
    easing: COMPOSER_MOTION_EASING,
    delay: Number.isFinite(startAtUnixMs) ? startAtUnixMs - now() : 0,
    fill: "backwards",
  };
}

export function composerChildMotionKeyframes({
  dx,
  dy,
  beforeWidth,
  afterWidth,
  beforePaddingLeft,
  afterPaddingLeft,
  beforePaddingRight,
  afterPaddingRight,
}) {
  const start = { transform: translate(dx, dy), offset: 0 };
  const end = { transform: "translate(0, 0)", offset: 1 };
  for (const [property, before, after] of [
    ["width", beforeWidth, afterWidth],
    ["paddingLeft", beforePaddingLeft, afterPaddingLeft],
    ["paddingRight", beforePaddingRight, afterPaddingRight],
  ]) {
    if (![before, after].every(Number.isFinite) || Math.abs(before - after) <= 0.5) continue;
    start[property] = `${before}px`;
    end[property] = `${after}px`;
  }
  return [start, end];
}

function frameHeight(style) {
  return px(style.paddingTop)
    + px(style.paddingBottom)
    + px(style.borderTopWidth)
    + px(style.borderBottomWidth);
}

function naturalTextareaScrollHeight({ composer, input, expanded }) {
  const visibleInputHeight = input.style.height;
  const previousMeasurement = composer.dataset.inputMeasure;
  composer.dataset.inputMeasure = expanded ? "expanded" : "collapsed";
  input.style.height = "0px";
  const scrollHeight = input.scrollHeight;
  input.style.height = visibleInputHeight;
  if (previousMeasurement === undefined) delete composer.dataset.inputMeasure;
  else composer.dataset.inputMeasure = previousMeasurement;
  return scrollHeight;
}

function measuredControlHeights({ bubble, bubbleHeader, bubbleBody, bubbleCopy, composer, input, contract, getStyle }) {
  const inputStyle = getStyle(input);
  const visibleInputOverflow = input.dataset.overflow;
  const currentExpanded = composer.dataset.inputExpanded === "true";
  let naturalScrollHeight = naturalTextareaScrollHeight({
    composer,
    input,
    expanded: currentExpanded,
  });
  if (visibleInputOverflow === undefined) delete input.dataset.overflow;
  else input.dataset.overflow = visibleInputOverflow;

  const composerStyle = getStyle(composer);
  const metrics = (scrollHeight, expanded) => composerInputMetrics({
    value: input.value,
    scrollHeight,
    lineHeight: px(inputStyle.lineHeight) || px(inputStyle.fontSize) * 1.5,
    paddingBlock: px(inputStyle.paddingTop) + px(inputStyle.paddingBottom),
    frameHeight: frameHeight(composerStyle),
    expanded,
    expandedRows: Number.parseInt(composer.dataset.inputState?.split("-").at(-1), 10),
    composing: composer.dataset.composing === "true",
    attachmentCount: Number.parseInt(composer.dataset.attachmentCount || "0", 10),
    minExpandedRows: contract.controlPanel.inputExpandedMinRows,
    maxRows: contract.controlPanel.inputMaxRows,
    toolbarHeight: contract.controlPanel.inputToolbarHeight,
    expandedGap: contract.controlPanel.inputExpandedGap,
  });
  let text = metrics(naturalScrollHeight, currentExpanded);
  // A wrap in the narrow one-row layout selects the expanded layout. Measure that final, wider
  // layout in the same JavaScript task before starting either animation, so one input event has
  // one target height even when the text unwraps again at the wider width.
  if (!currentExpanded && text.expanded) {
    naturalScrollHeight = naturalTextareaScrollHeight({ composer, input, expanded: true });
    text = metrics(naturalScrollHeight, true);
  }
  const naturalText = textareaMetrics({
    scrollHeight: naturalScrollHeight,
    lineHeight: px(inputStyle.lineHeight) || px(inputStyle.fontSize) * 1.5,
    paddingBlock: px(inputStyle.paddingTop) + px(inputStyle.paddingBottom),
    maxRows: contract.controlPanel.inputMaxRows,
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
  startNativeExpansion = null,
  readAdjustments,
  startNativeTransition = null,
  now = () => Date.now(),
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
  let motionGeneration = 0;
  let optimisticExpansion = null;
  let inputMotionActive = false;
  let observerRefreshDeferred = false;

  function captureVisualRects() {
    if (typeof composer.getBoundingClientRect !== "function") return null;
    const controls = typeof composer.querySelectorAll === "function"
      ? [...composer.querySelectorAll("#composer-attachment, #composer-send")]
      : [];
    const elements = [input, ...controls];
    return Object.freeze({
      composer: composer.getBoundingClientRect(),
      children: elements.map((element) => {
        const style = getStyle(element);
        return Object.freeze({
          element,
          rect: element.getBoundingClientRect(),
          paddingLeft: px(style.paddingLeft),
          paddingRight: px(style.paddingRight),
        });
      }),
    });
  }

  function animateCommittedLayout(before, nativeTransition) {
    if (disposed) return;
    delete composer.dataset.inputMotion;
    composer.style.height = Number.isFinite(nativeTransition?.targetHeight)
      ? `${nativeTransition.targetHeight}px`
      : "";
    if (!before || typeof composer.animate !== "function") return;
    const reducedMotion = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches === true;
    composerAnimation?.cancel();
    for (const animation of childAnimations) animation.cancel();
    childAnimations = [];
    composerAnimation = null;
    if (reducedMotion) return;
    try {
      const timing = composerMotionTiming(nativeTransition?.startAtUnixMs, now);
      const after = composer.getBoundingClientRect();
      const direction = composerMotionDirection(before.composer.height, after.height);
      if (direction !== "stable") {
        const visualScale = after.width / Math.max(1, Number(composer.offsetWidth) || after.width);
        const stagingHeight = Number.isFinite(nativeTransition?.stagingHeight)
          ? nativeTransition.stagingHeight * visualScale
          : null;
        const firstHeight = direction === "expand" && stagingHeight !== null
          ? stagingHeight
          : before.composer.height;
        composerAnimation = composer.animate(
          [{ height: `${firstHeight}px` }, { height: `${after.height}px` }],
          timing,
        );
      }
      for (const item of before.children) {
        const next = item.element.getBoundingClientRect();
        const nextStyle = getStyle(item.element);
        const dx = item.rect.left - next.left;
        const isTextarea = item.element === input;
        // Existing text is the vertical anchor. New wrapped rows grow below it; only toolbar
        // controls follow the grid's vertical FLIP displacement.
        const dy = isTextarea ? 0 : item.rect.top - next.top;
        const widthChanged = isTextarea && Math.abs(item.rect.width - next.width) > 0.5;
        const paddingChanged = isTextarea && (
          Math.abs(item.paddingLeft - px(nextStyle.paddingLeft)) > 0.5
          || Math.abs(item.paddingRight - px(nextStyle.paddingRight)) > 0.5
        );
        if (Math.abs(dx) <= 0.5 && Math.abs(dy) <= 0.5
          && !widthChanged && !paddingChanged) continue;
        childAnimations.push(item.element.animate(
          composerChildMotionKeyframes({
            dx,
            dy,
            beforeWidth: isTextarea ? item.rect.width : undefined,
            afterWidth: isTextarea ? next.width : undefined,
            beforePaddingLeft: isTextarea ? item.paddingLeft : undefined,
            afterPaddingLeft: isTextarea ? px(nextStyle.paddingLeft) : undefined,
            beforePaddingRight: isTextarea ? item.paddingRight : undefined,
            afterPaddingRight: isTextarea ? px(nextStyle.paddingRight) : undefined,
          }),
          timing,
        ));
      }
      const generation = motionGeneration;
      const runningAnimations = [composerAnimation, ...childAnimations].filter(Boolean);
      const completions = runningAnimations
        .map((animation) => animation.finished)
        .filter((finished) => finished && typeof finished.then === "function");
      inputMotionActive = completions.length > 0;
      if (inputMotionActive) {
        Promise.allSettled(completions).then(() => {
          if (disposed || generation !== motionGeneration) return;
          inputMotionActive = false;
          if (!observerRefreshDeferred) return;
          observerRefreshDeferred = false;
          schedule();
        });
      }
    } catch {
      composerAnimation?.cancel();
      for (const animation of childAnimations) animation.cancel();
      composerAnimation = null;
      childAnimations = [];
      inputMotionActive = false;
    }
  }

  function applyInputVisual(inputVisual) {
    input.style.height = `${inputVisual.height}px`;
    input.dataset.overflow = inputVisual.overflow ? "true" : "false";
    // The text track is an invariant of the measured content, not a fraction of the outer
    // composer's animated height. This keeps the first line at one physical y coordinate while
    // the capsule grows and the toolbar moves into its second row.
    composer.style.setProperty("--input-text-height", `${inputVisual.height}px`);
    // WebView2 may scroll the old one-row textarea to reveal a newly wrapped caret before the
    // input event runs. Staging exposes the new row synchronously, so reset that transient scroll.
    if (!inputVisual.overflow) input.scrollTop = 0;
    composer.dataset.inputExpanded = inputVisual.expanded ? "true" : "false";
    composer.dataset.inputState = inputVisual.state;
  }

  function stageInputMotion({ beforeHeight, stagingHeight, inputVisual }) {
    const previousInputHeight = Number.parseFloat(input.style.height)
      || contract.controlPanel.inputBaseHeight;
    const rowGrowth = beforeHeight > contract.controlPanel.inputBaseHeight + 0.75;
    composer.style.height = `${stagingHeight}px`;
    applyInputVisual(inputVisual);
    composer.style.setProperty(
      "--input-toolbar-staging-shift",
      `${rowGrowth ? Math.min(0, previousInputHeight - inputVisual.height) : 0}px`,
    );
    composer.dataset.inputMotion = rowGrowth ? "row-growth" : "staging";
  }

  function stageImmediateExpansion() {
    if (disposed || composer.dataset.composing === "true") return;
    const reducedMotion = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches === true;
    if (reducedMotion || optimisticExpansion) return;
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
    const beforeHeight = Number(composer.offsetHeight) || captureVisualRects()?.composer.height;
    const stagingHeight = composerStagingHeight({
      beforeHeight,
      afterHeight: measuredControl.measurements.inputHeight,
      baseHeight: contract.controlPanel.inputBaseHeight,
      toolbarHeight: contract.controlPanel.inputToolbarHeight,
      expandedGap: contract.controlPanel.inputExpandedGap,
    });
    if (stagingHeight === null) return;

    const generation = ++motionGeneration;
    composerAnimation?.cancel();
    for (const animation of childAnimations) animation.cancel();
    composerAnimation = null;
    childAnimations = [];
    const previousVisual = Object.freeze({
      composerHeight: composer.style.height,
      inputHeight: input.style.height,
      overflow: input.dataset.overflow,
      expanded: composer.dataset.inputExpanded,
      state: composer.dataset.inputState,
    });
    stageInputMotion({ beforeHeight, stagingHeight, inputVisual: measuredControl.inputVisual });
    const stagingRects = captureVisualRects();
    const startAtUnixMs = scheduledMotionStart(now);
    const nativeReady = nativeMotionReady(() => {
      if (typeof startNativeExpansion !== "function") return false;
      return startNativeExpansion({
        targetHeight: measuredControl.measurements.inputHeight,
        stagingHeight,
        durationMs: COMPOSER_MOTION_DURATION_MS,
        startAtUnixMs,
      });
    });
    optimisticExpansion = Object.freeze({
      generation,
      inputHeight: measuredControl.measurements.inputHeight,
      state: measuredControl.inputVisual.state,
      previousVisual,
      nativeReady,
      startAtUnixMs,
    });
    nativeReady.then(() => {
      if (disposed || generation !== motionGeneration) return;
      animateCommittedLayout(stagingRects, {
        prepared: false,
        stagingHeight,
        targetHeight: measuredControl.measurements.inputHeight,
        startAtUnixMs,
      });
    });
  }

  function rollbackOptimisticExpansion(candidate) {
    if (!candidate || optimisticExpansion !== candidate) return;
    motionGeneration += 1;
    composerAnimation?.cancel();
    for (const animation of childAnimations) animation.cancel();
    composerAnimation = null;
    childAnimations = [];
    composer.style.height = candidate.previousVisual.composerHeight;
    input.style.height = candidate.previousVisual.inputHeight;
    if (candidate.previousVisual.overflow === undefined) delete input.dataset.overflow;
    else input.dataset.overflow = candidate.previousVisual.overflow;
    if (candidate.previousVisual.expanded === undefined) delete composer.dataset.inputExpanded;
    else composer.dataset.inputExpanded = candidate.previousVisual.expanded;
    if (candidate.previousVisual.state === undefined) delete composer.dataset.inputState;
    else composer.dataset.inputState = candidate.previousVisual.state;
    delete composer.dataset.inputMotion;
    optimisticExpansion = null;
    lastRequest = "";
  }

  async function refresh() {
    if (disposed) return Object.freeze({ applied: false, disposed: true });
    const pendingNativeExpansion = optimisticExpansion?.nativeReady;
    if (pendingNativeExpansion) await pendingNativeExpansion;
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
    const optimistic = optimisticExpansion
      && optimisticExpansion.inputHeight === measuredControl.measurements.inputHeight
      && optimisticExpansion.state === measuredControl.inputVisual.state
      ? optimisticExpansion
      : null;
    let transition;
    try {
      transition = layoutController.transition(PRODUCT_LAYOUT_STATE, "adaptive-control-surface", {
        adjustments,
        measurements: measured,
        commitVisual: (_layout, nativeResult) => {
          if (disposed) return;
          if (optimistic && optimisticExpansion === optimistic) {
            applyInputVisual(measuredControl.inputVisual);
            optimisticExpansion = null;
            optimistic.nativeReady.then(() => {
              if (disposed || optimistic.generation !== motionGeneration) return;
              composer.style.height = "";
            });
            return;
          }
          const generation = ++motionGeneration;
          const visualBefore = captureVisualRects();
          const previousComposerHeight = Number(composer.offsetHeight)
            || visualBefore?.composer.height
            || 0;
          const reducedMotion = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches === true;
          const stagingHeight = reducedMotion
            ? null
            : composerStagingHeight({
              beforeHeight: Number(composer.offsetHeight) || visualBefore?.composer.height,
              afterHeight: measuredControl.measurements.inputHeight,
              baseHeight: contract.controlPanel.inputBaseHeight,
              toolbarHeight: contract.controlPanel.inputToolbarHeight,
              expandedGap: contract.controlPanel.inputExpandedGap,
            });
          if (stagingHeight !== null) {
            stageInputMotion({
              beforeHeight: previousComposerHeight,
              stagingHeight,
              inputVisual: measuredControl.inputVisual,
            });
          } else if (previousComposerHeight > 0) {
            // apply_pet_layout commits the final CSS variables immediately after this callback.
            // Hold the accepted old frame until animateCommittedLayout installs its backwards-
            // filled animation, otherwise one unanimated final frame leaks between the two IPCs.
            composer.style.height = `${previousComposerHeight}px`;
          }
          const launch = (startAtUnixMs = null) => {
            if (disposed || generation !== motionGeneration) return;
            if (stagingHeight === null) {
              composer.style.height = "";
              applyInputVisual(measuredControl.inputVisual);
            }
            const motionBefore = stagingHeight === null ? visualBefore : captureVisualRects();
            animateCommittedLayout(motionBefore, {
              stagingHeight,
              startAtUnixMs,
            });
          };
          if (stagingHeight === null) delete composer.dataset.inputMotion;
          if (nativeResult?.inputTransitionPrepared === true
            && typeof startNativeTransition === "function") {
            const startAtUnixMs = scheduledMotionStart(now);
            nativeMotionReady(() => startNativeTransition(nativeResult.revision, startAtUnixMs))
              .then(() => launch(startAtUnixMs));
          } else {
            launch();
          }
        },
        visualPreview,
        deferNative,
        interactionTrace,
      });
    } catch (error) {
      rollbackOptimisticExpansion(optimistic);
      throw error;
    }
    try {
      const result = await transition;
      if (!result?.applied) rollbackOptimisticExpansion(optimistic);
      return result;
    } catch (error) {
      rollbackOptimisticExpansion(optimistic);
      throw error;
    }
  }

  function schedule() {
    if (disposed) return;
    if (inputMotionActive) {
      composerAnimation?.cancel();
      for (const animation of childAnimations) animation.cancel();
      composerAnimation = null;
      childAnimations = [];
      inputMotionActive = false;
      observerRefreshDeferred = false;
    }
    stageImmediateExpansion();
    if (pendingFrame !== null) return;
    pendingFrame = requestFrame(() => {
      pendingFrame = null;
      refreshPromise = refresh().catch(() => Object.freeze({ applied: false, failed: true }));
    });
  }

  function scheduleFromObservation() {
    if (disposed) return;
    if (inputMotionActive) {
      observerRefreshDeferred = true;
      return;
    }
    schedule();
  }

  const observer = typeof ResizeObserverClass === "function"
    ? new ResizeObserverClass(scheduleFromObservation)
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
      motionGeneration += 1;
      optimisticExpansion = null;
      inputMotionActive = false;
      observerRefreshDeferred = false;
      if (pendingFrame !== null) cancelFrame(pendingFrame);
      pendingFrame = null;
      observer?.disconnect();
      composerAnimation?.cancel();
      for (const animation of childAnimations) animation.cancel();
    },
  });
}
