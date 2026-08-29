import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  COMPOSER_MOTION_DURATION_MS,
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
    controlPanel: {
      centerX: 450,
      inputExpandedMinRows: 1,
      inputMaxRows: 3,
      inputToolbarHeight: 40,
      inputExpandedGap: 8,
      inputBaseHeight: 52,
      inputMaxHeight: 152,
      bubbleMinHeight: 88,
      bubbleMaxHeight: { default: 128, minimum: 96, maximum: 260 },
      controlPanelWidth: { default: 640, minimum: 420, maximum: 760 },
      controlPanelVerticalOffset: { default: 0, minimum: -60, maximum: 160 },
      inputBarOffset: { default: 0, minimum: 0, maximum: 60 },
    },
  };
}

function fixture({
  value = "",
  scrollHeight = 40,
  expandedScrollHeight = null,
  requestFrame = () => 1,
  startNativeExpansion = null,
  startNativeTransition = null,
  now = () => 1000,
  ResizeObserverClass = null,
} = {}) {
  const root = element();
  const bubble = element();
  const bubbleHeader = { offsetHeight: 20 };
  const bubbleBody = element();
  const bubbleCopy = { scrollHeight: 56 };
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
    startNativeExpansion,
    startNativeTransition,
    now,
    getStyle: (target) => styles.get(target) || {},
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
  return { bubbleCopy, composer, input, requests, surface };
}

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

test("IME also defers collapse of a latched composer", async () => {
  const env = fixture({ value: "第一行\n第二行", scrollHeight: 64 });
  await env.surface.refresh();
  env.requests.at(-1).commitVisual();
  assert.equal(env.composer.dataset.inputState, "expanded-2");

  env.input.value = "";
  env.input.scrollHeight = 40;
  env.surface.setComposing(true);
  await env.surface.refresh();
  let request = env.requests.at(-1);
  assert.equal(request.measurements.inputHeight, 124);
  request.commitVisual();

  env.surface.setComposing(false);
  await env.surface.refresh();
  request = env.requests.at(-1);
  assert.equal(request.measurements.inputHeight, 52);
});

test("composer metrics keep a one-row toolbar baseline and cap at three visible rows", () => {
  const base = {
    lineHeight: 24,
    paddingBlock: 16,
    frameHeight: 12,
    minExpandedRows: 1,
    maxRows: 3,
    toolbarHeight: 40,
    expandedGap: 8,
  };
  const collapsed = composerInputMetrics({ ...base, value: "第一行", scrollHeight: 40, expanded: false });
  assert.deepEqual(
    { state: collapsed.state, height: collapsed.height, textHeight: collapsed.textHeight },
    { state: "collapsed", height: 52, textHeight: 40 },
  );
  const roundedSingleLine = composerInputMetrics({
    ...base,
    value: "123",
    scrollHeight: 42,
    expanded: false,
  });
  assert.equal(roundedSingleLine.state, "collapsed");
  assert.equal(roundedSingleLine.height, 52);
  const expanded = composerInputMetrics({ ...base, value: "自动折成两行", scrollHeight: 64, expanded: false });
  assert.deepEqual(
    { state: expanded.state, height: expanded.height, textHeight: expanded.textHeight },
    { state: "expanded-2", height: 124, textHeight: 64 },
  );
  const latched = composerInputMetrics({ ...base, value: "删回一行", scrollHeight: 40, expanded: true });
  assert.equal(latched.state, "expanded-1");
  assert.equal(latched.height, 100);
  const overflow = composerInputMetrics({ ...base, value: "四行", scrollHeight: 112, expanded: true });
  assert.equal(overflow.state, "expanded-3");
  assert.equal(overflow.height, 148);
  assert.equal(overflow.overflow, true);
  const manualBreak = composerInputMetrics({ ...base, value: "\n", scrollHeight: 40, expanded: false });
  assert.equal(manualBreak.state, "expanded-2");
  assert.equal(manualBreak.height, 124);
  const whitespace = composerInputMetrics({ ...base, value: " \t", scrollHeight: 40, expanded: true });
  assert.equal(whitespace.state, "expanded-1");
  const blank = composerInputMetrics({ ...base, value: "", scrollHeight: 40, expanded: true });
  assert.equal(blank.state, "collapsed");
  assert.equal(blank.height, 52);
  const attachmentOnly = composerInputMetrics({
    ...base, value: "", scrollHeight: 40, expanded: false, attachmentCount: 1,
  });
  assert.equal(attachmentOnly.state, "expanded-1");
  assert.equal(attachmentOnly.height, 100);
  const singleLineWithAttachments = composerInputMetrics({
    ...base, value: "123", scrollHeight: 40, expanded: false, attachmentCount: 6,
  });
  assert.equal(singleLineWithAttachments.state, "expanded-1");
  assert.equal(singleLineWithAttachments.height, 100);
  const attachmentDuringComposition = composerInputMetrics({
    ...base,
    value: "拼",
    scrollHeight: 40,
    expanded: false,
    composing: true,
    attachmentCount: 1,
  });
  assert.equal(attachmentDuringComposition.state, "expanded-1");
});

test("composer motion classifies both smooth expansion and contraction", () => {
  assert.equal(composerMotionDirection(52, 124), "expand");
  assert.equal(composerMotionDirection(148, 124), "contract");
  assert.equal(composerMotionDirection(124, 52), "contract");
  assert.equal(composerMotionDirection(52, 52), "stable");
});

test("two-line entry stages above the capsule before the smooth toolbar motion", () => {
  const metrics = {
    baseHeight: 52,
    toolbarHeight: 40,
    expandedGap: 8,
  };
  assert.equal(composerStagingHeight({ ...metrics, beforeHeight: 52, afterHeight: 124 }), 76);
  assert.equal(composerStagingHeight({ ...metrics, beforeHeight: 100, afterHeight: 124 }), 100);
  assert.equal(composerStagingHeight({ ...metrics, beforeHeight: 124, afterHeight: 148 }), 124);
  assert.equal(composerStagingHeight({ ...metrics, beforeHeight: 124, afterHeight: 52 }), null);
  assert.equal(composerStagingHeight({ ...metrics, beforeHeight: 52, afterHeight: 100 }), 52);
  assert.equal(composerStagingHeight({ ...metrics, beforeHeight: 124, afterHeight: 100 }), null);
  assert.equal(COMPOSER_MOTION_DURATION_MS, 260);

  const text = composerChildMotionKeyframes({ dx: 44, dy: 0 });
  const toolbar = composerChildMotionKeyframes({ dx: 0, dy: -72 });
  assert.deepEqual(text.map(({ offset }) => offset), [0, 1]);
  assert.deepEqual(toolbar.map(({ offset }) => offset), [0, 1]);
  assert.deepEqual(composerChildMotionKeyframes({
    dx: -38,
    dy: 0,
    beforeWidth: 616,
    afterWidth: 540,
    beforePaddingLeft: 12,
    afterPaddingLeft: 0,
    beforePaddingRight: 12,
    afterPaddingRight: 0,
  }), [
    {
      transform: "translate(-38px, 0px)",
      offset: 0,
      width: "616px",
      paddingLeft: "12px",
      paddingRight: "12px",
    },
    {
      transform: "translate(0, 0)",
      offset: 1,
      width: "540px",
      paddingLeft: "0px",
      paddingRight: "0px",
    },
  ]);
});

test("textarea stays top-anchored while width and padding animate", () => {
  const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8");
  const textareaRule = css.match(/\.composer textarea \{(?<body>[\s\S]*?)\n\}/)?.groups?.body || "";
  const stagingRule = css.match(
    /\.composer\[data-input-motion="staging"\] textarea \{(?<body>[\s\S]*?)\n\}/,
  )?.groups?.body || "";
  assert.match(textareaRule, /align-self:\s*start/);
  assert.match(stagingRule, /align-self:\s*start/);
});

test("expanded text track stays fixed while the outer composer height animates", () => {
  const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8");
  const expandedRule = css.match(
    /\.composer\[data-input-expanded="true"\] \{(?<body>[\s\S]*?)\n\}/,
  )?.groups?.body || "";
  const stagingRule = css.match(
    /\.composer\[data-input-motion="staging"\] \{(?<body>[\s\S]*?)\n\}/,
  )?.groups?.body || "";
  const env = fixture({
    value: "first line\nsecond line",
    scrollHeight: 64,
    startNativeExpansion: () => true,
  });

  env.surface.schedule();

  assert.match(expandedRule, /grid-template-rows:\s*var\(--input-text-height\) 40px/);
  assert.match(stagingRule, /grid-template-rows:\s*var\(--input-text-height\)/);
  assert.equal(env.input.style.height, "64px");
  assert.equal(env.composer.style["--input-text-height"], "64px");
});

test("an already-expanded composer stages a new row before the next paint", () => {
  const frames = [];
  const nativeExpansions = [];
  let confirmNativeStart;
  const nativeStarted = new Promise((resolve) => { confirmNativeStart = resolve; });
  const env = fixture({
    value: "first line\nsecond line",
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
  env.composer.offsetHeight = 100;
  env.composer.dataset.inputExpanded = "true";
  env.composer.dataset.inputState = "expanded-1";
  env.input.scrollTop = 24;

  env.surface.schedule();

  assert.equal(env.composer.style.height, "100px");
  assert.equal(env.input.style.height, "64px");
  assert.equal(env.input.scrollTop, 0);
  assert.equal(env.composer.dataset.inputMotion, "row-growth");
  assert.equal(env.composer.style["--input-toolbar-staging-shift"], "-24px");
  assert.deepEqual(nativeExpansions, [{
    targetHeight: 124,
    stagingHeight: 100,
    durationMs: COMPOSER_MOTION_DURATION_MS,
    startAtUnixMs: 1040,
  }]);
  assert.equal(frames.length, 1, "only the native refresh is queued after synchronous staging");

  confirmNativeStart(true);
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

test("prepared native glass transition acknowledgement gates the WebView motion", async () => {
  const frames = [];
  const nativeStarts = [];
  let confirmNativeStart;
  const nativeStarted = new Promise((resolve) => { confirmNativeStart = resolve; });
  const env = fixture({
    value: "第一行\n第二行",
    scrollHeight: 64,
    requestFrame: (callback) => {
      frames.push(callback);
      return frames.length;
    },
    startNativeTransition: (revision, startAtUnixMs) => {
      nativeStarts.push({ revision, startAtUnixMs });
      return nativeStarted;
    },
  });
  await env.surface.refresh();
  env.requests.at(-1).commitVisual(null, {
    revision: 17,
    inputTransitionPrepared: true,
  });
  assert.equal(env.composer.style.height, "76px");
  assert.equal(env.composer.dataset.inputMotion, "staging");
  assert.deepEqual(nativeStarts, [{ revision: 17, startAtUnixMs: 1040 }]);
  assert.equal(frames.length, 0);
  confirmNativeStart(true);
  await nativeStarted;
  await Promise.resolve();
  assert.equal(env.composer.style.height, "");
  assert.equal(env.composer.dataset.inputMotion, undefined);
});

test("collapse holds the accepted placeholder frame until the shared animation is installed", async () => {
  let confirmNativeStart;
  const nativeStarted = new Promise((resolve) => { confirmNativeStart = resolve; });
  const nativeStarts = [];
  const composerAnimations = [];
  const env = fixture({
    value: "",
    scrollHeight: 40,
    startNativeTransition: (revision, startAtUnixMs) => {
      nativeStarts.push({ revision, startAtUnixMs });
      return nativeStarted;
    },
  });
  env.composer.dataset.inputExpanded = "true";
  env.composer.dataset.inputState = "expanded-2";
  env.composer.offsetHeight = 124;
  env.composer.offsetWidth = 640;
  env.composer.querySelectorAll = () => [];
  env.composer.getBoundingClientRect = () => ({
    left: 0,
    top: 0,
    width: 640,
    height: Number.parseFloat(env.composer.style.height)
      || (env.composer.dataset.inputExpanded === "true" ? 124 : 52),
  });
  env.input.getBoundingClientRect = () => ({
    left: env.composer.dataset.inputExpanded === "true" ? 12 : 50,
    top: 6,
    width: 540,
    height: 40,
  });
  env.composer.animate = (keyframes, options) => {
    composerAnimations.push({ keyframes, options });
    return { cancel() {} };
  };
  env.input.animate = () => ({ cancel() {} });

  await env.surface.refresh();
  env.requests.at(-1).commitVisual(null, {
    revision: 23,
    inputTransitionPrepared: true,
  });

  assert.equal(env.composer.style.height, "124px");
  assert.equal(env.composer.dataset.inputExpanded, "true");
  assert.deepEqual(nativeStarts, [{ revision: 23, startAtUnixMs: 1040 }]);
  assert.equal(composerAnimations.length, 0, "no final frame leaks before native acknowledgement");

  confirmNativeStart(true);
  await nativeStarted;
  await Promise.resolve();

  assert.equal(env.composer.dataset.inputExpanded, "false");
  assert.deepEqual(composerAnimations[0].keyframes, [{ height: "124px" }, { height: "52px" }]);
  assert.equal(composerAnimations[0].options.delay, 40);
  assert.equal(composerAnimations[0].options.fill, "backwards");
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
