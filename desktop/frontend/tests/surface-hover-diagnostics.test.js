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
      now: () => time,
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

test("continuous native hover failures retain the first cause, bounded summaries, and recovery", async () => {
  const env = fixture();
  await env.ready;
  for (const delay of [100, 200, 400, 800, 1000, 1000]) {
    assert.equal(env.timer().delay, delay);
    await env.tick();
  }
  await env.diagnostics.flush();
  assert.equal(env.entries.length, 1, "repeated probes do not emit per-read lifecycle or failure entries");
  assert.equal(env.entries[0].code, "HOVER_UNAVAILABLE");
  assert.match(env.entries[0].diagnostic, /native window missing/);
  assert.doesNotMatch(JSON.stringify(env.entries), /private-key/);

  // Advance by scheduled completion, not wall-clock sleeps.
  while (env.calls.filter((command) => command === "pet_surface_hovered").length < 128) await env.tick();
  await env.diagnostics.flush();
  const summaries = env.entries.filter((entry) => entry.event === "runtime.message");
  assert.equal(summaries.length, 2);
  assert.equal(summaries[0].fields.count, 64);
  assert.equal(summaries[0].fields.failed, 64);
  assert.equal(summaries[0].fields.elapsed_ms, 60500);
  assert.equal(summaries[1].fields.count, 124);
  assert.equal(summaries[1].fields.failed, 124);
  assert.equal(summaries[1].fields.elapsed_ms, 120500);
  assert.equal(summaries[0].message, summaries[1].message);
  assert.equal(summaries[0].fields.diagnostic, summaries[1].fields.diagnostic);

  env.setResult(() => true);
  await env.tick();
  await env.diagnostics.flush();
  const recovered = env.entries.at(-1);
  assert.equal(recovered.level, "info");
  assert.equal(recovered.fields.recovery_outcome, "success");
  assert.equal(recovered.fields.count, 128);
  assert.equal(recovered.fields.failed, 128);
  assert.equal(recovered.fields.elapsed_ms, 125500);
  assert.deepEqual(env.changes, [["native-surface", true]]);
  assert.equal(env.timer().delay, 50);

  env.setResult(() => { throw new Error("HOVER_UNAVAILABLE: native window missing"); });
  await env.tick();
  await env.diagnostics.flush();
  assert.equal(env.entries.filter((entry) => entry.event === "webview.command.failed").length, 2);
  assert.equal(env.timer().delay, 100);
  assert.deepEqual(env.changes.at(-1), ["native-surface", false]);
  await assert.rejects(env.diagnostics.invoke("chat_send"), /OTHER_COMMAND_FAILED/);
  await assert.rejects(env.diagnostics.invoke("chat_send"), /OTHER_COMMAND_FAILED/);
  await env.diagnostics.flush();
  assert.equal(env.entries.filter((entry) => entry.command === "chat_send" && entry.outcome === "failed").length, 2);
  env.probe.dispose();
  await env.diagnostics.flush();
  assert.equal(env.entries.at(-1).fields.count, 1);
  assert.equal(env.entries.at(-1).fields.failed, 129, "the probe retains a total across recovered episodes");
  assert.equal(env.entries.at(-1).fields.recovery_outcome, "skipped");
});

for (const completesWithError of [false, true]) {
  test(`stopping a failed probe records its count and ignores a late ${completesWithError ? "failure" : "recovery"}`, async () => {
    const env = fixture();
    await env.ready;
    await env.tick();
    let finish;
    env.setResult(() => new Promise((resolve, reject) => {
      finish = () => completesWithError ? reject(new Error("LATE_FAILURE")) : resolve(true);
    }));
    const running = env.tick();
    env.probe.dispose();
    await env.diagnostics.flush();
    assert.equal(env.entries.length, 2);
    assert.equal(env.entries[1].fields.count, 2);
    assert.equal(env.entries[1].fields.failed, 2);
    assert.equal(env.entries[1].fields.elapsed_ms, 300);
    assert.equal(env.entries[1].fields.recovery_outcome, "skipped");
    finish();
    await running;
    env.probe.dispose();
    await env.diagnostics.flush();
    assert.equal(env.entries.length, 2);
    assert.equal(env.timer(), null);
    assert.deepEqual(env.changes, []);
  });
}
