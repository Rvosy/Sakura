import assert from "node:assert/strict";
import test from "node:test";

import {
  createUpdateAnnouncementController,
  UPDATE_ANNOUNCEMENT_IDLE_MS,
} from "../chat/update-announcement-controller.js";

function harness({ checkResult = { status: "pending", version: "1.2.0" } } = {}) {
  let timestamp = 0;
  let idle = true;
  let nextOperation = 0;
  const checks = [];
  const announcements = [];
  const announcementResults = [];
  const diagnostics = [];
  const controller = createUpdateAnnouncementController({
    check: async () => {
      checks.push(true);
      return checkResult;
    },
    announce: async () => {
      announcements.push(true);
      const result = announcementResults.shift();
      if (result instanceof Error) throw result;
      return result || { operationId: `update-op-${++nextOperation}` };
    },
    isIdle: () => idle,
    now: () => timestamp,
    setInterval: () => 1,
    clearInterval: () => {},
    onDiagnostic: (event, details) => diagnostics.push([event, details]),
  });
  return {
    controller,
    checks,
    announcements,
    announcementResults,
    diagnostics,
    setIdle(value) { idle = value; },
    advance(value) { timestamp += value; },
  };
}

test("announcement requires a fresh continuous three-second idle window", async () => {
  const env = harness();
  await env.controller.refresh();
  await env.controller.tick();
  env.advance(UPDATE_ANNOUNCEMENT_IDLE_MS - 1);
  await env.controller.tick();
  assert.equal(env.announcements.length, 0);

  env.setIdle(false);
  await env.controller.tick();
  env.setIdle(true);
  env.advance(UPDATE_ANNOUNCEMENT_IDLE_MS);
  await env.controller.tick();
  assert.equal(env.announcements.length, 0);
  env.advance(UPDATE_ANNOUNCEMENT_IDLE_MS);
  await env.controller.tick();
  assert.equal(env.announcements.length, 1);
});

test("manual chat or greeting activity resets the idle gate but keeps the candidate", async () => {
  const env = harness();
  await env.controller.refresh();
  await env.controller.tick();
  env.advance(UPDATE_ANNOUNCEMENT_IDLE_MS);
  env.controller.noteActivity();
  await env.controller.tick();
  assert.equal(env.announcements.length, 0);
  assert.equal(env.controller.isPending(), true);
  env.advance(UPDATE_ANNOUNCEMENT_IDLE_MS);
  await env.controller.tick();
  assert.equal(env.announcements.length, 1);
});

for (const type of ["chat.completed", "chat.cancelled", "chat.failed"]) {
  test(`a requested catalog refresh resumes after ${type}`, async () => {
    const checkResult = { status: "pending", version: "1.2.0" };
    const env = harness({ checkResult });
    await env.controller.refresh();
    await env.controller.tick();
    env.advance(UPDATE_ANNOUNCEMENT_IDLE_MS);
    await env.controller.tick();
    checkResult.status = "up_to_date";
    await env.controller.refresh();
    env.controller.handleChatEvent({ type, operationId: "update-op-1" });
    await Promise.resolve();
    assert.equal(env.controller.isPending(), false);
  });
}

test("completed update clears pending and a failed model terminal retries only once", async () => {
  const completed = harness();
  await completed.controller.refresh();
  await completed.controller.tick();
  completed.advance(UPDATE_ANNOUNCEMENT_IDLE_MS);
  await completed.controller.tick();
  completed.controller.handleChatEvent({ type: "chat.completed", operationId: "update-op-1" });
  assert.equal(completed.controller.isPending(), false);

  const retry = harness();
  await retry.controller.refresh();
  await retry.controller.tick();
  retry.advance(UPDATE_ANNOUNCEMENT_IDLE_MS);
  await retry.controller.tick();
  retry.controller.handleChatEvent({ type: "chat.failed", operationId: "update-op-1" });
  assert.equal(retry.controller.snapshot().failedAttempts, 1);
  await retry.controller.tick();
  retry.advance(UPDATE_ANNOUNCEMENT_IDLE_MS);
  await retry.controller.tick();
  retry.controller.handleChatEvent({ type: "chat.failed", operationId: "update-op-2" });
  assert.equal(retry.controller.isPending(), false);
  assert.equal(retry.announcements.length, 2);
});

test("cancellation and generation changes do not consume the model retry", async () => {
  const env = harness();
  await env.controller.refresh();
  await env.controller.tick();
  env.advance(UPDATE_ANNOUNCEMENT_IDLE_MS);
  await env.controller.tick();
  env.controller.handleChatEvent({ type: "chat.cancelled", operationId: "update-op-1" });
  assert.equal(env.controller.snapshot().failedAttempts, 0);

  await env.controller.tick();
  env.advance(UPDATE_ANNOUNCEMENT_IDLE_MS);
  await env.controller.tick();
  env.controller.generationChanged();
  assert.equal(env.controller.snapshot().failedAttempts, 0);
  assert.equal(env.controller.isPending(), true);
});

test("transient dispatch races wait again without consuming a retry", async () => {
  const env = harness();
  env.announcementResults.push(new Error("CHAT_INTERACTION_ACTIVE"));
  await env.controller.refresh();
  await env.controller.tick();
  env.advance(UPDATE_ANNOUNCEMENT_IDLE_MS);
  await env.controller.tick();
  assert.equal(env.controller.snapshot().failedAttempts, 0);
  assert.equal(env.controller.isPending(), true);
});

test("an unconfirmed delivery never automatically repeats the accepted announcement", async () => {
  const env = harness();
  await env.controller.refresh();
  await env.controller.tick();
  env.advance(UPDATE_ANNOUNCEMENT_IDLE_MS);
  await env.controller.tick();
  await env.controller.refresh();
  env.controller.handleChatEvent({
    type: "chat.failed", operationId: "update-op-1",
    error: { code: "CHAT_DELIVERY_UNCONFIRMED", retryable: false },
  });
  await env.controller.tick();
  env.advance(UPDATE_ANNOUNCEMENT_IDLE_MS);
  await env.controller.tick();
  assert.equal(env.controller.isPending(), false);
  assert.equal(env.announcements.length, 1);
});

test("disabling drops the pending candidate while enabling immediately refreshes", async () => {
  const env = harness();
  await env.controller.refresh();
  env.controller.applyPreferences({ autoCheckEnabled: false });
  assert.equal(env.controller.isPending(), false);
  const before = env.checks.length;
  env.controller.applyPreferences({ autoCheckEnabled: true });
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(env.checks.length, before + 1);
  assert.equal(env.controller.isPending(), true);
});

test("disabling while a check is in flight cannot restore a stale pending candidate", async () => {
  let resolveCheck;
  const check = new Promise((resolve) => { resolveCheck = resolve; });
  const controller = createUpdateAnnouncementController({
    check: () => check,
    announce: async () => ({ operationId: "unused" }),
    isIdle: () => true,
    setInterval: () => 1,
    clearInterval: () => {},
  });

  const refresh = controller.refresh();
  controller.applyPreferences({ autoCheckEnabled: false });
  resolveCheck({ status: "pending", version: "1.2.0" });
  await refresh;

  assert.equal(controller.isPending(), false);
  assert.equal(controller.snapshot().enabled, false);
});

test("pending state is exposed so screen awareness can yield priority", async () => {
  const env = harness();
  await env.controller.refresh();
  assert.equal(env.controller.isPending(), true);
  await env.controller.tick();
  env.advance(UPDATE_ANNOUNCEMENT_IDLE_MS);
  await env.controller.tick();
  assert.equal(env.controller.isPending(), true);
  env.controller.handleChatEvent({ type: "chat.completed", operationId: "update-op-1" });
  assert.equal(env.controller.isPending(), false);
});

test("a terminal that wins the announce response race is applied exactly once", async () => {
  let resolveAnnouncement;
  let announceCalls = 0;
  const announcement = new Promise((resolve) => { resolveAnnouncement = resolve; });
  let timestamp = 0;
  const controller = createUpdateAnnouncementController({
    check: async () => ({ status: "pending", version: "1.2.0" }),
    announce: () => {
      announceCalls += 1;
      return announcement;
    },
    isIdle: () => true,
    now: () => timestamp,
    setInterval: () => 1,
    clearInterval: () => {},
  });
  await controller.refresh();
  await controller.tick();
  timestamp += UPDATE_ANNOUNCEMENT_IDLE_MS;
  const dispatch = controller.tick();
  await Promise.resolve();
  assert.equal(controller.snapshot().dispatching, true);
  await controller.tick();
  assert.equal(announceCalls, 1);

  controller.handleChatEvent({ type: "chat.completed", operationId: "early-update" });
  resolveAnnouncement({ operationId: "early-update" });
  await dispatch;

  assert.equal(controller.isPending(), false);
  assert.equal(controller.snapshot().operationId, null);
});


test("a generation change replaces an in-flight check and disposal ignores its result", async () => {
  const requests = [];
  const controller = createUpdateAnnouncementController({
    check: () => new Promise(resolve => requests.push(resolve)),
    announce: async () => ({ operationId: "unused" }), isIdle: () => false,
  });
  const first = controller.refresh();
  controller.generationChanged();
  requests[0]({ status: "pending" });
  await first;
  assert.equal(controller.snapshot().pending, false);
  assert.equal(requests.length, 2);
  controller.dispose();
  requests[1]({ status: "pending" });
  await Promise.resolve();
  assert.equal(controller.isPending(), false);
});
