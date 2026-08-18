import assert from "node:assert/strict";
import test from "node:test";

import { composerInputMetrics, createAdaptiveControlSurface } from "../pet/adaptive-control-surface.js";

function element(style = {}) {
  return { style: { height: "", setProperty() {}, ...style }, dataset: {} };
}

function contract() {
  return {
    controlPanel: {
      centerX: 450,
      inputExpandedMinRows: 2,
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

function fixture({ value = "", scrollHeight = 40 } = {}) {
  const root = element();
  const bubble = element();
  const bubbleHeader = { offsetHeight: 20 };
  const bubbleBody = element();
  const bubbleCopy = { scrollHeight: 56 };
  const composer = element();
  const input = element({ height: "40px" });
  input.dataset.overflow = "false";
  input.value = value;
  input.scrollHeight = scrollHeight;
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
    getStyle: (target) => styles.get(target) || {},
    requestFrame: () => 1,
    cancelFrame() {},
    ResizeObserverClass: null,
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
  assert.equal(request.measurements.inputHeight, 124);
  request.commitVisual();
  assert.equal(env.input.style.height, "64px");
  assert.equal(env.composer.dataset.inputState, "expanded-2");

  env.input.value = "  \n\t";
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

test("composer metrics use a two-row baseline and cap at three visible rows", () => {
  const base = {
    lineHeight: 24,
    paddingBlock: 16,
    frameHeight: 12,
    minExpandedRows: 2,
    maxRows: 3,
    toolbarHeight: 40,
    expandedGap: 8,
  };
  const collapsed = composerInputMetrics({ ...base, value: "第一行", scrollHeight: 40, expanded: false });
  assert.deepEqual(
    { state: collapsed.state, height: collapsed.height, textHeight: collapsed.textHeight },
    { state: "collapsed", height: 52, textHeight: 40 },
  );
  const expanded = composerInputMetrics({ ...base, value: "自动折成两行", scrollHeight: 64, expanded: false });
  assert.deepEqual(
    { state: expanded.state, height: expanded.height, textHeight: expanded.textHeight },
    { state: "expanded-2", height: 124, textHeight: 64 },
  );
  const latched = composerInputMetrics({ ...base, value: "删回一行", scrollHeight: 40, expanded: true });
  assert.equal(latched.state, "expanded-2");
  assert.equal(latched.height, 124);
  const overflow = composerInputMetrics({ ...base, value: "四行", scrollHeight: 112, expanded: true });
  assert.equal(overflow.state, "expanded-3");
  assert.equal(overflow.height, 148);
  assert.equal(overflow.overflow, true);
  const blank = composerInputMetrics({ ...base, value: " \n\t", scrollHeight: 64, expanded: true });
  assert.equal(blank.state, "collapsed");
  assert.equal(blank.height, 52);
});
