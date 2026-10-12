import assert from "node:assert/strict";
import test from "node:test";
import { createAsrController } from "../audio/asr-controller.js";

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}
function fixture(handlers = {}) {
  const calls = [], states = [], errors = [], writes = [], levels = [], selections = [];
  const events = new Map(), pending = new Map();
  let sequence = 0, timerId = 0;
  let context = "generation-1:character-1";
  let draft = { value: "hello world", version: 0, selectionStart: 0, selectionEnd: 5, selectionDirection: "forward" };
  const attachments = ["screen-test-attachment"];
  const controller = createAsrController({
    invoke: async (name, args) => {
      calls.push([name, args]);
      if (handlers[name]) return handlers[name](args);
      return { recordingId: args.payload.recordingId,
        state: { asr_prepare: "ready", asr_capture_start: "recording", asr_capture_stop: "recognizing",
          asr_cancel: "cancelled", asr_poll: "recording" }[name] };
    },
    listen: async (name, handler) => { events.set(name, handler); return () => events.delete(name); },
    readContext: () => context,
    readDraft: () => draft,
    writeDraft: (next) => { writes.push(next); draft = { ...draft, value: next.value, version: draft.version + 1 }; },
    restoreSelection: (saved) => selections.push(saved),
    onState: (state) => states.push(state.state), onError: (error) => errors.push(error),
    onLevel: (level) => levels.push(level), id: () => `recording-${++sequence}`,
    schedule: (callback) => { pending.set(++timerId, callback); return timerId; },
    unschedule: (id) => pending.delete(id),
  });
  return {
    controller, calls, states, errors, writes, levels, selections, attachments,
    draft: () => draft,
    changeDraft: (value) => { draft = { ...draft, value, version: draft.version + 1 }; },
    changeContext: () => { context = "generation-2:character-2"; },
    event: (name, payload) => events.get(name)?.({ payload }),
    poll: async () => {
      const [id, callback] = pending.entries().next().value || [];
      if (callback) { pending.delete(id); await callback(); }
    },
  };
}

test("two clicks capture then insert once at selection end, preserve draft and attachments, never send", async () => {
  const f = fixture({ asr_poll: async () => ({ state: "succeeded", text: "voice" }) });
  await f.controller.connect();
  await f.controller.start();
  assert.equal(f.controller.state(), "recording");
  assert.equal(f.writes.length, 0);
  await f.controller.stop();
  assert.equal(f.controller.state(), "recognizing");
  await f.poll(); await f.poll();
  assert.equal(f.controller.state(), "idle");
  assert.deepEqual(f.writes, [{ value: "hello voice world", caret: 11 }]);
  assert.deepEqual(f.attachments, ["screen-test-attachment"]);
  assert.equal(f.calls.some(([name]) => name.includes("chat") || name.includes("send")), false);
  f.controller.dispose();
});

test("cancellation during prepare releases a late opened task and restores selection", async () => {
  const ready = deferred();
  const f = fixture({ asr_prepare: () => ready.promise });
  const starting = f.controller.start();
  assert.equal(f.controller.state(), "preparing");
  await f.controller.cancel();
  ready.resolve({ state: "ready", recordingId: "recording-1" });
  await starting;
  assert.equal(f.calls.some(([name]) => name === "asr_capture_start"), false);
  assert.equal(f.controller.active(), false);
  assert.equal(f.selections[0].selectionStart, 0);
  assert.equal(f.selections[0].selectionEnd, 5);
  assert.equal(f.calls.filter(([name]) => name === "asr_cancel").length, 2);
});

test("recognition cancellation invalidates an in-flight success without overwriting new draft", async () => {
  const result = deferred();
  const f = fixture({ asr_poll: () => result.promise });
  await f.controller.start(); await f.controller.stop();
  const polling = f.poll();
  await f.controller.cancel();
  f.changeDraft("new draft");
  result.resolve({ state: "succeeded", text: "late" });
  await polling;
  assert.equal(f.writes.length, 0);
  assert.equal(f.draft().value, "new draft");
});

test("same context external draft edits append the full transcript without restoring old text", async () => {
  const f = fixture({ asr_poll: async () => ({ state: "succeeded", text: "recognized" }) });
  await f.controller.start(); await f.controller.stop();
  f.changeDraft("updated elsewhere");
  await f.poll();
  assert.equal(f.draft().value, "updated elsewhere recognized");
});

test("generation or character change rejects a pending transcript", async () => {
  const result = deferred();
  const f = fixture({ asr_poll: () => result.promise });
  await f.controller.start(); await f.controller.stop();
  const polling = f.poll();
  f.changeContext();
  result.resolve({ state: "succeeded", text: "obsolete" });
  await polling;
  assert.equal(f.writes.length, 0);
  f.controller.dispose();
});

test("host level summaries require recording identity and increasing sequence; stop freezes feedback", async () => {
  const f = fixture();
  await f.controller.connect(); await f.controller.start();
  const level = (recordingId, sequence, level) => f.event("sakura://asr-level", { recordingId, sequence, level });
  level("old", 1, 1); level("recording-1", 1, 0); level("recording-1", 1, 1);
  level("recording-1", 2, 0.6); level("recording-1", 3, NaN);
  await f.controller.stop(); level("recording-1", 4, 0.9);
  assert.deepEqual(f.levels, [0, 0.6]);
  f.controller.dispose();
});

test("host duration cutoff enters recognizing without sending; device interruption restores draft", async () => {
  const f = fixture();
  await f.controller.connect(); await f.controller.start();
  f.event("sakura://asr-capture", { recordingId: "recording-1", state: "recognizing" });
  assert.equal(f.controller.state(), "recognizing");
  f.event("sakura://asr-capture", { recordingId: "recording-1", state: "failed", errorCode: "ASR_MICROPHONE_DISCONNECTED" });
  assert.equal(f.controller.active(), false);
  assert.equal(f.draft().value, "hello world");
  assert.equal(f.writes.length, 0);
  assert.equal(f.errors.length, 1);
  f.controller.dispose();
});

test("polling a capture failure before its native event still reports the error exactly once", async () => {
  const f = fixture({ asr_poll: async () => ({ state: "failed", errorCode: "ASR_MICROPHONE_DISCONNECTED" }) });
  await f.controller.connect(); await f.controller.start();
  await f.poll();
  f.event("sakura://asr-capture", { recordingId: "recording-1", state: "failed", errorCode: "ASR_MICROPHONE_DISCONNECTED" });
  assert.equal(f.controller.state(), "idle");
  assert.equal(f.errors.length, 1);
  assert.match(f.errors[0], /ASR_MICROPHONE_DISCONNECTED/);
  assert.equal(f.draft().value, "hello world");
  assert.equal(f.writes.length, 0);
  f.controller.dispose();
});

test("stop and auto-stop cannot start a second poll while the single-delivery result is in flight", async () => {
  const result = deferred();
  const f = fixture({ asr_poll: () => result.promise });
  await f.controller.connect(); await f.controller.start();
  const polling = f.poll();
  await f.controller.stop();
  f.event("sakura://asr-capture", { recordingId: "recording-1", state: "recognizing" });
  await f.poll();
  assert.equal(f.calls.filter(([name]) => name === "asr_poll").length, 1);
  result.resolve({ state: "succeeded", text: "one result" });
  await polling; await f.poll();
  assert.equal(f.writes.length, 1);
  assert.equal(f.calls.filter(([name]) => name === "asr_poll").length, 1);
});
