import assert from "node:assert/strict";
import test from "node:test";

import { createRealChatClient } from "../chat/real-chat-client.js";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
}

function lifecyclePublication(generationNumber = 1, state = "running", readiness = "ready") {
  return {
    supervisor: {
      state,
      generationId: `generation-${generationNumber}`,
      generationNumber,
      appShutdown: false,
      failure: state === "failed" ? {
        code: "unexpected_exit",
        message: "Core 进程意外退出。",
      } : null,
    },
    snapshot: readiness ? {
      generationId: `generation-${generationNumber}`,
      revision: 7,
      readiness,
    } : null,
  };
}

function harness(sendResponses = []) {
  let nativeListener = null;
  const calls = [];
  const intervals = new Map();
  let nextInterval = 0;
  let publication = lifecyclePublication();
  globalThis.window = {
    setInterval(callback) { const id = ++nextInterval; intervals.set(id, callback); return id; },
    clearInterval(id) { intervals.delete(id); },
  };
  const invoke = async (name, payload) => {
    calls.push([name, payload]);
    if (name === "runtime_lifecycle_snapshot") return publication;
    if (name === "chat_send") return sendResponses.shift();
    if (name === "chat_cancel") return { accepted: true, operationId: payload.payload.operationId };
    throw new Error(name);
  };
  return {
    calls,
    emit(payload) { nativeListener({ payload }); },
    setPublication(next) { publication = next; },
    async tick() {
      await Promise.all([...intervals.values()].map((callback) => callback()));
    },
    create(onEvent, options = {}) {
      return createRealChatClient({
        invoke,
        listen: async (_name, listener) => { nativeListener = listener; return () => { nativeListener = null; }; },
        onEvent,
        initialPreparedGenerationId: "generation-1",
        ...options,
      });
    },
  };
}

test("started and terminal events may win the send response race without leaving a duplicate active turn", async () => {
  const response = deferred();
  const events = [];
  const env = harness([response.promise, {
    accepted: true,
    operationId: "op-2",
    cancelHandle: "cancel-2",
    generationId: "generation-1",
    generationNumber: 1,
  }]);
  const client = env.create((event) => events.push(event));
  await client.start();
  const first = client.send({ message: "first" });
  env.emit({ type: "chat.started", generationId: "generation-1", generationNumber: 1, operationId: "op-1" });
  env.emit({
    type: "chat.completed",
    generationId: "generation-1",
    generationNumber: 1,
    operationId: "op-1",
    reply: { segments: [{ text: "done" }] },
  });
  response.resolve({
    accepted: true,
    operationId: "op-1",
    cancelHandle: "cancel-1",
    generationId: "generation-1",
    generationNumber: 1,
  });
  await first;
  await client.send({ message: "second" });
  assert.deepEqual(events.slice(1).map((event) => event.type), ["chat.started", "chat.completed"]);
  assert.equal(env.calls.filter(([name]) => name === "chat_send").length, 2);
  client.dispose();
});

test("generation change seals pending send, cancel, and old native events", async () => {
  const response = deferred();
  const events = [];
  const env = harness([response.promise]);
  const client = env.create((event) => events.push(event));
  await client.start();

  const send = client.send({ message: "will be interrupted" });
  env.emit({ type: "chat.started", generationId: "generation-1", generationNumber: 1, operationId: "old-op" });
  assert.equal(await client.cancel("old-op"), true);

  env.setPublication(lifecyclePublication(2, "spawning", null));
  await env.tick();
  response.resolve({
    accepted: true,
    operationId: "old-op",
    cancelHandle: "old-cancel",
    generationId: "generation-1",
    generationNumber: 1,
  });
  await assert.rejects(send, /CHAT_GENERATION_INVALIDATED/);

  const count = events.length;
  env.emit({
    type: "chat.completed",
    generationId: "generation-1",
    generationNumber: 1,
    operationId: "old-op",
    reply: { segments: [{ text: "late" }] },
  });
  assert.equal(events.length, count);
  assert.equal(env.calls.some(([name]) => name === "chat_cancel"), false);
  client.dispose();
});

test("new generation stays rehydrating until its complete Snapshot resources are prepared", async () => {
  const gate = deferred();
  const events = [];
  const env = harness();
  const client = env.create((event) => events.push(event), {
    prepareGeneration: ({ generationId }) => {
      assert.equal(generationId, "generation-2");
      return gate.promise;
    },
  });
  await client.start();

  env.setPublication(lifecyclePublication(2));
  const poll = env.tick();
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(events.at(-1).status, "rehydrating");
  assert.equal(events.some((event) => event.generationNumber === 2 && event.status === "ready"), false);

  gate.resolve(true);
  await poll;
  assert.equal(events.at(-1).status, "ready");
  assert.equal(events.at(-1).generationNumber, 2);
  client.dispose();
});

test("failed readiness stays non-retryable while Core is still running", async () => {
  const events = [];
  const env = harness();
  const client = env.create((event) => events.push(event), {
    prepareGeneration: async () => false,
  });
  await client.start();

  env.setPublication(lifecyclePublication(2, "running", "failed"));
  await env.tick();
  assert.deepEqual(
    events.filter((event) => event.generationNumber === 2).map(({ status, canRetry }) => [status, canRetry]),
    [["rehydrating", false], ["failed", false]],
  );
  client.dispose();
});

test("stopped Core failure exposes its safe reason and manual retry", async () => {
  const events = [];
  const env = harness();
  const client = env.create((event) => events.push(event));
  await client.start();

  env.setPublication(lifecyclePublication(1, "failed", null));
  await env.tick();
  const failure = events.at(-1);
  assert.equal(failure.status, "failed");
  assert.equal(failure.canRetry, true);
  assert.deepEqual(failure.failure, {
    code: "unexpected_exit",
    message: "Core 进程意外退出。",
  });
  client.dispose();
});

test("cancel clicked after early started waits for the opaque send response handle", async () => {
  const response = deferred();
  const env = harness([response.promise]);
  const client = env.create(() => {});
  await client.start();
  const send = client.send({ message: "slow" });
  env.emit({ type: "chat.started", generationId: "generation-1", generationNumber: 1, operationId: "op-slow" });
  assert.equal(await client.cancel("op-slow"), true);
  response.resolve({
    accepted: true,
    operationId: "op-slow",
    cancelHandle: "opaque-cancel",
    generationId: "generation-1",
    generationNumber: 1,
  });
  await send;
  assert.deepEqual(env.calls.find(([name]) => name === "chat_cancel"), [
    "chat_cancel",
    { payload: { operationId: "op-slow", cancelHandle: "opaque-cancel" } },
  ]);
  client.dispose();
});

test("send forwards only the opaque screenshot attachment id when present", async () => {
  const attachmentId = `screen-${"a".repeat(32)}`;
  const env = harness([{
    accepted: true,
    operationId: "op-screen",
    cancelHandle: "cancel-screen",
    generationId: "generation-1",
    generationNumber: 1,
  }]);
  const client = env.create(() => {});
  await client.start();
  await client.send({ message: "看看这里", attachmentId });
  assert.deepEqual(env.calls.find(([name]) => name === "chat_send"), [
    "chat_send",
    { payload: { message: "看看这里", attachmentId } },
  ]);
  client.dispose();
});
