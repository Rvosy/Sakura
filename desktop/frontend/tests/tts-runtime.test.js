import assert from "node:assert/strict";
import test from "node:test";

import { createTtsController } from "../audio/tts-controller.js";


function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}


test("WP-4-05 subtitle gate opens on playback start and waits for playback finish", async () => {
  let playbackListener;
  const calls = [];
  const controller = createTtsController({
    listen: async (_name, listener) => { playbackListener = listener; return () => {}; },
    invoke: async (name, args) => {
      calls.push([name, args]);
      if (name === "tts_prepare_segment") {
        return {
          opaqueId: "0123456789abcdef0123456789abcdef",
          recordingId: "recording-1",
          mediaType: "audio/wav",
          byteLength: 128,
          expiresAt: "2099-01-01T00:00:00Z",
        };
      }
      return undefined;
    },
  });
  await controller.start();
  const segments = [{ text: "one", suppressTts: false }, { text: "two", suppressTts: false }];
  controller.beginReply("operation-1", segments);

  const opened = deferred();
  controller.beforeSegment(segments[0], 0).then(() => opened.resolve());
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(calls.filter(([name]) => name === "tts_prepare_segment").length, 1);
  assert.equal(calls.filter(([name]) => name === "tts_play_prepared").length, 1);

  let subtitleOpened = false;
  opened.promise.then(() => { subtitleOpened = true; });
  await Promise.resolve();
  assert.equal(subtitleOpened, false);
  playbackListener({ payload: { playbackId: "tts-1-0", state: "started" } });
  await opened.promise;
  assert.equal(subtitleOpened, true);

  let segmentSettled = false;
  const wait = controller.afterSegment(0).then(() => { segmentSettled = true; });
  await Promise.resolve();
  assert.equal(segmentSettled, false);
  playbackListener({ payload: { playbackId: "tts-1-0", state: "finished" } });
  await wait;
  assert.equal(segmentSettled, true);
  assert.equal(calls.filter(([name]) => name === "tts_prepare_segment").length, 2);
  controller.dispose();
});


test("WP-4-05 synthesis and playback failures release subtitle gate", async () => {
  const diagnostics = [];
  const controller = createTtsController({
    listen: async () => () => {},
    invoke: async (name) => {
      if (name === "tts_prepare_segment") throw new Error("TTS_SERVICE_UNAVAILABLE");
    },
    onDiagnostic: (code) => diagnostics.push(code),
  });
  await controller.start();
  const segment = { text: "fallback", suppressTts: false };
  controller.beginReply("operation-2", [segment]);
  await controller.beforeSegment(segment, 0);
  await controller.afterSegment(0);
  assert.deepEqual(diagnostics, ["Error: TTS_SERVICE_UNAVAILABLE"]);
  controller.dispose();
});


test("WP-4-05 suppressed and history-only segments never request synthesis", async () => {
  const calls = [];
  const controller = createTtsController({
    listen: async () => () => {},
    invoke: async (name) => { calls.push(name); },
  });
  await controller.start();
  const segment = { text: "silent", suppressTts: true };
  controller.beginReply("operation-3", [segment]);
  await controller.beforeSegment(segment, 0);
  assert.deepEqual(calls, []);
  controller.dispose();
});
