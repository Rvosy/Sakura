import assert from "node:assert/strict";
import test from "node:test";

import { createFakeChatCore } from "../chat/fake-chat-core.js";

function scheduler() {
  let id = 0;
  const tasks = new Map();
  return {
    setTimer(callback, delay) {
      const token = ++id;
      tasks.set(token, { callback, delay, token });
      return token;
    },
    clearTimer(token) {
      tasks.delete(token);
    },
    runNext() {
      const next = [...tasks.values()].sort((left, right) => left.delay - right.delay || left.token - right.token)[0];
      if (!next) return false;
      tasks.delete(next.token);
      next.callback();
      return true;
    },
    runAll(limit = 30) {
      let count = 0;
      while (this.runNext()) {
        count += 1;
        assert.ok(count <= limit, "scheduler exceeded deterministic task limit");
      }
    },
    size() {
      return tasks.size;
    },
  };
}

function readyCore() {
  const clock = scheduler();
  const core = createFakeChatCore({
    setTimer: clock.setTimer,
    clearTimer: clock.clearTimer,
    delays: { boot: 2, normal: 4, slow: 20, restart: 3 },
    portraits: { default: "__default__", multi: ["happy", "thinking", "concerned"] },
  });
  const events = [];
  core.subscribe((event) => events.push(event));
  core.start();
  clock.runAll();
  assert.equal(core.snapshot().lifecycleStatus, "ready");
  return { clock, core, events };
}

test("normal and multi scenarios publish one completed terminal with the frozen segment shape", () => {
  for (const message of ["hello", "/multi"]) {
    const { clock, core, events } = readyCore();
    const accepted = core.send({ message });
    clock.runAll();
    const operationEvents = events.filter((event) => event.operationId === accepted.operationId);
    assert.deepEqual(operationEvents.map((event) => event.type), ["chat.started", "chat.completed"]);
    const segments = operationEvents[1].reply.segments;
    assert.equal(segments.length, message === "/multi" ? 3 : 1);
    assert.deepEqual(Object.keys(segments[0]).sort(), ["portrait", "suppressTts", "text", "tone", "translation"]);
    assert.equal(core.snapshot().activeOperations, 0);
  }
});

test("slow response can be cancelled exactly once and its late completion is discarded", () => {
  const { clock, core, events } = readyCore();
  const accepted = core.send({ message: "/slow wait" });
  assert.equal(core.cancel(accepted.operationId).accepted, true);
  assert.equal(core.cancel(accepted.operationId).accepted, false);
  clock.runAll();
  assert.deepEqual(events.filter((event) => event.operationId === accepted.operationId).map((event) => event.type), ["chat.started", "chat.cancelled"]);
});

test("error is retryable, operation-scoped, and does not change readiness", () => {
  const { clock, core, events } = readyCore();
  const accepted = core.send({ message: "/error" });
  clock.runAll();
  const failed = events.find((event) => event.type === "chat.failed" && event.operationId === accepted.operationId);
  assert.equal(failed.error.code, "FAKE_PROVIDER_UNREACHABLE");
  assert.equal(failed.error.retryable, true);
  assert.deepEqual(failed.error.details, {});
  assert.equal(core.snapshot().lifecycleStatus, "ready");
});

test("restart cancels the old operation and advances generation before returning ready", () => {
  const { clock, core, events } = readyCore();
  const accepted = core.send({ message: "/restart" });
  clock.runAll();
  assert.deepEqual(events.filter((event) => event.operationId === accepted.operationId).map((event) => event.type), ["chat.started", "chat.cancelled"]);
  assert.equal(core.snapshot().generationNumber, 2);
  assert.equal(core.snapshot().lifecycleStatus, "ready");
  assert.ok(events.some((event) => event.type === "lifecycle" && event.status === "core_crashed"));
  assert.ok(events.some((event) => event.type === "lifecycle" && event.status === "restarting" && event.generationNumber === 2));
});

test("dispose clears every timer and prevents further publication", () => {
  const { clock, core, events } = readyCore();
  core.send({ message: "/slow" });
  const count = events.length;
  core.dispose();
  assert.equal(clock.size(), 0);
  clock.runAll();
  assert.equal(events.length, count);
  assert.equal(core.snapshot().activeOperations, 0);
});
