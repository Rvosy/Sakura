import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

// Run the shipping callback, replacing only the native command and UI boundary.
const source = await readFile(new URL("../app.js", import.meta.url), "utf8");
const callback = source.slice(source.indexOf("function activatePortraitHitTest("),
  source.indexOf("\nasync function drainPortraitScaleHitFrames("));

function fixture() {
  const pending = [], commits = [], errors = [];
  const context = vm.createContext({
    disposed: false, portraitHitRevision: 0,
    activeAppearance: { portraitScalePercent: 100 },
    currentSurface: { width: 320, height: 480, assetId: "old-resource" },
    PORTRAIT_HIT_TEST_NOTICE: "hit-test unavailable",
    presentationError: { textContent: "", hidden: true },
    tracedInteractionInvoke: (command, args) => new Promise((resolve, reject) => {
      pending.push({ command, args, resolve, reject });
    }),
    commitSurfaceApplication: surface => commits.push(surface),
    showRecoverableError: message => {
      errors.push(message);
      context.presentationError.textContent = message;
      context.presentationError.hidden = false;
    },
    clearRecoverableError: () => {
      context.presentationError.textContent = "";
      context.presentationError.hidden = true;
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

test("a current failure remains visible until a successful hit-region update", async () => {
  const f = fixture();
  const failed = f.activate("current");
  const failure = new Error("native update failed");
  f.pending[0].reject(failure);
  await assert.rejects(failed, error => error === failure);
  assert.equal(f.context.presentationError.hidden, false);
  const ignored = f.activate("current");
  f.pending[1].resolve(null);
  await ignored;
  assert.equal(f.context.presentationError.hidden, false);
  const recovered = f.activate("current");
  f.pending[2].resolve({ revision: 3 });
  await recovered;
  assert.equal(f.context.presentationError.hidden, true);
});

test("hit-region recovery preserves an unrelated error", async () => {
  const f = fixture();
  f.context.presentationError.textContent = "microphone unavailable";
  f.context.presentationError.hidden = false;
  const current = f.activate("current");
  f.pending[0].resolve({ revision: 1 });
  await current;
  assert.equal(f.context.presentationError.textContent, "microphone unavailable");
  assert.equal(f.context.presentationError.hidden, false);
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
