import assert from "node:assert/strict";
import test from "node:test";

import { createTtsController } from "../audio/tts-controller.js";
import { createTypewriter } from "../pet/typewriter.js";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, reject, resolve };
}

const descriptor = {
  opaqueId: "0123456789abcdef0123456789abcdef",
  recordingId: "recording-1",
  mediaType: "audio/wav",
  byteLength: 128,
  expiresAt: "2099-01-01T00:00:00Z",
};

async function harness(implementation = () => descriptor) {
  const calls = [];
  const diagnostics = [];
  const waiters = [];
  let listener;
  const controller = createTtsController({
    listen: async (_name, callback) => { listener = callback; return () => {}; },
    invoke(name, args) {
      calls.push([name, args]);
      for (const waiter of waiters) {
        const matching = calls.filter(([command]) => command === waiter.name);
        if (matching.length >= waiter.count) waiter.resolve(matching.at(-1));
      }
      return implementation(name, args);
    },
    onDiagnostic: (code) => diagnostics.push(code),
  });
  await controller.start();
  return {
    controller, calls, diagnostics,
    emit: (playbackId, state) => listener({ payload: { playbackId, state } }),
    waitFor(name, count = 1) {
      const matching = calls.filter(([command]) => command === name);
      if (matching.length >= count) return Promise.resolve(matching.at(-1));
      return new Promise((resolve) => waiters.push({ name, count, resolve }));
    },
  };
}

test("subtitles complete while synthesis is pending and audio advances only on its own terminal", async () => {
  const synthesis = deferred();
  const h = await harness((name) => name === "tts_prepare_segment" ? synthesis.promise : undefined);
  const segments = [{ text: "one" }, { text: "two" }];
  const timers = [];
  const text = [];
  const visualSegments = [];
  let complete = false;
  const typewriter = createTypewriter({
    setTimer: (callback) => { timers.push(callback); return callback; },
    clearTimer: () => {},
    onSegment: (_segment, index) => {
      visualSegments.push(index);
      return synthesis.promise;
    },
    onText: (value) => text.push(value),
    onComplete: () => { complete = true; },
  });

  assert.equal(h.controller.beginReply("operation-1", segments), undefined);
  typewriter.start(segments);
  while (timers.length) timers.shift()();

  assert.equal(complete, true);
  assert.deepEqual(visualSegments, [0, 1]);
  assert.ok(text.includes("one") && text.includes("two"));
  assert.equal(h.calls.filter(([name]) => name === "tts_prepare_segment").length, 1);
  assert.equal(h.calls.filter(([name]) => name === "tts_play_prepared").length, 0);

  synthesis.resolve(descriptor);
  await h.waitFor("tts_play_prepared");
  h.emit("tts-1-0", "started");
  assert.equal(h.calls.filter(([name]) => name === "tts_prepare_segment").length, 1);
  h.emit("tts-1-0", "finished");
  const [, next] = await h.waitFor("tts_play_prepared", 2);
  assert.equal(next.payload.playbackId, "tts-1-1");
  h.controller.dispose();
  typewriter.dispose();
});

test("voice capture stops queued TTS and does not replay replies received during capture", async () => {
  const pending = deferred();
  const h = await harness((name) => name === "tts_prepare_segment" ? pending.promise : undefined);
  h.controller.beginReply("before-capture", [{ text: "old" }]);
  h.controller.setInputCaptureActive(true);
  h.controller.beginReply("during-capture", [{ text: "during capture" }]);
  h.controller.setInputCaptureActive(false);
  pending.resolve(descriptor);
  await pending.promise;
  assert.equal(h.calls.filter(([name]) => name === "tts_prepare_segment").length, 1);
  assert.equal(h.calls.filter(([name]) => name === "tts_play_prepared").length, 0);
  assert.ok(h.calls.some(([name]) => name === "tts_stop_playback"));
  h.controller.dispose();
});

test("synthesis and playback failures stay in the audio queue and allow following segments", async () => {
  const h = await harness((name, args) => {
    if (name === "tts_prepare_segment" && args.payload.segmentIndex === 0) {
      throw new Error("TTS_SERVICE_UNAVAILABLE");
    }
    if (name === "tts_play_prepared" && args.payload.playbackId === "tts-1-1") {
      throw new Error("AUDIO_PLAYBACK_FAILED");
    }
    return descriptor;
  });
  h.controller.beginReply("operation-errors", [{ text: "one" }, { text: "two" }, { text: "three" }]);
  const [, next] = await h.waitFor("tts_play_prepared", 2);
  assert.equal(next.payload.playbackId, "tts-1-2");
  assert.deepEqual(h.diagnostics, ["TTS_SERVICE_UNAVAILABLE", "AUDIO_PLAYBACK_FAILED"]);
  h.controller.dispose();
});

test("disabled TTS quietly ends the reply audio queue", async () => {
  const h = await harness((name) => {
    if (name === "tts_prepare_segment") throw "TTS_DISABLED|角色语音已关闭";
  });
  h.controller.beginReply("disabled", [{ text: "one" }, { text: "two" }]);
  assert.deepEqual(h.calls.map(([name]) => name), ["tts_prepare_segment"]);
  assert.deepEqual(h.diagnostics, []);
  h.controller.dispose();
});

test("suppressed segments never request synthesis", async () => {
  const h = await harness();
  h.controller.beginReply("operation-silent", [{ text: "silent", suppressTts: true }]);
  assert.deepEqual(h.calls, []);
  h.controller.dispose();
});

test("cancellation stops pending synthesis and rejects its late result", async () => {
  const pending = deferred();
  const h = await harness((name) => name === "tts_prepare_segment" ? pending.promise : undefined);
  h.controller.beginReply("operation-cancel", [{ text: "cancel" }]);
  h.controller.cancel();
  assert.deepEqual(h.calls.slice(1), [
    ["tts_cancel_synthesis", { payload: { operationId: "operation-cancel" } }],
    ["tts_stop_playback", undefined],
  ]);
  pending.reject(new Error("TTS_SYNTHESIS_CANCELLED"));
  await pending.promise.catch(() => {});
  assert.deepEqual(h.diagnostics, []);
  assert.equal(h.calls.filter(([name]) => name === "tts_play_prepared").length, 0);
  h.controller.dispose();
});

test("replacing the reply stops old playback and ignores its late terminal", async () => {
  const h = await harness();
  h.controller.beginReply("old-character", [{ text: "old one" }, { text: "old two" }]);
  await h.waitFor("tts_play_prepared");
  h.controller.beginReply("new-character", [{ text: "new one" }, { text: "new two" }]);
  await h.waitFor("tts_play_prepared", 2);
  h.emit("tts-1-0", "finished");
  assert.equal(h.calls.filter(([name]) => name === "tts_prepare_segment").length, 2);
  h.emit("tts-2-0", "finished");
  const [, next] = await h.waitFor("tts_prepare_segment", 3);
  assert.deepEqual(next.payload, { operationId: "new-character", segmentIndex: 1 });
  h.controller.dispose();
  assert.deepEqual(h.calls.filter(([name]) => name === "tts_cancel_synthesis"), [
    ["tts_cancel_synthesis", { payload: { operationId: "old-character" } }],
    ["tts_cancel_synthesis", { payload: { operationId: "new-character" } }],
  ]);
  assert.equal(h.calls.filter(([name]) => name === "tts_stop_playback").length, 2);
});
