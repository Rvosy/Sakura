import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

import { createRuntimeDiagnostics, RUNTIME_DIAGNOSTICS_COMMAND } from "../core/runtime-diagnostics.js";
import { createSurfaceHoverProbe } from "../pet/surface-visibility.js";

const source = (await readFile(new URL("../app.js", import.meta.url), "utf8")).replace(/\r\n/g, "\n");
const start = source.indexOf("surfaceHoverProbe = createSurfaceHoverProbe({");
const binding = source.slice(start, source.indexOf("\n}\n\nconst ttsController", start));

function fixture() {
  let time = 0;
  let nextTimer = null;
  let result = () => { throw new Error("HOVER_UNAVAILABLE: native window missing token=private-key"); };
  let scheduled;
  const ready = new Promise((resolve) => { scheduled = resolve; });
  const entries = [];
  const changes = [];
  const calls = [];
  const nativeInvoke = async (command, args) => {
    calls.push(command);
    if (command === RUNTIME_DIAGNOSTICS_COMMAND) {
      entries.push(...args.entries);
      return;
    }
    if (command === "pet_surface_hovered") return result();
    throw new Error("OTHER_COMMAND_FAILED");
  };
  const diagnostics = createRuntimeDiagnostics({
    invoke: nativeInvoke,
    now: () => time,
    setTimer: () => 2,
    clearTimer() {},
    windowObject: null,
  });
  const probe = vm.runInNewContext(`let surfaceHoverProbe; ${binding}; surfaceHoverProbe`, {
    invoke: diagnostics.invoke,
    nativeInvoke,
    runtimeDiagnostics: diagnostics,
    surfaceHoverTracker: {
      enter: (name) => changes.push([name, true]),
      leave: (name) => changes.push([name, false]),
    },
    createSurfaceHoverProbe: (options) => createSurfaceHoverProbe({
      ...options,
      setTimer(callback, delay) {
        nextTimer = { callback, delay };
        scheduled();
        return 1;
      },
      clearTimer: () => { nextTimer = null; },
    }),
  });
  return {
    ready, entries, changes, calls, probe, diagnostics,
    timer: () => nextTimer,
    setResult: (callback) => { result = callback; },
    async tick() {
      const timer = nextTimer;
      assert.ok(timer, "a completed probe schedules its next read");
      nextTimer = null;
      time += timer.delay;
      await timer.callback();
    },
  };
}

test("native hover failures report once until recovery and preserve normal command diagnostics", async () => {
  const env = fixture();
  await env.ready;
  for (let i = 0; i < 128; i += 1) await env.tick();
  await env.diagnostics.flush();
  assert.equal(env.entries.length, 1);
  assert.equal(env.entries[0].code, "HOVER_UNAVAILABLE");
  assert.match(env.entries[0].diagnostic, /native window missing/);
  assert.doesNotMatch(JSON.stringify(env.entries), /private-key/);
  assert.equal(env.timer().delay, 1000);

  env.setResult(() => true);
  await env.tick();
  assert.deepEqual(env.changes, [["native-surface", true]]);
  assert.equal(env.timer().delay, 50);
  env.setResult(() => { throw new Error("HOVER_UNAVAILABLE: native window missing"); });
  await env.tick();
  await env.diagnostics.flush();
  assert.equal(env.entries.length, 2);
  assert.deepEqual(env.changes.at(-1), ["native-surface", false]);
  await assert.rejects(env.diagnostics.invoke("chat_send"), /OTHER_COMMAND_FAILED/);
  await assert.rejects(env.diagnostics.invoke("chat_send"), /OTHER_COMMAND_FAILED/);
  await env.diagnostics.flush();
  assert.equal(env.entries.filter((entry) => entry.command === "chat_send" && entry.outcome === "failed").length, 2);
  env.probe.dispose();
});

for (const completesWithError of [false, true]) {
  test(`disposed hover probe ignores a late ${completesWithError ? "failure" : "recovery"}`, async () => {
    const env = fixture();
    await env.ready;
    let finish;
    env.setResult(() => new Promise((resolve, reject) => {
      finish = () => completesWithError ? reject(new Error("LATE_FAILURE")) : resolve(true);
    }));
    const running = env.tick();
    env.probe.dispose();
    finish();
    await running;
    await env.diagnostics.flush();
    assert.equal(env.entries.length, 1);
    assert.equal(env.timer(), null);
    assert.deepEqual(env.changes, []);
  });
}
