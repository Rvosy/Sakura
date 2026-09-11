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

test("real errors retain original messages and frames with credentials redacted", async () => {
  const env=harness();
  const error=new TypeError("Cannot read properties of undefined");
  env.listeners.get("error")({error,filename:"http://tauri.localhost/settings/index.js",lineno:42,colno:7});
  const rejection=new Error("Connection refused token=PRIVATE_KEY_VALUE");
  rejection.stack="Error: Connection refused token=PRIVATE_KEY_VALUE\n at send (tauri://localhost/chat/main.js:19:5)";
  env.listeners.get("unhandledrejection")({reason:rejection});
  env.listeners.get("error")({target:{src:"https://private.example/PRIVATE_PATH.js"}});
  await env.diagnostics.flush();
  const entries=env.calls.find(([c])=>c===RUNTIME_DIAGNOSTICS_COMMAND)[1].entries;
  assert.equal(entries[0].details.file,"desktop/frontend/settings/index.js");
  assert.match(entries[0].diagnostic,/Cannot read properties of undefined/);
  assert.match(entries[1].exceptionStack,/Connection refused/);
  assert.equal(entries[0].details.line,42);
  assert.equal(entries[0].details.causeType,"TypeError");
  assert.equal(entries[1].details.line,19);
  assert.equal(entries[2].details.stage,"resource");
  assert.equal(entries[2].details.file,undefined);
  assert.equal(JSON.stringify(entries).includes("PRIVATE"),false);
});


test("caught plugin exceptions retain causes and stages through the shared diagnostic transport", async () => {
  const env = harness();
  const cause = new TypeError("model shader failed token=private-value at C:\\Users\\private\\model.bin");
  const failure = new Error("renderer mount failed", { cause });
  assert.equal(env.diagnostics.reportError(failure, { command: "visual_renderer", stage: "visual.renderer.ready", code: "VISUAL_RENDERER_FAILED" }), true);
  assert.equal(env.diagnostics.reportError(failure, { command: "visual_renderer" }), false);
  await env.diagnostics.flush();
  const entries = env.calls.find(([command]) => command === RUNTIME_DIAGNOSTICS_COMMAND)[1].entries;
  assert.equal(entries.length, 1);
  assert.equal(entries[0].stage, "visual.renderer.ready");
  assert.equal(entries[0].code, "VISUAL_RENDERER_FAILED");
  assert.match(entries[0].diagnostic, /model shader failed/);
  assert.match(entries[0].exceptionStack, /Caused by:/);
  assert.doesNotMatch(JSON.stringify(entries), /private-value|Users/);
});

test("Studio polling remains debug and failed calls keep the method without request contents", async () => {
  let failed = false;
  const env = harness(async () => { if (failed) throw new Error("worker went away"); return {}; });
  await env.diagnostics.invoke("studio_request", { method: "studio.visual.catalog", params: { private: "private-body" } });
  failed = true;
  await assert.rejects(env.diagnostics.invoke("studio_request", { method: "studio.visual.open", params: { private: "private-body" } }));
  await env.diagnostics.flush();
  const entries = env.calls.find(([command]) => command === RUNTIME_DIAGNOSTICS_COMMAND)[1].entries;
  assert.ok(entries.filter(entry => entry.command === "studio.visual.catalog").every(entry => entry.level === "debug"));
  assert.match(entries.find(entry => entry.outcome === "failed").diagnostic, /worker went away/);
  assert.doesNotMatch(JSON.stringify(entries), /private-body/);
});
