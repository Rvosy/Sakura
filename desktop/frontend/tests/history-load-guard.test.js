import assert from "node:assert/strict";
import test from "node:test";

import {
  createHistoryLoadGuard,
  historyRefreshAction,
  subscribeHistoryRefresh,
} from "../history/history-load-guard.js";

test("character reset invalidates old history pages and cursors", () => {
  const guard = createHistoryLoadGuard();
  const oldInitial = guard.begin();
  const oldPagination = guard.begin();
  assert.equal(guard.isCurrent(oldInitial), true);

  guard.invalidate();

  assert.equal(guard.isCurrent(oldInitial), false);
  assert.equal(guard.isCurrent(oldPagination), false);
  assert.equal(guard.isCurrent(guard.begin()), true);
});

test("history stays blank while character restart is pending and reloads only when ready", () => {
  assert.deepEqual(historyRefreshAction({ reset: true, ready: false }), {
    reset: true,
    reload: false,
  });
  assert.deepEqual(historyRefreshAction({ reset: true, ready: true }), {
    reset: true,
    reload: true,
  });
  assert.deepEqual(historyRefreshAction({}), { reset: false, reload: true });
});

test("history refresh subscription is fully installed before bootstrap continues", async () => {
  let release;
  let callback = null;
  const installed = subscribeHistoryRefresh(async (name, listener) => {
    assert.equal(name, "sakura://history-refresh-requested");
    callback = listener;
    await new Promise((resolve) => { release = resolve; });
  }, () => {});

  let settled = false;
  void installed.then(() => { settled = true; });
  await Promise.resolve();
  assert.equal(settled, false);
  assert.equal(typeof callback, "function");
  release();
  assert.equal(await installed, true);
});
