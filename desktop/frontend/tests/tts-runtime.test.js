import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { createTtsController } from "../audio/tts-controller.js";

const APP_JS = readFileSync(new URL("../app.js", import.meta.url), "utf8");


function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, reject, resolve };
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
  const startOrder = [];
  controller.beforeSegment(segments[0], 0, {
    onStarted: () => startOrder.push("portrait-and-subtitle-boundary"),
  }).then(() => {
    startOrder.push("subtitle-gate-opened");
    opened.resolve();
  });
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
  assert.deepEqual(startOrder, ["portrait-and-subtitle-boundary", "subtitle-gate-opened"]);

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


test("segment portrait is committed only inside the shared playback-start hook", () => {
  const segmentBoundary = APP_JS.match(
    /onSegment: \(segment, index\) => \{[\s\S]*?onSegmentComplete:/,
  )?.[0] || "";

  assert.match(
    segmentBoundary,
    /ttsController\.beforeSegment\(segment, index, \{\s*onStarted: \(\) => \{\s*const result = presentation\.setTypingSegment\(segment, index\);\s*if \(result\.applied\) void render\(result\.state\);/,
  );
  assert.doesNotMatch(
    segmentBoundary.slice(0, segmentBoundary.indexOf("ttsController.beforeSegment")),
    /presentation\.setTypingSegment/,
  );
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
  let started = false;
  await controller.beforeSegment(segment, 0, { onStarted: () => { started = true; } });
  await controller.afterSegment(0);
  assert.equal(started, true);
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
  let started = false;
  await controller.beforeSegment(segment, 0, { onStarted: () => { started = true; } });
  assert.equal(started, true);
  assert.deepEqual(calls, []);
  controller.dispose();
});


test("Plugin Kernel v3 cancel stops synthesis by operation before playback", async () => {
  const pending = deferred();
  const calls = [];
  const diagnostics = [];
  const controller = createTtsController({
    listen: async () => () => {},
    invoke: (name, args) => {
      calls.push([name, args]);
      if (name === "tts_prepare_segment") return pending.promise;
      return Promise.resolve();
    },
    onDiagnostic: (code) => diagnostics.push(code),
  });
  await controller.start();
  const segment = { text: "cancel", suppressTts: false };
  controller.beginReply("operation-cancel", [segment]);

  controller.cancel();

  assert.deepEqual(calls.slice(1), [
    ["tts_cancel_synthesis", { payload: { operationId: "operation-cancel" } }],
    ["tts_stop_playback", undefined],
  ]);
  pending.reject(new Error("TTS_SYNTHESIS_CANCELLED"));
  await pending.promise.catch(() => {});
  await Promise.resolve();
  assert.deepEqual(diagnostics, []);
  controller.dispose();
});


test("Plugin Kernel v3 replacing or disposing a reply cancels its operation", async () => {
  const calls = [];
  const controller = createTtsController({
    listen: async () => () => {},
    invoke: (name, args) => {
      calls.push([name, args]);
      return Promise.resolve(null);
    },
  });
  await controller.start();
  const suppressed = [{ text: "silent", suppressTts: true }];
  controller.beginReply("operation-old", suppressed);

  controller.beginReply("operation-new", suppressed);
  controller.dispose();

  assert.deepEqual(
    calls.filter(([name]) => name === "tts_cancel_synthesis"),
    [
      ["tts_cancel_synthesis", { payload: { operationId: "operation-old" } }],
      ["tts_cancel_synthesis", { payload: { operationId: "operation-new" } }],
    ],
  );
  assert.equal(calls.at(-1)[0], "tts_stop_playback");
});
