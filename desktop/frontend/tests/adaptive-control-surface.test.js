import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  bubbleSurfaceHeight,
  createAdaptiveControlSurface,
  textareaMetrics,
} from "../pet/adaptive-control-surface.js";

const contract = JSON.parse(readFileSync(new URL("../pet/layout-contract.json", import.meta.url), "utf8"));

test("textarea height follows content through four rows and then scrolls", () => {
  const oneLine = textareaMetrics({ scrollHeight: 39, lineHeight: 22.5, paddingBlock: 16, maxRows: 4 });
  assert.deepEqual(oneLine, { height: 39, overflow: false });

  const fourLines = textareaMetrics({ scrollHeight: 106, lineHeight: 22.5, paddingBlock: 16, maxRows: 4 });
  assert.deepEqual(fourLines, { height: 106, overflow: false });

  const overflow = textareaMetrics({ scrollHeight: 180, lineHeight: 22.5, paddingBlock: 16, maxRows: 4 });
  assert.deepEqual(overflow, { height: 106, overflow: true });
});

test("bubble height grows from its compact floor to the configured ceiling", () => {
  const compact = bubbleSurfaceHeight({
    contentHeight: 24,
    headerHeight: 20,
    chromeHeight: 28,
    contentGap: 8,
    minimum: 88,
    maximum: 128,
  });
  const medium = bubbleSurfaceHeight({
    contentHeight: 60,
    headerHeight: 20,
    chromeHeight: 28,
    contentGap: 8,
    minimum: 88,
    maximum: 128,
  });
  const long = bubbleSurfaceHeight({
    contentHeight: 600,
    headerHeight: 20,
    chromeHeight: 28,
    contentGap: 8,
    minimum: 88,
    maximum: 128,
  });
  assert.equal(compact, 88);
  assert.equal(medium, 116);
  assert.equal(long, 128);
});

test("resize work is coalesced and reset restores the one-line composer", async () => {
  const frames = [];
  const transitions = [];
  const root = { style: { setProperty() {} } };
  const bubble = { style: {} };
  const bubbleHeader = { offsetHeight: 20 };
  const bubbleCopy = { scrollHeight: 24 };
  const composer = { style: {} };
  const input = { scrollHeight: 39, style: {}, dataset: {} };
  const styles = new Map([
    [bubble, { paddingTop: "12px", paddingBottom: "14px", borderTopWidth: "1px", borderBottomWidth: "1px" }],
    [bubbleCopy, { marginTop: "8px" }],
    [composer, { paddingTop: "5px", paddingBottom: "5px", borderTopWidth: "1px", borderBottomWidth: "1px" }],
    [input, { lineHeight: "22.5px", fontSize: "15px", paddingTop: "8px", paddingBottom: "8px" }],
  ]);
  class Observer {
    observe() {}
    disconnect() {}
  }
  const surface = createAdaptiveControlSurface({
    root,
    bubble,
    bubbleHeader,
    bubbleCopy,
    composer,
    input,
    contract,
    layoutController: {
      transition: async (...args) => {
        transitions.push(args);
        return { applied: true };
      },
    },
    readAdjustments: () => ({ controlPanelWidth: 640, bubbleMaxHeight: 128 }),
    getStyle: (element) => styles.get(element),
    requestFrame: (callback) => {
      frames.push(callback);
      return frames.length;
    },
    cancelFrame() {},
    ResizeObserverClass: Observer,
  });

  surface.schedule();
  surface.schedule();
  assert.equal(frames.length, 1);
  frames.shift()();
  await surface.settle();
  assert.equal(input.style.height, "39px");
  assert.equal(input.dataset.overflow, "false");
  assert.deepEqual(transitions.at(-1)[2].measurements, { bubbleHeight: 88, inputHeight: 52 });

  input.scrollHeight = 180;
  surface.schedule();
  frames.shift()();
  await surface.settle();
  assert.equal(input.style.height, "106px");
  assert.equal(input.dataset.overflow, "true");
  assert.equal(transitions.at(-1)[2].measurements.inputHeight, 118);

  surface.resetInput();
  assert.equal(input.style.height, "");
  assert.equal(input.dataset.overflow, "false");
  assert.equal(frames.length, 1);
  surface.dispose();
});
