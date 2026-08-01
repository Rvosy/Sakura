import assert from "node:assert/strict";
import test from "node:test";

import { createRealChatClient } from "../chat/real-chat-client.js";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
}

function lifecyclePublication() {
  return {
    supervisor: {
      state: "running",
      generationId: "generation-1",
      generationNumber: 1,
      restartPending: false,
      lastFailure: null,
    },
    snapshot: { generationId: "generation-1", revision: 7, readiness: "ready" },
  };
}

function harness(sendResponses = []) {
  let nativeListener = null;
  const calls = [];
  const intervals = new Map();
  let nextInterval = 0;
  globalThis.window = {
    setInterval(callback) { const id = ++nextInterval; intervals.set(id, callback); return id; },
    clearInterval(id) { intervals.delete(id); },
  };
  const invoke = async (name, payload) => {
    calls.push([name, payload]);
    if (name === "runtime_lifecycle_snapshot") return lifecyclePublication();
    if (name === "chat_send") return sendResponses.shift();
    if (name === "chat_cancel") return { accepted: true, operationId: payload.payload.operationId };
    throw new Error(name);
  };
  return {
    calls,
    emit(payload) { nativeListener({ payload }); },
    create(onEvent) {
      return createRealChatClient({
        invoke,
        listen: async (_name, listener) => { nativeListener = listener; return () => { nativeListener = null; }; },
        onEvent,
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
