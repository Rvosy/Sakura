import assert from "node:assert/strict";
import test from "node:test";

import { createAdaptiveControlSurface } from "../pet/adaptive-control-surface.js";

function element(style = {}) {
  return { style: { height: "", setProperty() {}, ...style }, dataset: {} };
}

test("conversation bubble height stays settings-owned while textarea measurement remains adaptive", async () => {
  const root = element();
  const bubble = element();
  const bubbleHeader = { offsetHeight: 20 };
  const bubbleBody = element();
  const bubbleCopy = { scrollHeight: 56 };
  const composer = element();
  const input = element({ height: "40px" });
  input.dataset.overflow = "false";
  input.scrollHeight = 120;
  const styles = new Map([
    [bubble, { paddingTop: "13px", paddingBottom: "13px", borderTopWidth: "1px", borderBottomWidth: "1px" }],
    [bubbleBody, { marginTop: "8px" }],
    [bubbleCopy, { marginTop: "0px" }],
    [composer, { paddingTop: "5px", paddingBottom: "5px", borderTopWidth: "1px", borderBottomWidth: "1px" }],
    [input, { lineHeight: "24px", fontSize: "16px", paddingTop: "8px", paddingBottom: "8px" }],
  ]);
  const contract = {
    controlPanel: {
      centerX: 450,
      inputMaxRows: 4,
      inputBaseHeight: 52,
      inputMaxHeight: 152,
      bubbleMinHeight: 88,
      bubbleMaxHeight: { default: 128, minimum: 96, maximum: 260 },
      controlPanelWidth: { default: 640, minimum: 420, maximum: 760 },
      controlPanelVerticalOffset: { default: 0, minimum: -60, maximum: 160 },
      inputBarOffset: { default: 0, minimum: 0, maximum: 60 },
    },
  };
  let request = null;
  let transitionCount = 0;
  let expectedVisibleHeight = "40px";
  let expectedVisibleOverflow = "false";
  const surface = createAdaptiveControlSurface({
    root,
    bubble,
    bubbleHeader,
    bubbleBody,
    bubbleCopy,
    composer,
    input,
    contract,
    readAdjustments: () => ({}),
    getStyle: (target) => styles.get(target) || {},
    requestFrame: () => 1,
    cancelFrame() {},
    ResizeObserverClass: null,
    layoutController: {
      transition(_state, _reason, candidate) {
        transitionCount += 1;
        request = candidate;
        assert.equal(input.style.height, expectedVisibleHeight);
        assert.equal(input.dataset.overflow, expectedVisibleOverflow);
        return Promise.resolve({ applied: true });
      },
    },
  });

  await surface.refresh();
  assert.equal(request.measurements.bubbleHeight, 128);
  assert.equal(request.measurements.inputHeight, 124);
  assert.equal(typeof request.commitVisual, "function");
  request.commitVisual();
  assert.equal(input.style.height, "112px");
  assert.equal(input.dataset.overflow, "true");
  expectedVisibleHeight = "112px";
  expectedVisibleOverflow = "true";

  input.scrollHeight = 112;
  bubbleCopy.scrollHeight = 240;
  await surface.refresh();
  assert.equal(transitionCount, 2);
  assert.equal(request.measurements.bubbleHeight, 128);
  request.commitVisual();
  assert.equal(input.dataset.overflow, "false");
});
