import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import test from "node:test";
import { errorText } from "../core/error-display.js";

test("gesture end reports the original native preview failure and allows recovery", async () => {
  const source = await readFile(new URL("../app.js", import.meta.url), "utf8");
  const start = source.indexOf('await listenAppEvent("sakura://control-surface-gesture"');
  const end = source.indexOf('\nawait listenAppEvent("sakura://character-appearance-changed"', start);
  const logic = source.slice(
    source.indexOf("function endLayoutPreviewSession("),
    source.indexOf("\nasync function settleLayoutPreview("),
  );
  const failure = new Error("END_CONTROL_SURFACE_PREVIEW_FAILED: native surface unavailable");
  let listener;
  const errors = [];
  const context = vm.createContext({
    errorText,
    gestureEventPayload: (payload) => payload,
    listenAppEvent: async (_, callback) => { listener = callback; },
    interactionLatencyTrace: { mark() {}, atRevision: () => null, flush() {} },
    layoutGestureTrace: null,
    layoutPreviewRevision: 1,
    layoutGestureReady: Promise.resolve({ revision: 1 }),
    layoutPreviewSessionActive: true,
    layoutPreviewEnd: null,
    layoutPreviewEnding: false,
    layoutNativePrepared: true,
    layoutSurfaceReady: null,
    layoutGestureActive: true,
    disposed: false,
    stage: { dataset: { layoutPreview: "active" } },
    flushControlSurfaceGlassPreviews: async () => {},
    adaptiveSurface: { invalidate() {}, flush: async () => {} },
    runControlSurfaceTransaction: async (_, callback) => callback(),
    tracedInteractionInvoke: async () => { throw failure; },
    showRecoverableError: (error) => errors.push(error),
  });
  vm.runInContext(logic, context);
  await vm.runInContext(`(async () => { ${source.slice(start, end)} })()`, context);
  await listener({ payload: { active: false } });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(errors.length, 1);
  assert.match(errors[0], /native surface unavailable/);
  assert.equal(context.layoutPreviewEnding, false);

  context.tracedInteractionInvoke = async () => null;
  await listener({ payload: { active: false } });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(context.layoutPreviewSessionActive, false);
});
