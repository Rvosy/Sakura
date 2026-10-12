import assert from "node:assert/strict";
import test from "node:test";
import { createAsrAvailability } from "../audio/asr-availability.js";

test("microphone visibility tracks Hub availability without inspecting or selecting a provider", async () => {
  let enabled = false;
  const changes = [], calls = [];
  const availability = createAsrAvailability({
    invoke: async (name) => { calls.push(name); return { enabled }; },
    onChange: (value) => changes.push(value), schedule: () => 1, unschedule: () => {},
  });
  await availability.start();
  assert.equal(availability.enabled(), false);
  enabled = true; await availability.refresh(); await availability.refresh();
  enabled = false; await availability.refresh();
  assert.deepEqual(changes, [true, false]);
  assert.equal(calls.every((name) => name === "asr_availability"), true);
  availability.dispose();
});

test("availability polling is single flight and an obsolete callback cannot reveal the microphone", async () => {
  let resolve;
  const changes = [];
  let calls = 0;
  const availability = createAsrAvailability({
    invoke: () => { calls += 1; return new Promise((done) => { resolve = done; }); },
    onChange: (value) => changes.push(value), schedule: () => 1, unschedule: () => {},
  });
  const first = availability.refresh(); const second = availability.refresh();
  assert.equal(calls, 1);
  availability.dispose(); resolve({ enabled: true });
  await first; await second;
  assert.deepEqual(changes, []);
});
