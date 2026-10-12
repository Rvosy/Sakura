import assert from "node:assert/strict";
import test from "node:test";

import { createTtsController as createPluginTtsController } from "../../../plugins/builtin/sakura_tts_hub/frontend/playback.mjs";
import { errorText } from "../core/error-display.js";
const createTtsController = options => createPluginTtsController({ ...options, errorText });

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

async function harness(implementation = () => descriptor, { emitBegin = true } = {}) {
  const calls = [];
  const diagnostics = [];
  const waiters = [];
  const listeners = new Map();
  const states = [];
  const controller = createTtsController({
    listen: async (name, callback) => { listeners.set(name, callback); return () => listeners.delete(name); },
    invoke(name, args) {
      calls.push([name, args]);
      if (name === "tts_begin_reply" && emitBegin) {
        listeners.get("sakura://tts-operation-started")?.({ payload: args.payload });
      }
      for (const waiter of waiters) {
        const matching = calls.filter(([command]) => command === waiter.name);
        if (matching.length >= waiter.count) waiter.resolve(matching.at(-1));
      }
      return implementation(name, args);
    },
    onDiagnostic: (code) => diagnostics.push(code),
    onPlaybackState: state => states.push(state),
  });
  await controller.start();
  return {
    controller, calls, diagnostics, states,
    emit: (playbackId, state) => listeners.get("sakura://tts-playback-event")({ payload: { playbackId, state } }),
    operationStarted: operationId => listeners.get("sakura://tts-operation-started")({ payload: { operationId } }),
    waitFor(name, count = 1) {
      const matching = calls.filter(([command]) => command === name);
      if (matching.length >= count) return Promise.resolve(matching.at(-1));
      return new Promise((resolve) => waiters.push({ name, count, resolve }));
    },
  };
}

test("all segments synthesize serially while the first segment is still playing", async () => {
  const synthesis = [deferred(), deferred(), deferred()];
  const h = await harness((name, args) => name === "tts_prepare_segment"
    ? synthesis[args.payload.segmentIndex].promise : undefined);
  const segments = [{ text: "one" }, { text: "two" }, { text: "three" }];
  h.controller.beginReply("reply", segments);
  const first = h.controller.beforeSegment(segments[0], 0);
  await h.waitFor("tts_prepare_segment");
  await new Promise(setImmediate);
  assert.equal(h.calls.filter(([name]) => name === "tts_prepare_segment").length, 1);
  synthesis[0].resolve(descriptor);
  await h.waitFor("tts_prepare_segment", 2);
  await h.waitFor("tts_play_prepared");
  h.emit("tts-1-0", "started");
  await first;
  await new Promise(setImmediate);
  assert.equal(h.calls.filter(([name]) => name === "tts_prepare_segment").length, 2);
  synthesis[1].resolve(descriptor);
  await h.waitFor("tts_prepare_segment", 3);
  synthesis[2].resolve(descriptor);
  await new Promise(setImmediate);
  assert.deepEqual(h.calls.filter(([name]) => name === "tts_prepare_segment")
    .map(([, args]) => args.payload.segmentIndex), [0, 1, 2]);
  assert.equal(h.calls.filter(([name]) => name === "tts_play_prepared").length, 1);
  assert.equal(h.states.at(-1).state, "playing");
  h.controller.dispose();
});

test("serial synthesis skips suppressed segments and continues after a failed segment", async () => {
  const h = await harness((name, args) => {
    if (name === "tts_prepare_segment" && args.payload.segmentIndex === 1) throw new Error("TTS_SERVICE_UNAVAILABLE");
    return descriptor;
  });
  h.controller.beginReply("reply", [{ suppressTts: true }, { text: "failed" }, { text: "next" }]);
  await h.waitFor("tts_prepare_segment", 2);
  assert.deepEqual(h.calls.filter(([name]) => name === "tts_prepare_segment")
    .map(([, args]) => args.payload.segmentIndex), [1, 2]);
  assert.equal(h.diagnostics.length, 1);
  assert.match(h.diagnostics[0], /TTS_SERVICE_UNAVAILABLE/);
  h.controller.dispose();
});

for (const action of ["stop", "cancel", "dispose", "replace", "capture", "another-window"]) {
  test(`${action} prevents remaining queued segments from synthesizing after a late result`, async () => {
    const pending = deferred();
    const h = await harness(name => name === "tts_prepare_segment" ? pending.promise : undefined);
    h.controller.beginReply("old", [{ text: "one" }, { text: "two" }, { text: "three" }]);
    await h.waitFor("tts_prepare_segment");
    if (action === "replace") h.controller.beginReply("new", []);
    else if (action === "capture") h.controller.setInputCaptureActive(true);
    else if (action === "another-window") h.operationStarted("foreign");
    else h.controller[action]();
    pending.resolve(descriptor);
    await new Promise(setImmediate);
    assert.equal(h.calls.filter(([name]) => name === "tts_prepare_segment").length, 1);
    assert.equal(h.calls.some(([name]) => name === "tts_play_prepared"), false);
    h.controller.dispose();
  });
}

test("reply ownership begins before synthesis even when the first segment is suppressed", async () => {
  const begun = deferred();
  const h = await harness(name => name === "tts_begin_reply" ? begun.promise : descriptor);
  const segments = [{ text: "silent", suppressTts: true }, { text: "spoken", segmentIndex: 1 }];
  h.controller.beginReply("automatic", segments);
  await h.controller.beforeSegment(segments[0], 0);
  const waiting = h.controller.beforeSegment(segments[1], 1);
  assert.deepEqual(h.calls.map(([name]) => name), ["tts_begin_reply"]);
  begun.resolve();
  const [, { payload }] = await h.waitFor("tts_prepare_segment");
  assert.equal(payload.segmentIndex, 1);
  const [, play] = await h.waitFor("tts_play_prepared");
  h.emit(play.payload.playbackId, "started");
  await waiting;
  h.controller.dispose();
});

test("cancelling while reply ownership begins prevents synthesis after its late acknowledgement", async () => {
  const begun = deferred();
  const h = await harness(name => name === "tts_begin_reply" ? begun.promise : descriptor);
  const waiting = h.controller.playHistorySegment({ historyEntryId: "saved", segmentIndex: 0 });
  h.controller.stop();
  await waiting;
  begun.resolve();
  await begun.promise;
  assert.equal(h.calls.some(([name]) => name.startsWith("tts_prepare")), false);
  h.controller.dispose();
});

test("a delayed previous ownership event cannot cancel the newer pending reply", async () => {
  const h = await harness(undefined, { emitBegin: false });
  const first = h.controller.playHistorySegment({ historyEntryId: "saved", segmentIndex: 0 });
  const second = h.controller.playHistorySegment({ historyEntryId: "saved", segmentIndex: 1 });
  const begins = h.calls.filter(([name]) => name === "tts_begin_reply");
  h.operationStarted(begins[0][1].payload.operationId);
  h.operationStarted(begins[1][1].payload.operationId);
  const [, play] = await h.waitFor("tts_play_prepared");
  h.emit(play.payload.playbackId, "started");
  await Promise.all([first, second]);
  const prepared = h.calls.filter(([name]) => name === "tts_prepare_history_segment");
  assert.equal(prepared.length, 1);
  assert.equal(prepared[0][1].payload.segmentIndex, 1);
  assert.equal(h.states.at(-1).state, "playing");
  h.controller.dispose();
});

test("history playback prepares the stable segment and uses a fresh cancellable operation each time", async () => {
  const h = await harness();
  const segment = { historyEntryId: "saved-assistant", segmentIndex: 3 };
  const operations = [];
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    const waiting = h.controller.playHistorySegment(segment);
    const [, { payload }] = await h.waitFor("tts_prepare_history_segment", attempt);
    assert.equal(payload.historyEntryId, segment.historyEntryId);
    assert.equal(payload.segmentIndex, 3);
    operations.push(payload.operationId);
    h.operationStarted(payload.operationId);
    const [, play] = await h.waitFor("tts_play_prepared", attempt);
    assert.equal(play.payload.opaqueId, descriptor.opaqueId);
    h.emit(play.payload.playbackId, "started");
    await waiting;
    assert.deepEqual(h.states.at(-1), { state: "playing", ...segment });
    h.emit(play.payload.playbackId, "finished");
    assert.equal(h.states.at(-1).state, "idle");
  }
  assert.notEqual(operations[0], operations[1]);
  assert.equal(h.calls.some(([name]) => name === "tts_prepare_segment"), false);
  h.controller.dispose();
});

for (const interruption of ["stop", "cancel", "capture", "another-window"]) {
  test(`history ${interruption} cancels pending preparation and cannot play its late descriptor`, async () => {
    const prepared = deferred();
    const h = await harness(name => name === "tts_prepare_history_segment" ? prepared.promise : undefined);
    const waiting = h.controller.playHistorySegment({ historyEntryId: "saved-assistant", segmentIndex: 2 });
    const [, { payload }] = await h.waitFor("tts_prepare_history_segment");
    if (interruption === "capture") h.controller.setInputCaptureActive(true);
    else if (interruption === "another-window") h.operationStarted("foreign-operation");
    else h.controller[interruption]();
    await waiting;
    assert.ok(h.calls.some(([name, args]) => name === "tts_cancel_synthesis" && args.payload.operationId === payload.operationId));
    if (interruption !== "another-window") {
      assert.ok(h.calls.some(([name, args]) => name === "tts_stop_playback" && args.payload.operationId === payload.operationId));
    }
    prepared.resolve(descriptor);
    await prepared.promise;
    assert.equal(h.calls.some(([name]) => name === "tts_play_prepared"), false);
    assert.equal(h.states.at(-1).state, "idle");
    h.controller.dispose();
    if (interruption === "another-window") {
      assert.equal(h.calls.some(([name]) => name === "tts_stop_playback"), false);
    }
  });
}

test("another window taking audio keeps later automatic segments silent while subtitles proceed", async () => {
  const h = await harness();
  const segments = [{ text: "first" }, { text: "second" }];
  h.controller.beginReply("automatic", segments);
  await h.waitFor("tts_prepare_segment");
  h.operationStarted("history-other-window");
  const shown = [];
  for (const [index, segment] of segments.entries()) {
    await h.controller.beforeSegment(segment, index, { onStarted: () => shown.push(index) });
  }
  assert.deepEqual(shown, [0, 1]);
  assert.equal(h.calls.filter(([name]) => name === "tts_prepare_segment").length, 1);
  assert.equal(h.calls.some(([name]) => name === "tts_play_prepared"), false);
  h.controller.dispose();
  assert.equal(h.calls.some(([name]) => name === "tts_stop_playback"), false);
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

test("afterSegment waits for playback and still reports voiced after it finishes", async () => {
  const h = await harness();
  const segment = { text: "one" };
  h.controller.beginReply("reply", [segment]);
  const gate = h.controller.beforeSegment(segment, 0);
  await h.waitFor("tts_play_prepared");
  h.emit("tts-1-0", "started");
  await gate;
  let settled = false;
  const wait = h.controller.afterSegment(0).then((voiced) => { settled = voiced; });
  await Promise.resolve();
  assert.equal(settled, false);
  h.emit("tts-1-0", "finished");
  await wait;
  assert.equal(settled, true);
  assert.equal(await h.controller.afterSegment(0), true);
  h.controller.dispose();
});

test("synthesis failure does not count the segment as voiced", async () => {
  const h = await harness(() => { throw new Error("TTS_SERVICE_UNAVAILABLE"); });
  const segment = { text: "one" };
  h.controller.beginReply("reply", [segment]);
  await h.controller.beforeSegment(segment, 0);
  assert.equal(await h.controller.afterSegment(0), false);
  h.controller.dispose();
});

for (const failure of ["synthesis", "descriptor", "command", "event", "disabled", "suppressed"]) {
  test(`${failure} releases the visual gate and allows later segments`, async () => {
    const h = await harness((name, args) => {
      if (name === "tts_prepare_segment" && args.payload.segmentIndex === 0) {
        if (failure === "synthesis") throw new Error("TTS_SERVICE_UNAVAILABLE");
        if (failure === "disabled") throw "TTS_DISABLED|角色语音已关闭";
        if (failure === "descriptor") throw new Error("AUDIO_RECORDING_INVALID");
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
  const h = await harness(name => { if (name === "tts_prepare_segment") throw new Error("TTS_DISABLED"); });
  const segments = [{ text: "one" }, { text: "two" }];
  h.controller.beginReply("disabled", segments);
  for (const [index, segment] of segments.entries()) await h.controller.beforeSegment(segment, index);
  assert.deepEqual(h.calls.map(([name]) => name), ["tts_begin_reply", "tts_prepare_segment"]);
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
    await h.waitFor("tts_prepare_segment");
    if (stage === "playback") {
      pending.resolve(descriptor);
      await h.waitFor("tts_play_prepared");
      await h.waitFor("tts_prepare_segment", 2);
    }
    h.controller.setInputCaptureActive(true);
    await gate;
    await h.controller.afterSegment(0);
    h.controller.setInputCaptureActive(false);
    await h.controller.beforeSegment(segments[1], 1, { onStarted: () => shown.push(1) });
    pending.resolve(descriptor);
    await pending.promise;
    assert.deepEqual(shown, [0, 1]);
    assert.equal(h.calls.filter(([name]) => name === "tts_prepare_segment").length, stage === "playback" ? 2 : 1);
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
