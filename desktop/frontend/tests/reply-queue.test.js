import test from "node:test";
import assert from "node:assert/strict";
import { createReplyQueue } from "../chat/reply-queue.js";

const event = (operationId, type, presentation = "queued") => ({
  generationId: "generation-a", operationId, type, presentation,
});

test("completed model replies wait for speech presentation without interrupting it", () => {
  let speaking = true;
  const shown = [];
  const queue = createReplyQueue({
    isPresenting: () => speaking,
    publish: value => {
      shown.push([value.operationId, value.type]);
      if (value.type === "chat.completed") speaking = true;
    },
  });
  for (const id of ["move-one", "move-two"]) {
    queue.receive(event(id, "chat.started"));
    queue.receive(event(id, "chat.completed"));
  }
  assert.deepEqual(shown, []);
  speaking = false;
  queue.flush();
  assert.deepEqual(shown, [["move-one", "chat.started"], ["move-one", "chat.completed"]]);
  speaking = false;
  queue.flush();
  assert.deepEqual(shown.slice(2), [["move-two", "chat.started"], ["move-two", "chat.completed"]]);
});

test("a move presents immediately when no previous speech is playing", () => {
  const shown = [];
  const queue = createReplyQueue({ isPresenting: () => false, publish: value => shown.push(value) });
  queue.receive(event("move", "chat.started"));
  queue.receive(event("move", "chat.completed"));
  assert.deepEqual(shown.map(value => value.type), ["chat.started", "chat.completed"]);
});

for (const interruption of [
  event("manual", "chat.started", "interactive"),
  { type: "lifecycle", generationId: "generation-b", status: "ready" },
  { type: "lifecycle", generationId: "generation-a", status: "rehydrating" },
]) {
  test(`pending speech is discarded on ${interruption.type} ${interruption.status || "manual input"}`, () => {
    let speaking = true;
    const shown = [], discarded = [];
    const queue = createReplyQueue({ isPresenting: () => speaking, publish: value => shown.push(value),
      onDiscard: value => discarded.push(value.operationId) });
    queue.receive(event("old-move", "chat.started"));
    queue.receive(event("old-move", "chat.completed"));
    queue.receive(interruption);
    speaking = false;
    queue.flush();
    assert.deepEqual(shown, [interruption]);
    assert.deepEqual(discarded, ["old-move"]);
  });
}
