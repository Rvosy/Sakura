import assert from "node:assert/strict";
import test from "node:test";
import { createControlSurfaceTransactions } from "../pet/control-surface-transactions.js";

function deferred() {
  let resolve;
  const promise = new Promise((accept) => { resolve = accept; });
  return { promise, resolve };
}

test("a new gesture starts only after the previous native frame reaches the DOM", async () => {
  let revision = 1;
  const resized = deferred();
  const calls = [];
  const run = createControlSurfaceTransactions({
    isCurrent: (candidate) => candidate === revision,
    isDisposed: () => false,
    commit: (surface) => calls.push(`dom:${surface}`),
  });
  const ending = run(1, () => {
    calls.push("resize:1");
    return resized.promise;
  });
  await Promise.resolve();
  revision = 2;
  const beginning = run(2, async () => {
    calls.push("resize:2");
    return "expanded";
  });
  resized.resolve("settled");
  await Promise.all([ending, beginning]);
  assert.deepEqual(calls, [
    "resize:1", "dom:settled", "resize:2", "dom:expanded",
  ]);
});

test("superseded queued gestures never resize the window", async () => {
  const calls = [];
  const run = createControlSurfaceTransactions({
    isCurrent: (revision) => revision === 3,
    isDisposed: () => false,
    commit: () => calls.push("commit"),
  });
  assert.equal(await run(2, () => calls.push("resize")), null);
  assert.deepEqual(calls, []);
});

test("a failed resize does not block the next gesture", async () => {
  const calls = [];
  const run = createControlSurfaceTransactions({
    isCurrent: () => true,
    isDisposed: () => false,
    commit: (surface) => calls.push(surface),
  });
  await assert.rejects(run(1, async () => { throw new Error("resize failed"); }), /resize failed/);
  await run(2, async () => "recovered");
  assert.deepEqual(calls, ["recovered"]);
});

test("disposing during a native resize prevents a DOM commit", async () => {
  const calls = [];
  let disposed = false;
  const resized = deferred();
  const run = createControlSurfaceTransactions({
    isCurrent: () => true,
    isDisposed: () => disposed,
    commit: () => calls.push("commit"),
  });
  const pending = run(1, () => resized.promise);
  await Promise.resolve();
  disposed = true;
  resized.resolve("settled");
  await pending;
  assert.deepEqual(calls, []);
});
