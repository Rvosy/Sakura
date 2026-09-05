import assert from "node:assert/strict";
import test from "node:test";

import {
  BUBBLE_MOTION_DURATION_MS,
  COMPOSER_MOTION_DURATION_MS,
  bubbleMotionKeyframes,
  composerChildMotionKeyframes,
  composerInputMetrics,
  composerMotionDirection,
  composerStagingHeight,
  createAdaptiveControlSurface,
} from "../pet/adaptive-control-surface.js";

function element(style = {}) {
  return {
    style: {
      height: "",
      setProperty(name, value) { this[name] = value; },
      ...style,
    },
    dataset: {},
  };
}

function contract() {
  return {
    viewport: { windowSize: [900, 1774] },
    controlPanel: {
      centerX: 450,
      inputExpandedMinRows: 1,
      inputMaxRows: 3,
      inputToolbarHeight: 40,
      inputExpandedGap: 8,
      inputBaseHeight: 52,
      inputMaxHeight: 152,
      bubbleMinHeight: 88,
      bubbleMaxHeight: { default: 128, minimum: 96, maximum: 400 },
      controlPanelWidth: { default: 640, minimum: 420, maximum: 860 },
      controlPanelVerticalOffset: { default: 0, minimum: -400, maximum: 400 },
      inputBarOffset: { default: 0, minimum: 0, maximum: 400 },
    },
  };
}

function fixture({
  value = "",
  scrollHeight = 40,
  expandedScrollHeight = null,
  fontSize = null,
  requestFrame = () => 1,
  startNativeExpansion = null,
  startNativeTransition = null,
  startNativeBubbleTransition = null,
  bubbleAutoExpand = false,
  now = () => 1000,
  ResizeObserverClass = null,
} = {}) {
  const root = element();
  const bubble = element();
  bubble.offsetTop = 680;
  bubble.offsetHeight = 128;
  const bubbleHeader = { offsetHeight: 20 };
  const bubbleBody = element();
  const bubbleCopy = element();
  bubbleCopy.scrollHeight = 56;
  bubbleCopy.clientHeight = 56;
  bubbleCopy.offsetWidth = 560;
  const composer = element();
  composer.offsetHeight = 52;
  const input = element({ height: "40px" });
  input.dataset.overflow = "false";
  input.value = value;
  let currentScrollHeight = scrollHeight;
  Object.defineProperty(input, "scrollHeight", {
    configurable: true,
    get: () => composer.dataset.inputMeasure === "expanded" && expandedScrollHeight !== null
      ? expandedScrollHeight
      : currentScrollHeight,
    set: (value) => { currentScrollHeight = value; },
  });
  const styles = new Map([
    [bubble, { paddingTop: "13px", paddingBottom: "13px", borderTopWidth: "1px", borderBottomWidth: "1px" }],
    [bubbleBody, { marginTop: "8px" }],
    [composer, { paddingTop: "5px", paddingBottom: "5px", borderTopWidth: "1px", borderBottomWidth: "1px" }],
    [input, { lineHeight: "24px", fontSize: "16px", paddingTop: "8px", paddingBottom: "8px" }],
  ]);
  const requests = [];
  const surface = createAdaptiveControlSurface({
    root,
    bubble,
    bubbleHeader,
    bubbleBody,
    bubbleCopy,
    composer,
    input,
    contract: contract(),
    readAdjustments: () => ({ inputBarOffset: 0 }),
    readBubbleAutoExpand: () => bubbleAutoExpand,
    startNativeExpansion,
    startNativeTransition,
    startNativeBubbleTransition,
    now,
    getStyle: (target) => {
      if (target === input && fontSize !== null) {
        const expanded = (composer.dataset.inputMeasure ?? composer.dataset.inputExpanded) === "expanded"
          || (!composer.dataset.inputMeasure && composer.dataset.inputExpanded === "true");
        const padding = expanded ? 0 : (40 - fontSize * 1.5) / 2;
        return { lineHeight: `${fontSize * 1.5}px`, fontSize: `${fontSize}px`, paddingTop: `${padding}px`, paddingBottom: `${padding}px` };
      }
      return styles.get(target) || {};
    },
    requestFrame,
    cancelFrame() {},
    ResizeObserverClass,
    layoutController: {
      transition(_state, _reason, candidate) {
        requests.push(candidate);
        return Promise.resolve({ applied: true });
      },
    },
  });
  return { bubble, bubbleCopy, composer, input, requests, surface };
}


test("expanding large-font input measures the final text padding before committing its height", async () => {
  const env = fixture({ value: "first\nsecond\nthird", fontSize: 20, scrollHeight: 100, expandedScrollHeight: 90 });
  await env.surface.refresh();
  assert.equal(env.requests.at(-1).measurements.inputHeight, 150);
});

test("message-following mode measures natural copy height so a later short reply can contract", async () => {
  const env = fixture({ bubbleAutoExpand: true });
  let naturalHeight = 164;
  Object.defineProperty(env.bubbleCopy, "scrollHeight", {
    configurable: true,
    get: () => env.bubbleCopy.style.height === "0px" ? naturalHeight : 220,
  });

  await env.surface.refresh();
  assert.equal(env.requests.at(-1).measurements.bubbleHeight, 220);

  naturalHeight = 56;
  await env.surface.refresh();
  assert.equal(env.requests.at(-1).measurements.bubbleHeight, 128);
});

test("a final settings commit overrides a coalesced deferred preview of the same layout", async () => {
  const env = fixture();

  env.surface.invalidate({ visualPreview: true, deferNative: true });
  await env.surface.flush();
  assert.equal(env.requests.length, 1);
  assert.equal(env.requests[0].deferNative, true);

  env.surface.invalidate({ visualPreview: true, deferNative: true });
  env.surface.invalidate({ visualPreview: true, forceNative: true });
  await env.surface.flush();
  assert.equal(env.requests.length, 2);
  assert.equal(env.requests[1].deferNative, false);
});




test("composer expands to three text rows, stays latched, and collapses only when blank", async () => {
  const env = fixture({ value: "第一行\n第二行\n第三行\n第四行", scrollHeight: 120 });
  await env.surface.refresh();
  let request = env.requests.at(-1);
  assert.equal(request.measurements.bubbleHeight, 128);
  assert.equal(request.measurements.inputHeight, 148);
  request.commitVisual();
  assert.equal(env.input.style.height, "88px");
  assert.equal(env.input.dataset.overflow, "true");
  assert.equal(env.composer.dataset.inputState, "expanded-3");

  env.input.value = "仍有文字";
  env.input.scrollHeight = 40;
  env.bubbleCopy.scrollHeight = 240;
  await env.surface.refresh();
  request = env.requests.at(-1);
  assert.equal(request.measurements.bubbleHeight, 128);
  assert.equal(request.measurements.inputHeight, 100);
  request.commitVisual();
  assert.equal(env.input.style.height, "40px");
  assert.equal(env.composer.dataset.inputState, "expanded-1");

  env.input.value = "  \t";
  env.input.scrollHeight = 40;
  await env.surface.refresh();
  request = env.requests.at(-1);
  assert.equal(request.measurements.inputHeight, 100);
  request.commitVisual();
  assert.equal(env.composer.dataset.inputState, "expanded-1");

  env.input.value = "";
  await env.surface.refresh();
  request = env.requests.at(-1);
  assert.equal(request.measurements.inputHeight, 52);
  assert.equal(request.measurements.bubbleHeight, 128);
  request.commitVisual();
  assert.equal(env.input.style.height, "40px");
  assert.equal(env.composer.dataset.inputState, "collapsed");
});

test("IME defers expansion until composition ends", async () => {
  const env = fixture({ value: "输入中的长文本", scrollHeight: 64 });
  env.surface.setComposing(true);
  await env.surface.refresh();
  let request = env.requests.at(-1);
  assert.equal(request.measurements.inputHeight, 52);
  request.commitVisual();

  env.surface.setComposing(false);
  await env.surface.refresh();
  request = env.requests.at(-1);
  assert.equal(request.measurements.inputHeight, 124);
  assert.equal(request.adjustments.inputBarOffset, 0);
  request.commitVisual();
  assert.equal(env.input.style.height, "64px");
});







test("input events hold staging until native glass confirms its animation has started", async () => {
  const frames = [];
  const animations = [];
  const nativeExpansions = [];
  let confirmNativeStart;
  const nativeStarted = new Promise((resolve) => { confirmNativeStart = resolve; });
  const env = fixture({
    value: "第一行\n第二行",
    scrollHeight: 64,
    requestFrame: (callback) => {
      frames.push(callback);
      return frames.length;
    },
    startNativeExpansion: (transition) => {
      nativeExpansions.push(transition);
      return nativeStarted;
    },
  });
  env.composer.offsetWidth = 640;
  env.composer.querySelectorAll = () => [];
  env.composer.getBoundingClientRect = () => ({
    left: 0,
    top: 0,
    width: 640,
    height: Number.parseFloat(env.composer.style.height) || 52,
  });
  env.input.getBoundingClientRect = () => ({
    left: env.composer.dataset.inputMotion === "staging" ? 50 : 12,
    top: 6,
    width: 540,
    height: Number.parseFloat(env.input.style.height) || 40,
  });
  env.composer.animate = (keyframes, options) => {
    animations.push({ keyframes, options });
    return { cancel() {} };
  };
  env.input.animate = () => ({ cancel() {} });

  env.surface.schedule();

  assert.equal(env.composer.style.height, "76px");
  assert.equal(env.composer.dataset.inputMotion, "staging");
  assert.equal(env.input.style.height, "64px");
  assert.deepEqual(nativeExpansions, [{
    targetHeight: 124,
    stagingHeight: 76,
    durationMs: COMPOSER_MOTION_DURATION_MS,
    startAtUnixMs: 1040,
  }]);
  assert.equal(frames.length, 1, "only the full native refresh is queued while glass startup is pending");
  assert.equal(animations.length, 0);

  confirmNativeStart(true);
  await nativeStarted;
  await Promise.resolve();
  assert.equal(env.composer.style.height, "124px");
  assert.equal(env.composer.dataset.inputMotion, undefined);
  assert.deepEqual(animations[0].keyframes, [{ height: "76px" }, { height: "124px" }]);
  assert.equal(animations[0].options.duration, COMPOSER_MOTION_DURATION_MS);
  assert.equal(animations[0].options.delay, 40);
  assert.equal(animations[0].options.fill, "backwards");

  frames.shift()();
  await env.surface.settle();
  assert.equal(env.requests.at(-1).measurements.inputHeight, 124);
});



test("narrow wrapping measures one final expanded target before either animation starts", () => {
  const nativeExpansions = [];
  const env = fixture({
    value: "临界宽度文字",
    scrollHeight: 64,
    expandedScrollHeight: 40,
    startNativeExpansion: (transition) => nativeExpansions.push(transition),
  });

  env.surface.schedule();

  assert.equal(env.composer.style.height, "52px");
  assert.equal(env.composer.dataset.inputMotion, "staging");
  assert.equal(env.composer.dataset.inputState, "expanded-1");
  assert.deepEqual(nativeExpansions, [{
    targetHeight: 100,
    stagingHeight: 52,
    durationMs: COMPOSER_MOTION_DURATION_MS,
    startAtUnixMs: 1040,
  }]);
});

test("ResizeObserver cannot feed animated textarea frames back into layout", async () => {
  const frames = [];
  let observeLayout;
  let finishMotion;
  const finished = new Promise((resolve) => { finishMotion = resolve; });
  class TestResizeObserver {
    constructor(callback) { observeLayout = callback; }
    observe() {}
    disconnect() {}
  }
  const env = fixture({
    value: "第一行\n第二行",
    scrollHeight: 64,
    startNativeExpansion: () => true,
    requestFrame: (callback) => {
      frames.push(callback);
      return frames.length;
    },
    ResizeObserverClass: TestResizeObserver,
  });
  env.composer.offsetHeight = 52;
  env.composer.offsetWidth = 640;
  env.composer.querySelectorAll = () => [];
  env.composer.getBoundingClientRect = () => ({
    left: 0,
    top: 0,
    width: 640,
    height: Number.parseFloat(env.composer.style.height) || 52,
  });
  env.input.getBoundingClientRect = () => ({
    left: env.composer.dataset.inputMotion === "staging" ? 50 : 12,
    top: 6,
    width: env.composer.dataset.inputMotion === "staging" ? 540 : 616,
    height: Number.parseFloat(env.input.style.height) || 40,
  });
  const animation = () => ({ cancel() {}, finished });
  env.composer.animate = animation;
  env.input.animate = animation;

  env.surface.schedule();
  await Promise.resolve();
  await Promise.resolve();
  frames.shift()();
  await env.surface.settle();
  assert.equal(frames.length, 0);

  observeLayout();
  assert.equal(frames.length, 0, "animated width/height observations stay inside the transaction");

  finishMotion();
  await finished;
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(frames.length, 1, "the final settled geometry is checked exactly once");
});
