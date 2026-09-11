import assert from "node:assert/strict";
import test from "node:test";

import { createChatPresentationReducer } from "../chat/chat-presentation.js";
import { isNewCharacterGeneration, rebindCharacterPresentation } from "../pet/character-generation.js";
import { loadCurrentCharacterPresentation } from "../pet/character-presentation.js";
import { createRendererHost } from "../pet/renderer-host.js";

test("startup waits for plugin binding, while real unavailability is reported immediately", async () => {
  const base = { schemaVersion: 2, generationId: "g", characterId: "sample", displayName: "角色", initialMessage: "你好", themeTokens: {}, visual: null, visualReasonCode: "VISUAL_NOT_BOUND" };
  const readyVisual = { bindingId: "a".repeat(32), resourceId: "sample-model", renderer: `http://sakura-character.localhost/module/67/${"a".repeat(32)}/renderer.js`, data: {}, assets: {} };
  const unavailable = [];
  const root = { replaceChildren() {}, append() {}, ownerDocument: { createElement: () => ({ remove() {} }) } };
  const host = createRendererHost({ container: root, onUnavailable: code => unavailable.push(code), loadModule: async () => ({ mount: () => ({ applyState() {}, destroy() {} }) }) });
  // Before first-run setup completes, the app has only a pending placeholder.
  assert.equal(await host.bind(base), false);
  assert.deepEqual(unavailable, []);
  let reads = 0;
  const presentation = await loadCurrentCharacterPresentation({
    invoke: async () => ++reads < 3 ? base : { ...base, visual: readyVisual, visualReasonCode: "READY" },
    setTimer: resolve => resolve(),
  });
  assert.equal(reads, 3);
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

test("first lifecycle publication preserves the initial renderer; a later generation revokes it", () => {
  const reducer = createChatPresentationReducer({initialMessage: "你好"});
  let generation = reducer.current().generationId;
  assert.equal(isNewCharacterGeneration(generation, "g1"), false);
  reducer.reduce(ready("g1", 1));
  generation = reducer.current().generationId;
  assert.equal(isNewCharacterGeneration(generation, "g1"), false);
  assert.equal(isNewCharacterGeneration(generation, "g2"), true);
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

test("same-character Core restart preserves the settled presentation reducer", () => {
  const reducer = settledReducer();
  const result = rebindCharacterPresentation({
    currentCharacterId: "character-a",
    nextPresentation: {
      characterId: "character-a",
      portraitKeys: ["a-default", "a-smile"],
      defaultPortraitKey: "a-default",
      concernedPortraitKey: "a-default",
    },
    currentReducer: reducer,
  });
  assert.equal(result.characterChanged, false);
  assert.equal(result.reducer, reducer);
  assert.equal(result.reducer.current().bubbleText, "A 的旧回复");
  assert.equal(result.greetingPending, false);
});

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
