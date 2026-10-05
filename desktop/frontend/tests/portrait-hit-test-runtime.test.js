import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

// Run the shipping callback, replacing only the native command and UI boundary.
const source = await readFile(new URL("../app.js", import.meta.url), "utf8");
const callback = source.slice(source.indexOf("function activatePortraitHitTest("),
  source.indexOf("\nasync function drainPortraitScaleHitFrames("));

for (const superseded of [false, true]) {
  test(`form surface holds the native envelope until paint, superseded: ${superseded}`, async () => {
    const calls = [];
    let painted;
    const paint = new Promise(resolve => { painted = resolve; });
    const context = vm.createContext({
      portraitHitRevision: 0, visualScalePercent: 100, currentSurface: {}, renderedPortrait: "",
      visualContainer: {}, characterPresentation: {}, portraitFallback: { hidden: false },
      createRendererHost: options => options, visualUnavailable() {}, reportVisualError() {},
      runPortraitSurfaceMutation: action => action(),
      waitForPortraitPaint: () => paint,
      commitSurfaceApplication: surface => calls.push(["layout", surface]),
      syncPortraitAppearance() {},
      invoke: async (name, args) => { calls.push([name, args]); return { revision: args.revision }; },
      activatePortraitHitTest: async (key, revision, trace, options) => {
        calls.push(["activate", options]); return { revision };
      },
    });
    const finish = source.slice(source.indexOf("async function finishPortraitSurfaceTransition("),
      source.indexOf("\nfunction activatePortraitHitTest("));
    const renderer = source.slice(source.indexOf("const rendererHost = createRendererHost({"),
      source.indexOf("\nlet presentation = createChatPresentationReducer("));
    const services = vm.runInContext(`${finish}\n${renderer}\nrendererHost.services`, context);
    const signal = new AbortController().signal;
    assert.equal(await services.setSurface({ width: 320, height: 420 },
      { signal, visual: { assets: {} }, replacing: true }), true);
    assert.equal(calls[0][0], "prepare_portrait_transition");
    assert.equal(calls[0][1].deferNativeCommit, true);
    assert.deepEqual(Array.from(calls[0][1].surfaceSize), [320, 420]);
    assert.equal(calls[2][0], "activate");
    assert.equal(calls[2][1].retainTransitionLayout, true);
    const finishing = services.finishSurface();
    assert.equal(calls.some(([name]) => name === "commit_portrait_transition"), false);
    if (superseded) context.portraitHitRevision++;
    painted();
    assert.equal(await finishing, !superseded);
    assert.equal(calls.some(([name]) => name === "commit_portrait_transition"), !superseded);
  });
}

function fixture() {
  const pending = [], commits = [], errors = [];
  const context = vm.createContext({
    disposed: false, portraitHitRevision: 0,
    activeAppearance: { portraitScalePercent: 100 },
    currentSurface: { width: 320, height: 480, assetId: "old-resource" },
    PORTRAIT_HIT_TEST_NOTICE: "hit-test unavailable",
    errorDialog: { details: "", open: false },
    tracedInteractionInvoke: (command, args) => new Promise((resolve, reject) => {
      pending.push({ command, args, resolve, reject });
    }),
    commitSurfaceApplication: surface => commits.push(surface),
    showRecoverableError: message => {
      errors.push(message);
      context.errorDialog.details = message;
      context.errorDialog.open = true;
    },
    clearRecoverableError: () => {
      assert.fail("background recovery must not dismiss a dialog being read");
    },
  });
  const activate = vm.runInContext(`(${callback})`, context);
  return { context, pending, commits, errors, activate };
}

test("a late failure from the previous form cannot warn after the replacement succeeds", async () => {
  const f = fixture();
  const old = f.activate("old");
  const current = f.activate("current");
  const surface = { revision: 2 };
  f.pending[1].resolve(surface);
  assert.equal(await current, surface);
  f.pending[0].reject(new Error("VISUAL_ASSET_UNKNOWN"));
  assert.equal(await old, null);
  assert.deepEqual(f.errors, []);
  assert.deepEqual(f.commits, [surface]);
});

test("a successful hit-region update preserves the error dialog until the user closes it", async () => {
  const f = fixture();
  const failed = f.activate("current");
  const failure = new Error("native update failed");
  f.pending[0].reject(failure);
  await assert.rejects(failed, error => error === failure);
  assert.equal(f.context.errorDialog.open, true);
  const ignored = f.activate("current");
  f.pending[1].resolve(null);
  await ignored;
  assert.equal(f.context.errorDialog.open, true);
  const recovered = f.activate("current");
  f.pending[2].resolve({ revision: 3 });
  await recovered;
  assert.equal(f.context.errorDialog.open, true);
});

test("hit-region recovery preserves an unrelated error", async () => {
  const f = fixture();
  f.context.errorDialog.details = "microphone unavailable";
  f.context.errorDialog.open = true;
  const current = f.activate("current");
  f.pending[0].resolve({ revision: 1 });
  await current;
  assert.equal(f.context.errorDialog.details, "microphone unavailable");
  assert.equal(f.context.errorDialog.open, true);
});

for (const obsolete of ["revision", "signal", "operationSignal", "disposed"]) {
  for (const outcome of ["success", "failure"]) {
    test(`${obsolete} invalidation ignores late ${outcome}`, async () => {
      const f = fixture();
      const controller = new AbortController();
      const current = f.activate("current", undefined, null, { [obsolete]: controller.signal });
      if (obsolete === "revision") f.context.portraitHitRevision++;
      else if (obsolete === "disposed") f.context.disposed = true;
      else controller.abort();
      if (outcome === "success") f.pending[0].resolve({ revision: 1 });
      else f.pending[0].reject(new Error("resource revoked"));
      assert.equal(await current, null);
      assert.deepEqual(f.errors, []);
      assert.deepEqual(f.commits, []);
    });
  }
}
