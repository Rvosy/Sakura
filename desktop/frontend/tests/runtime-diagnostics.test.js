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


test("coded invoke failures preserve only a bounded redacted diagnostic", async () => {
  const failure = "REQUEST_DEADLINE_EXCEEDED: Provider token=visible did not respond";
  const env = harness(async () => { throw failure; });
  await assert.rejects(env.diagnostics.invoke("chat_send", {}), (error) => error === failure);
  await env.diagnostics.flush();

  const [, payload] = env.calls.find(([command]) => command === RUNTIME_DIAGNOSTICS_COMMAND);
  assert.deepEqual(payload.entries.filter((entry) => entry.outcome === "failed"), [{
    level: "warn",
    event: "webview.command.failed",
    command: "chat_send",
    outcome: "failed",
    code: "REQUEST_DEADLINE_EXCEEDED",
    diagnostic: "Provider token=[REDACTED] did not respond",
    elapsedMs: 1,
  }]);
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


test("custom messages are bounded and cleaned before IPC without changing plain HTML text", async () => {
  const env = harness();
  assert.equal(env.diagnostics.message("info", "中文".repeat(800), {
    nested: { password: "private-password", count: 2 },
    credential: "token=private-token", html: "<b>纯文本</b>",
  }), true);
  await env.diagnostics.flush();
  const [, payload] = env.calls.find(([command]) => command === RUNTIME_DIAGNOSTICS_COMMAND);
  const entry = payload.entries[0];
  assert.ok(new TextEncoder().encode(entry.message).length <= 1024);
  assert.ok(entry.message.includes("[truncated]"));
  assert.equal(entry.fields.nested.count, 2);
  assert.equal(entry.fields.html, "<b>纯文本</b>");
  assert.ok(!JSON.stringify(payload).includes("private-"));
});
