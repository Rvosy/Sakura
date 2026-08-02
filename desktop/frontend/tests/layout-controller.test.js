import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { createLayoutController } from "../pet/layout-controller.js";

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

test("an in-flight native layout is followed only by the newest queued state", async () => {
  const pending = new Map();
  const committed = [];
  const controller = createLayoutController({
    computeLayout: (state) => ({ state, contractVersion: 1 }),
    applyNativeLayout: ({ state }) => {
      const task = deferred();
      pending.set(state, task);
      return task.promise;
    },
    commitLayout: (layout) => committed.push(layout.state),
  });

  const oldTransition = controller.transition("bubble");
  const supersededTransition = controller.transition("composer");
  const newTransition = controller.transition("expanded");
  pending.get("bubble").resolve({ applied: true, contractVersion: 1 });
  assert.equal((await oldTransition).applied, false);
  assert.equal((await supersededTransition).applied, false);
  await Promise.resolve();
  pending.get("expanded").resolve({ applied: true, contractVersion: 1 });
  assert.equal((await newTransition).applied, true);
  assert.deepEqual(committed, ["expanded"]);
});

test("rapid transitions commit only the last accepted revision", async () => {
  const committed = [];
  const nativeRevisions = [];
  const controller = createLayoutController({
    computeLayout: (state) => ({ state, contractVersion: 1 }),
    applyNativeLayout: async ({ revision }) => {
      nativeRevisions.push(revision);
      return { applied: revision === 4, contractVersion: 1 };
    },
    commitLayout: (layout) => committed.push(layout.state),
  });

  await Promise.all([
    controller.transition("idle"),
    controller.transition("bubble"),
    controller.transition("composer"),
    controller.transition("expanded"),
  ]);
  assert.deepEqual(committed, ["expanded"]);
  assert.deepEqual(nativeRevisions, [1, 4]);
});

test("a Rust/WebView contract mismatch is rejected", async () => {
  const controller = createLayoutController({
    computeLayout: (state) => ({ state, contractVersion: 1 }),
    applyNativeLayout: async () => ({ applied: true, contractVersion: 2 }),
    commitLayout: () => assert.fail("mismatched layout must not commit"),
  });
  await assert.rejects(controller.transition("idle"), /contracts do not match/);
});

test("native bounds are confirmed before the DOM state is committed", async () => {
  const order = [];
  const controller = createLayoutController({
    computeLayout: (state) => ({ state, contractVersion: 1 }),
    applyNativeLayout: async () => {
      order.push("native");
      return { applied: true, contractVersion: 1 };
    },
    commitLayout: () => order.push("commit"),
  });
  await controller.transition("composer");
  assert.deepEqual(order, ["native", "commit"]);
});

test("native-confirmed child and panel geometry commit in one synchronous stage", async () => {
  const order = [];
  const controller = createLayoutController({
    computeLayout: (state) => ({ state, contractVersion: 1 }),
    applyNativeLayout: async () => {
      order.push("native");
      return { applied: true, contractVersion: 1 };
    },
    commitLayout: () => order.push("panel"),
  });

  await controller.transition("composer", "", { commitVisual: () => order.push("child") });
  assert.deepEqual(order, ["native", "child", "panel"]);
});

test("native-confirmed intermediate frames keep a busy same-state slider moving", async () => {
  const pending = [];
  const committed = [];
  const controller = createLayoutController({
    computeLayout: (state, _placeholder, input) => ({ state, value: input.value, contractVersion: 1 }),
    applyNativeLayout: () => {
      const task = deferred();
      pending.push(task);
      return task.promise;
    },
    commitLayout: (layout) => committed.push(layout.value),
  });

  const first = controller.transition("product", "", { value: 10 });
  const skipped = controller.transition("product", "", { value: 20 });
  const latest = controller.transition("product", "", { value: 30 });
  pending[0].resolve({ applied: true, contractVersion: 1 });
  assert.equal((await first).applied, false);
  assert.equal((await skipped).applied, false);
  assert.deepEqual(committed, [10]);
  await Promise.resolve();
  pending[1].resolve({ applied: true, contractVersion: 1 });
  assert.equal((await latest).applied, true);
  assert.deepEqual(committed, [10, 30]);
});

test("an explicitly relaxed settings preview paints immediately without stale native overwrite", async () => {
  const pending = [];
  const previewed = [];
  const committed = [];
  const controller = createLayoutController({
    computeLayout: (state, _placeholder, input) => ({ state, value: input.value, contractVersion: 1 }),
    previewLayout: (layout) => previewed.push(layout.value),
    applyNativeLayout: () => {
      const task = deferred();
      pending.push(task);
      return task.promise;
    },
    commitLayout: (layout) => committed.push(layout.value),
  });

  const first = controller.transition("product", "", { value: 10, visualPreview: true });
  const latest = controller.transition("product", "", { value: 30, visualPreview: true });
  assert.deepEqual(previewed, [10, 30]);
  pending[0].resolve({ applied: true, contractVersion: 1 });
  assert.equal((await first).applied, false);
  assert.deepEqual(committed, []);
  await Promise.resolve();
  pending[1].resolve({ applied: true, contractVersion: 1 });
  assert.equal((await latest).applied, true);
  assert.deepEqual(committed, [30]);
});

test("settings layout bursts relax one native clip and restore it after the latest frame", () => {
  const app = readFileSync(new URL("../app.js", import.meta.url), "utf8");
  const styles = readFileSync(new URL("../styles.css", import.meta.url), "utf8");
  const native = readFileSync(new URL("../../src-tauri/src/main.rs", import.meta.url), "utf8");
  assert.match(app, /previewLayout\s*:/);
  assert.match(app, /await invoke\("begin_control_surface_preview", \{ revision \}\)[\s\S]*?invalidate\(\{ visualPreview: true \}\)/);
  assert.match(app, /await adaptiveSurface\.settle\(\)[\s\S]*?invoke\("end_control_surface_preview", \{ revision \}\)/);
  assert.match(native, /fn begin_control_surface_preview[\s\S]*?restore_full_hit_region/);
  assert.match(native, /fn end_control_surface_preview[\s\S]*?apply_precise_hit_regions/);
  assert.match(styles, /\[data-layout-preview="active"\][\s\S]*?transition:\s*none/);
});

test("a reloaded WebView continues after the native layout revision", async () => {
  const revisions = [];
  const controller = createLayoutController({
    initialRevision: 7,
    computeLayout: (state) => ({ state, contractVersion: 1 }),
    applyNativeLayout: async ({ revision }) => {
      revisions.push(revision);
      return { applied: revision > 7, contractVersion: 1 };
    },
    commitLayout() {},
  });

  assert.equal((await controller.transition("product")).applied, true);
  assert.deepEqual(revisions, [8]);
  assert.equal(controller.snapshot().requestedRevision, 8);
  assert.throws(() => createLayoutController({ initialRevision: -1 }), /initial layout revision/);
});
