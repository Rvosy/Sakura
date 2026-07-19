import assert from "node:assert/strict";
import test from "node:test";

import { createLayoutController } from "../pet/layout-controller.js";

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

test("late native layout results cannot overwrite the newest state", async () => {
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
  const newTransition = controller.transition("expanded");
  pending.get("expanded").resolve({ applied: true, contractVersion: 1 });
  assert.equal((await newTransition).applied, true);
  pending.get("bubble").resolve({ applied: true, contractVersion: 1 });
  assert.equal((await oldTransition).applied, false);
  assert.deepEqual(committed, ["expanded"]);
});

test("rapid transitions commit only the last accepted revision", async () => {
  const committed = [];
  const controller = createLayoutController({
    computeLayout: (state) => ({ state, contractVersion: 1 }),
    applyNativeLayout: async ({ revision }) => ({
      applied: revision === 4,
      contractVersion: 1,
    }),
    commitLayout: (layout) => committed.push(layout.state),
  });

  await Promise.all([
    controller.transition("idle"),
    controller.transition("bubble"),
    controller.transition("composer"),
    controller.transition("expanded"),
  ]);
  assert.deepEqual(committed, ["expanded"]);
});

test("a Rust/WebView contract mismatch is rejected", async () => {
  const controller = createLayoutController({
    computeLayout: (state) => ({ state, contractVersion: 1 }),
    applyNativeLayout: async () => ({ applied: true, contractVersion: 2 }),
    commitLayout: () => assert.fail("mismatched layout must not commit"),
  });
  await assert.rejects(controller.transition("idle"), /contracts do not match/);
});

test("native bounds are applied before the DOM layout is committed", async () => {
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
