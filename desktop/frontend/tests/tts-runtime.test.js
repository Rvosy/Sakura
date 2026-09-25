import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { createChatPresentationReducer } from "../chat/chat-presentation.js";
import { createWaitingIndicator } from "../chat/waiting-indicator.js";

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

const appSource = readFileSync(new URL("../app.js", import.meta.url), "utf8");
const typewriterBinding = appSource.slice(
  appSource.indexOf("const typewriter = createTypewriter({"),
  appSource.indexOf("\nconst waitingIndicator =", appSource.indexOf("const typewriter = createTypewriter({")),
);

function sequence(h, segments, prepareVisual = async control => control) {
  const timers = [];
  const text = [];
  const portraits = [];
  const controls = [];
  const opened = segments.map(() => deferred());
  const requested = segments.map(() => deferred());
  const pauses = segments.map(() => deferred());
  const finished = deferred();
  const presentation = createChatPresentationReducer({ initialMessage: "你好" });
  const identity = { generationId: "generation-1", generationNumber: 1, operationId: "reply" };
  presentation.reduce({ type: "lifecycle", status: "ready", revision: 1, ...identity });
  presentation.reduce({ type: "chat.started", ...identity });
  const waiting = createWaitingIndicator({
    setTimer: () => 1, clearTimer() {}, onFrame: frame => presentation.setWaitingText(frame),
  });
  waiting.start();
  presentation.reduce({ type: "chat.completed", reply: { segments }, ...identity });
  segments = presentation.current().segments;
  // Execute the production app binding, with fake native audio and deterministic timers.
  const writer = runInNewContext(`${typewriterBinding}\ntypewriter;`, {
    chatTiming: { subtitleTypingIntervalMs: 28, replySegmentPauseMs: 160 },
    subtitleLanguage: "zh", bubbleScroll: { beginReply() {} },
    presentation, ttsController: h.controller, waitingIndicator: waiting,
    rendererHost: { prepare: prepareVisual, play(control, _operation, _index, segment) { portraits.push(segment.portrait); controls.push(control); } },
    render() {},
    createTypewriter(options) {
      return createTypewriter({
        ...options,
        setTimer(callback, delay) {
          timers.push(callback);
          if (delay === 160) pauses[portraits.length - 1].resolve();
          return callback;
        },
        clearTimer(callback) {
          const index = timers.indexOf(callback);
          if (index >= 0) timers.splice(index, 1);
        },
        onSegment(segment, index) {
          requested[index].resolve();
          return options.onSegment(segment, index);
        },
        onText(value, update) {
          options.onText(value, update);
          text.push(value);
          if (update.reason === "segment") opened[portraits.length - 1]?.resolve();
        },
        onComplete(result) { options.onComplete(result); finished.resolve(); },
      });
    },
  });
  h.controller.beginReply("reply", segments);
  writer.start(segments);
  return {
    writer, text, portraits, controls, opened, requested, pauses, finished, waiting, presentation,
    drain() { while (timers.length) timers.shift()(); },
  };
}

test("slow visual preparation keeps segment presentation synchronized after chat completion", async () => {
  const visual = deferred();
  const entered = deferred();
  const h = await harness(name => name === "tts_prepare_segment" ? descriptor : undefined);
  const s = sequence(h, [{ text: "ready text", portrait: "smile" }], () => {
    entered.resolve();
    return visual.promise;
  });
  await entered.promise;
  assert.equal(s.presentation.current().segments[0].text, "ready text");
  assert.equal(h.calls.filter(([name]) => name === "tts_play_prepared").length, 0);
  assert.deepEqual(s.text, []);
  assert.deepEqual(s.portraits, []);
  visual.resolve(null);
  await h.waitFor("tts_play_prepared");
  h.emit("tts-1-0", "started");
  await s.opened[0].promise;
  assert.deepEqual(s.portraits, ["smile"]);
  assert.deepEqual(s.text, [""]);
  h.controller.dispose();
});

test("cancelling a segment releases pending visual preparation without playing late audio", async () => {
  const visual = deferred();
  const entered = deferred();
  const h = await harness(name => name === "tts_prepare_segment" ? descriptor : undefined);
  const segment = { text: "cancelled" };
  h.controller.beginReply("reply", [segment]);
  let started = 0;
  const waiting = h.controller.beforeSegment(segment, 0, {
    prepareVisual: () => { entered.resolve(); return visual.promise; },
    onStarted: () => started++,
  });
  await entered.promise;
  h.controller.cancel();
  await waiting;
  assert.equal(started, 0);
  visual.resolve(null);
  await visual.promise;
  assert.equal(h.calls.filter(([name]) => name === "tts_play_prepared").length, 0);
  h.controller.dispose();
});

test("capture interrupts the current wait while later silent segments still prepare their visuals", async () => {
  const visuals = [deferred(), deferred()];
  const entered = [deferred(), deferred()];
  const h = await harness(name => name === "tts_prepare_segment" ? descriptor : undefined);
  let index = 0;
  const s = sequence(h, [{ text: "one", portrait: "smile" }, { text: "two", portrait: "calm" }], () => {
    const current = index++;
    entered[current].resolve();
    return visuals[current].promise;
  });
  try {
    await entered[0].promise;
    h.controller.setInputCaptureActive(true);
    await s.opened[0].promise;
    s.drain();
    await s.pauses[0].promise;
    h.controller.setInputCaptureActive(false);
    s.drain();
    await entered[1].promise;
    await new Promise(setImmediate);
    assert.deepEqual(s.portraits, ["smile"]);
    assert.equal(s.text.at(-1), "one");
    const control = { state: { expression: "calm" } };
    visuals[1].resolve(control);
    await s.opened[1].promise;
    assert.deepEqual(s.portraits, ["smile", "calm"]);
    assert.equal(s.controls[1], control);
    visuals[0].resolve({ state: { expression: "late" } });
    await visuals[0].promise;
    assert.equal(h.calls.filter(([name]) => name === "tts_play_prepared").length, 0);
    assert.equal(s.controls.length, 2);
  } finally {
    s.writer.dispose();
    h.controller.dispose();
  }
});

for (const audioFirst of [false, true]) {
  test(`each segment waits for synthesis and playback start, then both text and audio completion (${audioFirst})`, async () => {
    const synthesis = [deferred(), deferred()];
    const h = await harness((name, args) => name === "tts_prepare_segment"
      ? synthesis[args.payload.segmentIndex].promise : undefined);
    const s = sequence(h, [{ text: "one", portrait: "smile" }, { text: "two", portrait: "calm" }]);
    s.drain();
    assert.deepEqual(s.text, []);
    assert.deepEqual(s.portraits, []);
    assert.equal(s.waiting.active(), true);
    assert.equal(s.presentation.current().bubbleText, ".");
    assert.equal(s.writer.skip(), false);
    synthesis[0].resolve(descriptor);
    await h.waitFor("tts_play_prepared");
    assert.deepEqual(s.portraits, []);
    assert.deepEqual(s.text, []);
    h.emit("tts-1-0", "started");
    await s.opened[0].promise;
    assert.equal(s.waiting.active(), false);
    assert.deepEqual(s.portraits, ["smile"]);
    assert.deepEqual(s.text, [""]);
    if (audioFirst) {
      h.emit("tts-1-0", "finished");
      assert.deepEqual(s.portraits, ["smile"]);
      assert.equal(s.writer.isActive(), true);
      s.drain();
    } else {
      s.drain();
      assert.equal(s.text.at(-1), "one");
      assert.deepEqual(s.portraits, ["smile"]);
      assert.equal(s.writer.isActive(), true);
      h.emit("tts-1-0", "finished");
    }
    await s.pauses[0].promise;
    s.drain();
    await s.requested[1].promise;
    assert.equal(s.writer.skip(), false);
    assert.equal(s.text.at(-1), "one");
    assert.deepEqual(s.portraits, ["smile"]);
    synthesis[1].resolve(descriptor);
    await h.waitFor("tts_play_prepared", 2);
    assert.equal(s.text.at(-1), "one");
    h.emit("tts-1-1", "started");
    await s.opened[1].promise;
    assert.deepEqual(s.portraits, ["smile", "calm"]);
    s.drain();
    assert.equal(s.text.at(-1), "two");
    assert.equal(s.writer.isActive(), true);
    h.emit("tts-1-1", "finished");
    await s.finished.promise;
    assert.equal(s.writer.isActive(), false);
    s.writer.dispose();
    h.controller.dispose();
  });
}

test("subtitle language changes reuse the pending gate and never replay a segment", async () => {
  const pending = deferred();
  const h = await harness(name => name === "tts_prepare_segment" ? pending.promise : undefined);
  const s = sequence(h, [{ text: "かな", translation: "中文", portrait: "smile" }]);
  s.writer.updateLanguage("ja");
  assert.deepEqual(s.text, []);
  pending.resolve(descriptor);
  await h.waitFor("tts_play_prepared");
  h.emit("tts-1-0", "started");
  await s.opened[0].promise;
  s.drain();
  assert.equal(s.text.at(-1), "かな");
  s.writer.updateLanguage("bilingual");
  assert.equal(s.writer.skip(), true);
  assert.equal(s.writer.skip(), true);
  assert.equal(s.text.at(-1), "中文\nかな");
  s.writer.updateLanguage("bilingual_ja");
  assert.equal(s.writer.skip(), true);
  assert.equal(s.text.at(-1), "かな\n中文");
  assert.equal(h.calls.filter(([name]) => name === "tts_play_prepared").length, 1);
  h.emit("tts-1-0", "finished");
  await s.finished.promise;
  assert.deepEqual(s.portraits, ["smile"]);
  s.writer.dispose();
  h.controller.dispose();
});

for (const failure of ["synthesis", "descriptor", "command", "event", "disabled", "suppressed"]) {
  test(`${failure} releases the visual gate and allows later segments`, async () => {
    const h = await harness((name, args) => {
      if (name === "tts_prepare_segment" && args.payload.segmentIndex === 0) {
        if (failure === "synthesis") throw new Error("TTS_SERVICE_UNAVAILABLE");
        if (failure === "disabled") throw "TTS_DISABLED|角色语音已关闭";
        if (failure === "descriptor") return null;
      }
      if (name === "tts_play_prepared" && failure === "command") throw new Error("AUDIO_PLAYBACK_FAILED");
      return descriptor;
    });
    const segments = [{ text: "one", suppressTts: failure === "suppressed" }, { text: "two", suppressTts: true }];
    h.controller.beginReply("errors", segments);
    const shown = [];
    const gate = h.controller.beforeSegment(segments[0], 0, { onStarted: () => shown.push(0) });
    if (failure === "event") {
      await h.waitFor("tts_play_prepared");
      h.emit("tts-1-0", "failed");
    }
    await gate;
    await h.controller.afterSegment(0);
    await h.controller.beforeSegment(segments[1], 1, { onStarted: () => shown.push(1) });
    assert.deepEqual(shown, [0, 1]);
    assert.equal(h.diagnostics.length, ["disabled", "suppressed"].includes(failure) ? 0 : 1);
    h.controller.dispose();
  });
}

test("disabled TTS skips the rest of the reply without repeated synthesis", async () => {
  const h = await harness(() => { throw new Error("TTS_DISABLED"); });
  const segments = [{ text: "one" }, { text: "two" }];
  h.controller.beginReply("disabled", segments);
  for (const [index, segment] of segments.entries()) await h.controller.beforeSegment(segment, index);
  assert.deepEqual(h.calls.map(([name]) => name), ["tts_prepare_segment"]);
  assert.deepEqual(h.diagnostics, []);
  h.controller.dispose();
});

for (const stage of ["synthesis", "playback"]) {
  test(`voice capture releases ${stage} wait and never replays this reply`, async () => {
    const pending = deferred();
    const h = await harness(name => name === "tts_prepare_segment" ? pending.promise : undefined);
    const segments = [{ text: "one" }, { text: "two" }];
    const shown = [];
    h.controller.beginReply("capture", segments);
    const gate = h.controller.beforeSegment(segments[0], 0, { onStarted: () => shown.push(0) });
    if (stage === "playback") {
      pending.resolve(descriptor);
      await h.waitFor("tts_play_prepared");
    }
    h.controller.setInputCaptureActive(true);
    await gate;
    await h.controller.afterSegment(0);
    h.controller.setInputCaptureActive(false);
    await h.controller.beforeSegment(segments[1], 1, { onStarted: () => shown.push(1) });
    pending.resolve(descriptor);
    await pending.promise;
    assert.deepEqual(shown, [0, 1]);
    assert.equal(h.calls.filter(([name]) => name === "tts_prepare_segment").length, 1);
    assert.equal(h.calls.filter(([name]) => name === "tts_play_prepared").length, stage === "playback" ? 1 : 0);
    h.controller.dispose();
  });
}

test("a reply received during capture stays silent after capture ends", async () => {
  const h = await harness();
  const segments = [{ text: "one" }];
  h.controller.setInputCaptureActive(true);
  h.controller.beginReply("capture", segments);
  h.controller.setInputCaptureActive(false);
  let shown = false;
  await h.controller.beforeSegment(segments[0], 0, { onStarted: () => { shown = true; } });
  assert.equal(shown, true);
  assert.equal(h.calls.filter(([name]) => name === "tts_prepare_segment").length, 0);
  h.controller.dispose();
});

for (const action of ["cancel", "dispose", "replace"]) {
  test(`${action} releases pending synthesis and ignores its late result`, async () => {
    const pending = deferred();
    const h = await harness(name => name === "tts_prepare_segment" ? pending.promise : undefined);
    const segments = [{ text: "old" }];
    h.controller.beginReply("old", segments);
    let shown = false;
    const gate = h.controller.beforeSegment(segments[0], 0, { onStarted: () => { shown = true; } });
    if (action === "replace") h.controller.beginReply("new", []);
    else h.controller[action]();
    await gate;
    pending.resolve(descriptor);
    await pending.promise;
    assert.equal(shown, false);
    assert.equal(h.calls.filter(([name]) => name === "tts_play_prepared").length, 0);
    assert.ok(h.calls.some(([name, args]) => name === "tts_cancel_synthesis" && args.payload.operationId === "old"));
    h.controller.dispose();
  });
}

test("replacing playback ignores late events and native rejection from the old reply", async () => {
  const oldCommand = deferred();
  const h = await harness((name, args) => name === "tts_play_prepared" && args.payload.playbackId === "tts-1-0"
    ? oldCommand.promise : descriptor);
  const old = [{ text: "old" }];
  const next = [{ text: "new" }];
  const shown = [];
  h.controller.beginReply("old", old);
  const oldGate = h.controller.beforeSegment(old[0], 0, { onStarted: () => shown.push("old") });
  await h.waitFor("tts_play_prepared");
  h.controller.beginReply("new", next);
  const newGate = h.controller.beforeSegment(next[0], 0, { onStarted: () => shown.push("new") });
  await h.waitFor("tts_play_prepared", 2);
  await oldGate;
  h.emit("tts-1-0", "started");
  h.emit("tts-1-0", "finished");
  oldCommand.reject(new Error("AUDIO_PLAYBACK_FAILED"));
  await oldCommand.promise.catch(() => {});
  assert.deepEqual(shown, []);
  assert.deepEqual(h.diagnostics, []);
  h.emit("tts-2-0", "started");
  await newGate;
  assert.deepEqual(shown, ["new"]);
  h.controller.dispose();
});
