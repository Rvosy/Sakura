import assert from "node:assert/strict";
import test from "node:test";

import { createChatPresentationReducer } from "../chat/chat-presentation.js";

const lifecycle = (status, generationNumber = 1, revision = 1) => ({
  type: "lifecycle",
  status,
  generationId: `generation-${generationNumber}`,
  generationNumber,
  revision,
});

function readyReducer() {
  const reducer = createChatPresentationReducer({
    initialMessage: "你好，我是当前角色。",
    defaultPortraitKey: "__default__",
    thinkingPortraitKey: "thinking",
    concernedPortraitKey: "concerned",
  });
  reducer.reduce(lifecycle("ready"));
  return reducer;
}

test("ready, thinking, complete reply typing, and settled form one deterministic path", () => {
  const reducer = readyReducer();
  assert.equal(reducer.current().phase, "ready");
  assert.equal(reducer.current().bubbleText, "你好，我是当前角色。");
  assert.equal(reducer.reduce({ type: "chat.started", generationId: "generation-1", generationNumber: 1, operationId: "op-1" }).applied, true);
  assert.equal(reducer.current().canCancel, true);
  assert.equal(
    reducer.reduce({
      type: "chat.completed",
      generationId: "generation-1",
      generationNumber: 1,
      operationId: "op-1",
      reply: { segments: [{ text: "完整回复", portrait: "smile" }] },
    }).applied,
    true,
  );
  assert.equal(reducer.current().phase, "typing");
  assert.equal(reducer.current().canCancel, false);
  assert.equal(reducer.current().canSkip, true);
  reducer.setTypingText("完整回复");
  reducer.finishTyping();
  assert.equal(reducer.current().phase, "settled");
  assert.equal(reducer.current().bubbleText, "完整回复");
});

test("old operations, generations, and revisions cannot replace current presentation", () => {
  const reducer = readyReducer();
  reducer.reduce({ type: "chat.started", generationId: "generation-1", generationNumber: 1, operationId: "current" });
  for (const stale of [
    { type: "chat.failed", generationId: "generation-1", generationNumber: 1, operationId: "old", error: { message: "wrong" } },
    { type: "chat.cancelled", generationId: "generation-0", generationNumber: 0, operationId: "current" },
    lifecycle("failed", 1, 0),
  ]) assert.equal(reducer.reduce(stale).applied, false);
  assert.equal(reducer.current().phase, "thinking");
  assert.equal(reducer.current().bubbleText, "正在组织完整回复……");
});

test("failed and cancelled terminals are operation-scoped and immediately retryable", () => {
  for (const terminal of ["chat.failed", "chat.cancelled"]) {
    const reducer = readyReducer();
    reducer.reduce({ type: "chat.started", generationId: "generation-1", generationNumber: 1, operationId: "op" });
    const event = { type: terminal, generationId: "generation-1", generationNumber: 1, operationId: "op" };
    if (terminal === "chat.failed") event.error = { code: "OFFLINE", message: "网络不可达", retryable: true };
    assert.equal(reducer.reduce(event).applied, true);
    assert.equal(reducer.current().phase, terminal === "chat.failed" ? "error" : "settled");
    assert.equal(reducer.current().operationId, null);
    assert.equal(reducer.current().canCancel, false);
  }
});

test("Core restart resets presentation and rejects old generation callbacks", () => {
  const reducer = readyReducer();
  reducer.reduce({ type: "chat.started", generationId: "generation-1", generationNumber: 1, operationId: "old" });
  reducer.reduce(lifecycle("core_crashed", 1, 2));
  reducer.reduce(lifecycle("restarting", 2, 3));
  reducer.reduce(lifecycle("ready", 2, 4));
  assert.equal(reducer.current().phase, "ready");
  assert.equal(reducer.current().generationId, "generation-2");
  assert.equal(
    reducer.reduce({ type: "chat.completed", generationId: "generation-1", generationNumber: 1, operationId: "old", reply: { segments: [{ text: "late" }] } }).applied,
    false,
  );
  assert.notEqual(reducer.current().bubbleText, "late");
});
