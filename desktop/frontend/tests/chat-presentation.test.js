import assert from "node:assert/strict";
import test from "node:test";

import { createChatPresentationReducer } from "../chat/chat-presentation.js";
import { createTypewriter } from "../pet/typewriter.js";

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

test("initial startup keeps the greeting hidden until an explicit one-shot reveal", () => {
  const reducer = createChatPresentationReducer({
    initialMessage: "早上好，今天也请多关照。",
    defaultPortraitKey: "__default__",
    thinkingPortraitKey: "thinking",
    concernedPortraitKey: "concerned",
  });

  reducer.reduce(lifecycle("startup", 1, 1));
  assert.equal(reducer.current().bubbleText, "");
  assert.equal(reducer.current().portrait, "__default__");

  reducer.reduce(lifecycle("initializing", 1, 2));
  assert.equal(reducer.current().bubbleText, "");
  assert.equal(reducer.current().portrait, "__default__");

  reducer.reduce(lifecycle("ready", 1, 3));
  assert.equal(reducer.current().bubbleText, "");
  assert.equal(reducer.current().portrait, "__default__");
  assert.equal(reducer.beginGreeting().applied, true);
  assert.equal(reducer.current().phase, "typing");
  assert.deepEqual(reducer.current().segments.map(({ text }) => text), ["早上好，今天也请多关照。"]);
  assert.equal(reducer.beginGreeting().applied, false);
});

test("startup lifecycle refreshes preserve an active one-shot greeting", () => {
  const reducer = createChatPresentationReducer({
    initialMessage: "启动问候",
    defaultPortraitKey: "__default__",
  });
  reducer.reduce(lifecycle("startup", 1, 1));
  reducer.beginGreeting();
  reducer.setTypingText("启动");
  reducer.reduce(lifecycle("initializing", 1, 2));
  assert.equal(reducer.current().phase, "typing");
  assert.equal(reducer.current().bubbleText, "启动");
  assert.equal(reducer.current().canSkip, true);
  reducer.reduce(lifecycle("ready", 1, 3));
  assert.equal(reducer.current().phase, "typing");
  assert.equal(reducer.current().bubbleText, "启动");
});

test("ready, thinking, complete reply typing, and settled form one deterministic path", () => {
  const reducer = readyReducer();
  assert.equal(reducer.current().phase, "ready");
  assert.equal(reducer.current().bubbleText, "");
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

test("chat.started preserves the committed portrait while waiting", () => {
  const reducer = readyReducer();
  reducer.setPortraitForTest?.("smile");
  reducer.reduce({
    type: "chat.started",
    generationId: "generation-1",
    generationNumber: 1,
    operationId: "op-portrait",
  });
  assert.equal(reducer.current().portrait, "__default__");
});

test("same-generation ready updates preserve active cancel and typewriter skip actions", () => {
  const reducer = readyReducer();
  reducer.reduce({ type: "chat.started", generationId: "generation-1", generationNumber: 1, operationId: "op-1" });

  assert.equal(reducer.reduce(lifecycle("ready", 1, 2)).applied, true);
  assert.equal(reducer.current().phase, "thinking");
  assert.equal(reducer.current().operationId, "op-1");
  assert.equal(reducer.current().canCancel, true);

  reducer.reduce({
    type: "chat.completed",
    generationId: "generation-1",
    generationNumber: 1,
    operationId: "op-1",
    reply: { segments: [{ text: "完整回复", portrait: "smile" }] },
  });
  assert.equal(reducer.reduce(lifecycle("ready", 1, 3)).applied, true);
  assert.equal(reducer.current().phase, "typing");
  assert.equal(reducer.current().operationId, "op-1");
  assert.equal(reducer.current().canSkip, true);
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

test("Core restart preserves the settled presentation and rejects old generation callbacks", () => {
  const reducer = readyReducer();
  reducer.reduce({ type: "chat.started", generationId: "generation-1", generationNumber: 1, operationId: "old" });
  reducer.reduce({
    type: "chat.completed",
    generationId: "generation-1",
    generationNumber: 1,
    operationId: "old",
    reply: { segments: [{ text: "切换前的回复", portrait: "smile" }] },
  });
  reducer.setTypingText("切换前的回复");
  reducer.setTypingSegment({ portrait: "smile" });
  reducer.finishTyping();
  reducer.reduce(lifecycle("core_crashed", 1, 2));
  reducer.reduce(lifecycle("restarting", 2, 3));
  reducer.reduce(lifecycle("ready", 2, 4));
  assert.equal(reducer.current().phase, "settled");
  assert.equal(reducer.current().generationId, "generation-2");
  assert.equal(reducer.current().bubbleText, "切换前的回复");
  assert.equal(reducer.current().portrait, "smile");
  assert.equal(
    reducer.reduce({ type: "chat.completed", generationId: "generation-1", generationNumber: 1, operationId: "old", reply: { segments: [{ text: "late" }] } }).applied,
    false,
  );
  assert.notEqual(reducer.current().bubbleText, "late");
});

test("timing updates are snapshotted for the next reply without retiming the active one", () => {
  const timers = [];
  const typewriter = createTypewriter({
    intervalMs: 28,
    segmentPauseMs: 160,
    setTimer(callback, delay) { timers.push({ callback, delay }); return timers.length; },
    clearTimer() {},
  });
  typewriter.start([{ text: "ab" }]);
  assert.equal(timers.at(-1).delay, 28);
  typewriter.updateTiming({ intervalMs: 51, segmentPauseMs: 275 });
  timers.shift().callback();
  assert.equal(timers.at(-1).delay, 28);
  typewriter.skip();
  typewriter.start([{ text: "next" }]);
  assert.equal(timers.at(-1).delay, 51);
});

test("multi-segment replies clear the previous segment and select Chinese translation", () => {
  const timers = [];
  const rendered = [];
  const segments = [];
  const typewriter = createTypewriter({
    intervalMs: 10,
    segmentPauseMs: 20,
    language: "zh",
    setTimer(callback, delay) { timers.push({ callback, delay }); return timers.length; },
    clearTimer() {},
    onText(text) { rendered.push(text); },
    onSegment(_segment, index) { segments.push(index); },
  });
  typewriter.start([
    { text: "いち", translation: "一" },
    { text: "に", translation: "二" },
  ]);
  while (timers.length) timers.shift().callback();
  assert.deepEqual(segments, [0, 1]);
  assert.deepEqual(rendered, ["", "一", "", "二"]);
});

test("skip completes only the current segment and keeps later segments sequential", () => {
  const timers = [];
  let nextTimer = 0;
  const rendered = [];
  const completed = [];
  const typewriter = createTypewriter({
    intervalMs: 10,
    segmentPauseMs: 20,
    setTimer(callback, delay) { const id = ++nextTimer; timers.push({ id, callback, delay }); return id; },
    clearTimer(id) { const index = timers.findIndex((timer) => timer.id === id); if (index >= 0) timers.splice(index, 1); },
    onText(text) { rendered.push(text); },
    onComplete(result) { completed.push(result); },
  });
  typewriter.start([{ text: "ab" }, { text: "cd" }]);
  timers.shift().callback();
  assert.equal(typewriter.skip(), true);
  assert.equal(rendered.at(-1), "ab");
  assert.equal(completed.length, 0);
  while (timers.length) timers.shift().callback();
  assert.equal(rendered.at(-1), "cd");
  assert.equal(completed.length, 1);
});

test("changing subtitle language restarts only the active segment without mixed text", () => {
  const timers = [];
  let nextTimer = 0;
  const rendered = [];
  const typewriter = createTypewriter({
    intervalMs: 10,
    language: "zh",
    setTimer(callback, delay) { const id = ++nextTimer; timers.push({ id, callback, delay }); return id; },
    clearTimer(id) { const index = timers.findIndex((timer) => timer.id === id); if (index >= 0) timers.splice(index, 1); },
    onText(text) { rendered.push(text); },
  });
  typewriter.start([{ text: "かな", translation: "中文" }, { text: "次", translation: "下一段" }]);
  timers.shift().callback();
  assert.equal(rendered.at(-1), "中");
  assert.equal(typewriter.updateLanguage("ja"), true);
  assert.equal(rendered.at(-1), "");
  timers.shift().callback();
  assert.equal(rendered.at(-1), "か");
  assert.equal(rendered.includes("中か"), false);
});

test("reduced motion completes one segment immediately but preserves the segment pause", () => {
  const timers = [];
  const rendered = [];
  const typewriter = createTypewriter({
    reducedMotion: true,
    segmentPauseMs: 25,
    setTimer(callback, delay) { timers.push({ callback, delay }); return timers.length; },
    clearTimer() {},
    onText(text) { rendered.push(text); },
  });
  typewriter.start([{ text: "first" }, { text: "second" }]);
  assert.equal(rendered.at(-1), "first");
  assert.equal(timers.at(-1).delay, 25);
  timers.shift().callback();
  assert.equal(rendered.at(-1), "second");
});
