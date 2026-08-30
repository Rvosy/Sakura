import assert from "node:assert/strict";
import test from "node:test";

import { createChatPresentationReducer } from "../chat/chat-presentation.js";
import { rebindCharacterPresentation } from "../pet/character-generation.js";

const ready = (generationId, generationNumber) => ({
  type: "lifecycle",
  status: "ready",
  generationId,
  generationNumber,
  revision: 1,
  canRetry: false,
  failure: null,
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
    nextPresentation: { characterId: "character-a" },
    currentReducer: reducer,
  });
  assert.equal(result.characterChanged, false);
  assert.equal(result.reducer, reducer);
  assert.equal(result.reducer.current().bubbleText, "A 的旧回复");
  assert.equal(result.greetingPending, false);
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
