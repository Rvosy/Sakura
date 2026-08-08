import assert from "node:assert/strict";
import test from "node:test";

import { createInteractionLatencyTracer } from "../core/interaction-latency.js";

test("interaction diagnostics batch timing metadata without copying interaction payloads", async () => {
  const calls = [];
  let clock = 10;
  let timer = null;
  const tracer = createInteractionLatencyTracer({
    source: "settings",
    enabled: true,
    invoke: async (command, args) => {
      calls.push([command, args]);
      if (command !== "record_interaction_latency_trace") clock += 4;
      return { ok: true };
    },
    now: () => clock,
    timeOrigin: 1_800_000_000_000,
    setTimer(callback) { timer = callback; return 7; },
    clearTimer() { timer = null; },
    consoleObject: null,
  });

  const gesture = tracer.createGesture("layout-control-panel-width");
  const frame = tracer.atRevision(gesture, 3);
  clock = 18;
  tracer.mark("layout.input", frame, { event: { timeStamp: 12 } });
  await tracer.tracedInvoke(
    "settings_character_appearance_layout_frame",
    { values: { privatePayloadMustNotEnterTrace: "secret" } },
    frame,
    "layout.frame",
  );
  await tracer.flush();

  assert.equal(timer, null);
  assert.equal(calls[0][0], "settings_character_appearance_layout_frame");
  assert.equal(calls[0][1].trace.gestureId, "settings-layout-control-panel-width-1");
  const traceCall = calls.find(([command]) => command === "record_interaction_latency_trace");
  assert.ok(traceCall);
  assert.equal(JSON.stringify(traceCall[1]).includes("secret"), false);
  assert.deepEqual(
    Object.keys(traceCall[1].entries[0]).sort(),
    [
      "elapsedMs",
      "epochMs",
      "eventDelayMs",
      "eventPerfMs",
      "gestureId",
      "perfMs",
      "revision",
      "source",
      "stage",
    ],
  );
  assert.equal(traceCall[1].entries[0].eventDelayMs, 6);
});

test("disabled interaction diagnostics leave command arguments and scheduling untouched", async () => {
  const calls = [];
  const tracer = createInteractionLatencyTracer({
    source: "main",
    enabled: false,
    invoke: async (command, args) => { calls.push([command, args]); return 9; },
    now: () => 1,
    timeOrigin: 1_800_000_000_000,
    setTimer() { throw new Error("disabled diagnostics must not schedule"); },
    clearTimer() {},
    consoleObject: null,
  });

  assert.equal(tracer.createGesture("pet-drag"), null);
  assert.equal(await tracer.tracedInvoke("start_pet_drag", { revision: 2 }, null, "unused"), 9);
  assert.deepEqual(calls, [["start_pet_drag", { revision: 2 }]]);
});
