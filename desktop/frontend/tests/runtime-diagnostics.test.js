import assert from "node:assert/strict";
import test from "node:test";

import {
  createRuntimeDiagnostics,
  RUNTIME_DIAGNOSTICS_COMMAND,
} from "../core/runtime-diagnostics.js";

function harness(handler = async () => ({ ok: true })) {
  const calls = [];
  const listeners = new Map();
  let timer = null;
  const nativeInvoke = async (command, args) => {
    calls.push([command, args]);
    if (command === RUNTIME_DIAGNOSTICS_COMMAND) return undefined;
    return handler(command, args);
  };
  const diagnostics = createRuntimeDiagnostics({
    invoke: nativeInvoke,
    now: (() => { let clock = 10; return () => ++clock; })(),
    setTimer(callback) { timer = callback; return 7; },
    clearTimer() { timer = null; },
    windowObject: {
      addEventListener(name, callback) { listeners.set(name, callback); },
      removeEventListener(name) { listeners.delete(name); },
    },
  });
  return {
    calls,
    diagnostics,
    listeners,
    timer: () => timer,
  };
}

test("invoke wrapper preserves argument, result, and rejection object identity", async () => {
  const result = Object.freeze({ accepted: true });
  const args = { payload: { privateChat: "WP4L01 PRIVATE CHAT" } };
  const success = harness(async (_command, received) => {
    assert.equal(received, args);
    return result;
  });
  assert.equal(await success.diagnostics.invoke("chat_send", args), result);
  await success.diagnostics.flush();

  const failure = Object.assign(new Error("WP4L01 PRIVATE ERROR"), {
    privateResult: "WP4L01 PRIVATE RESULT",
  });
  const failed = harness(async () => { throw failure; });
  await assert.rejects(
    failed.diagnostics.invoke("settings_memory_search", args),
    (received) => received === failure,
  );
  await failed.diagnostics.flush();

  const serialized = JSON.stringify([...success.calls, ...failed.calls]);
  const diagnosticCalls = [...success.calls, ...failed.calls]
    .filter(([command]) => command === RUNTIME_DIAGNOSTICS_COMMAND);
  assert.equal(diagnosticCalls.length, 2);
  const diagnosticJson = JSON.stringify(diagnosticCalls);
  assert.equal(diagnosticJson.includes("PRIVATE CHAT"), false);
  assert.equal(diagnosticJson.includes("PRIVATE ERROR"), false);
  assert.equal(diagnosticJson.includes("PRIVATE RESULT"), false);
  assert.equal(serialized.includes("PRIVATE CHAT"), true, "the product call must retain its args");
});

test("batches contain only controlled fields and never arbitrary attributes", async () => {
  const env = harness();
  assert.equal(env.diagnostics.record({
    level: "info",
    event: "webview.chat.terminal",
    outcome: "completed",
    operationId: "operation-7",
    revision: 3,
    arguments: { secret: "WP4L01 TOOL ARGUMENT" },
    result: "WP4L01 CHAT BODY",
    attributes: { arbitrary: true },
  }), true);
  await env.diagnostics.flush();

  const [, payload] = env.calls.find(([command]) => command === RUNTIME_DIAGNOSTICS_COMMAND);
  assert.deepEqual(payload.entries, [{
    level: "info",
    event: "webview.chat.terminal",
    outcome: "completed",
    operationId: "operation-7",
    revision: 3,
  }]);
});

test("diagnostic transport failure never changes a successful product command", async () => {
  const result = { value: 9 };
  const calls = [];
  const diagnostics = createRuntimeDiagnostics({
    invoke: async (command, args) => {
      calls.push([command, args]);
      if (command === RUNTIME_DIAGNOSTICS_COMMAND) throw new Error("diagnostics unavailable");
      return result;
    },
    now: () => 1,
    setTimer: () => 1,
    clearTimer() {},
    windowObject: null,
  });

  assert.equal(await diagnostics.invoke("settings_tools_get"), result);
  await diagnostics.flush();
  assert.equal(calls.filter(([command]) => command === "settings_tools_get").length, 1);
});

test("unhandled errors use fixed codes without reading event content", async () => {
  const env = harness();
  env.listeners.get("error")?.({
    message: "WP4L01 PRIVATE ERROR",
    error: { stack: "WP4L01 PRIVATE STACK" },
  });
  env.listeners.get("unhandledrejection")?.({ reason: "WP4L01 PRIVATE REJECTION" });
  await env.diagnostics.flush();

  const [, payload] = env.calls.find(([command]) => command === RUNTIME_DIAGNOSTICS_COMMAND);
  assert.deepEqual(payload.entries.map((entry) => entry.code), [
    "WEBVIEW_UNHANDLED_ERROR",
    "WEBVIEW_UNHANDLED_REJECTION",
  ]);
  assert.equal(JSON.stringify(payload).includes("PRIVATE"), false);
});

test("flush never sends more than sixty-four entries per command", async () => {
  const env = harness();
  for (let revision = 0; revision < 70; revision += 1) {
    env.diagnostics.record({
      level: "debug",
      event: "webview.interaction.stage",
      outcome: "completed",
      revision,
    });
  }
  await env.diagnostics.flush();
  await env.diagnostics.flush();

  const batches = env.calls
    .filter(([command]) => command === RUNTIME_DIAGNOSTICS_COMMAND)
    .map(([, payload]) => payload.entries.length);
  assert.deepEqual(batches, [64, 6]);
  assert.equal(batches.every((size) => size >= 1 && size <= 64), true);
});
