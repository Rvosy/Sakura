import assert from "node:assert/strict";
import test from "node:test";

import { createChatPresentationReducer } from "../chat/chat-presentation.js";
import { rebindCharacterPresentation } from "../pet/character-generation.js";
import { loadCurrentCharacterPresentation } from "../pet/character-presentation.js";
import { createRendererHost } from "../pet/renderer-host.js";

const initializing = generationId => ({
  supervisor: { state: "running", generationId },
  snapshot: { generationId, readiness: "initializing" },
});

test("startup waits for plugin binding, while real unavailability is reported immediately", async () => {
  const base = { schemaVersion: 2, generationId: "g", characterId: "sample", displayName: "角色", initialMessage: "你好", themeTokens: {}, visual: null, visualReasonCode: "VISUAL_NOT_BOUND" };
  const readyVisual = { bindingId: "a".repeat(32), resourceId: "sample-model", renderer: `http://sakura-character.localhost/module/67/${"a".repeat(32)}/renderer.js`, data: {}, assets: {} };
  const unavailable = [];
  const root = { replaceChildren() {}, append() {}, ownerDocument: {
    defaultView: { getComputedStyle: () => ({}) }, createElement: () => ({ style: {}, remove() {} }) } };
  const host = createRendererHost({ container: root, onUnavailable: code => unavailable.push(code), loadModule: async () => ({ mount: () => ({ applyState() {}, destroy() {} }) }) });
  // Before first-run setup completes, the app has only a pending placeholder.
  assert.equal(await host.bind(base), false);
  assert.deepEqual(unavailable, []);
  let reads = 0;
  const presentation = await loadCurrentCharacterPresentation({
    invoke: async command => command === "runtime_lifecycle_snapshot" ? initializing("g")
      : ++reads < 201 ? base : { ...base, visual: readyVisual, visualReasonCode: "READY" },
    setTimer: resolve => resolve(),
  });
  assert.equal(reads, 201);
  assert.equal(await host.bind(presentation), true);
  assert.deepEqual(unavailable, []);
  let waited = false;
  const missing = await loadCurrentCharacterPresentation({ invoke: async () => ({ ...base, visualReasonCode: "PLUGIN_DISABLED" }), setTimer: () => { waited = true; } });
  assert.equal(await host.bind(missing), false);
  assert.deepEqual(unavailable, ["PLUGIN_DISABLED"]);
  assert.equal(waited, false);
  host.destroy();
});

const ready = (generationId, generationNumber) => ({
  type: "lifecycle",
  status: "ready",
  generationId,
  generationNumber,
  revision: 1,
  canRetry: false,
  failure: null,
});

test("pending resources stop waiting for setup, failure, shutdown, or a replaced Core", async () => {
  const pending = initializing("g");
  for (const publication of [
    { ...pending, snapshot: { generationId: "g", readiness: "setup_required" } },
    { ...pending, snapshot: { generationId: "g", readiness: "failed" } },
    { ...pending, supervisor: { state: "failed", generationId: "g", failure: { code: "core_crashed", message: "Core exited" } } },
    { ...pending, supervisor: { ...pending.supervisor, appShutdown: true } },
    initializing("replacement"),
  ]) {
    const result = await loadCurrentCharacterPresentation({
      invoke: async command => {
        if (command === "runtime_lifecycle_snapshot") return publication;
        throw new Error("CHARACTER_PRESENTATION_NOT_READY");
      },
      expectedGenerationId: "g",
      setTimer: () => { assert.fail("terminal lifecycle must not keep polling"); },
    });
    assert.equal(result, null);
  }
});

test("a stale setup snapshot does not stop the new Core's resource load", async () => {
  let current = null;
  const result = await loadCurrentCharacterPresentation({
    invoke: async command => {
      if (command === "runtime_lifecycle_snapshot") return {
        supervisor: { state: "running", generationId: "new" },
        snapshot: { generationId: "old", readiness: "setup_required" },
      };
      if (current) return current;
      throw new Error("CHARACTER_PRESENTATION_NOT_READY");
    },
    expectedGenerationId: "new",
    setTimer: resolve => {
      current = { schemaVersion: 2, generationId: "new", visual: null, visualReasonCode: "PLUGIN_DISABLED" };
      resolve();
    },
  });
  assert.equal(result.generationId, "new");
});

test("cancelling a presentation load releases its timer and ignores late IPC results", async () => {
  const abort = new AbortController();
  let timerCleared = false;
  const pending = loadCurrentCharacterPresentation({
    invoke: async command => {
      if (command === "runtime_lifecycle_snapshot") return initializing("g");
      throw new Error("CHARACTER_PRESENTATION_NOT_READY");
    },
    signal: abort.signal,
    setTimer: () => { abort.abort(); return 7; },
    clearTimer: id => { timerCleared = id === 7; },
  });
  assert.equal(await pending, null);
  assert.equal(timerCleared, true);

  const nextAbort = new AbortController();
  let resolve;
  const loading = loadCurrentCharacterPresentation({
    invoke: () => new Promise(done => { resolve = done; }), signal: nextAbort.signal,
  });
  nextAbort.abort();
  assert.equal(await loading, null);
  resolve({ schemaVersion: 2, generationId: "old", visual: null });
});

test("a Core restart retires the pending old resource load without retrying or blocking the new generation", async () => {
  const base = { schemaVersion: 2, generationId: "old", characterId: "sample", displayName: "角色", initialMessage: "你好", themeTokens: {}, visual: null, visualReasonCode: "VISUAL_NOT_BOUND" };
  let current = base;
  let resume;
  let reads = 0;
  const invoke = async command => {
    if (command === "runtime_lifecycle_snapshot") return initializing(current.generationId);
    reads += 1; return current;
  };
  const pending = loadCurrentCharacterPresentation({
    invoke,
    expectedGenerationId: "old",
    setTimer: callback => { resume = callback; },
  });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(typeof resume, "function");
  current = { ...base, generationId: "new", visualReasonCode: "PLUGIN_DISABLED" };
  resume();
  assert.equal(await pending, null);
  assert.equal(reads, 2);
  const next = await loadCurrentCharacterPresentation({ invoke, expectedGenerationId: "new" });
  assert.equal(next.generationId, "new");
  assert.equal(next.visualReasonCode, "PLUGIN_DISABLED");
});

test("a native resource error is preserved without retrying", async () => {
  for (const code of ["VISUAL_MODULE_REJECTED", "CHARACTER_ROOT_UNAVAILABLE"]) {
    const failure = new Error(`${code}: missing plugin file`);
    await assert.rejects(loadCurrentCharacterPresentation({
      invoke: async () => { throw failure; },
      expectedGenerationId: "current",
    }), error => error === failure);
  }
});

function settledReducer() {
  const reducer = createChatPresentationReducer({
    initialMessage: "A 的问候",
    defaultPortraitKey: "a-default",
  });
  reducer.reduce(ready("generation-a", 1));
  reducer.reduce({
    type: "chat.started",
    generationId: "generation-a",
    generationNumber: 1,
    operationId: "reply-a",
  });
  reducer.reduce({
    type: "chat.completed",
    generationId: "generation-a",
    generationNumber: 1,
    operationId: "reply-a",
    reply: { segments: [{ text: "A 的旧回复", portrait: "a-smile" }] },
  });
  reducer.setTypingText("A 的旧回复");
  reducer.finishTyping();
  return reducer;
}

test("same-character Core restart preserves old opaque history values", () => {
  const reducer = settledReducer();
  const result = rebindCharacterPresentation({
    currentCharacterId: "character-a",
    nextPresentation: {
      characterId: "character-a",
      portraitKeys: ["new-default", "new-smile"],
      defaultPortraitKey: "new-default",
      concernedPortraitKey: "new-default",
    },
    currentReducer: reducer,
  });
  assert.equal(result.reducer, reducer);
  assert.equal(result.reducer.current().bubbleText, "A 的旧回复");
  assert.deepEqual(
    result.reducer.current().replyHistorySegments.map((segment) => segment.portrait),
    ["a-smile"],
  );
});

test("A to B replaces reply browsing state and exposes only B greeting", () => {
  const result = rebindCharacterPresentation({
    currentCharacterId: "character-a",
    nextPresentation: {
      characterId: "character-b",
      initialMessage: "B 的新问候",
      defaultPortraitKey: "b-default",
      thinkingPortraitKey: "b-thinking",
      concernedPortraitKey: "b-concerned",
    },
    currentReducer: settledReducer(),
  });
  assert.equal(result.characterChanged, true);
  assert.equal(result.greetingPending, true);
  assert.deepEqual(result.reducer.current().replyHistorySegments, []);
  result.reducer.reduce(ready("generation-b", 2));
  const greeting = result.reducer.beginGreeting();
  assert.equal(greeting.applied, true);
  assert.deepEqual(greeting.state.segments.map((segment) => segment.text), ["B 的新问候"]);
});
