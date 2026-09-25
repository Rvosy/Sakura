import assert from "node:assert/strict";
import test from "node:test";

import { composerPlaceholder, createChatPresentationReducer } from "../chat/chat-presentation.js";
import { bubbleJapaneseOriginal, createTypewriter, selectSegmentText } from "../pet/typewriter.js";

const lifecycle = (status, generationNumber = 1, revision = 1, canRetry = false, failure = null) => ({
  type: "lifecycle",
  status,
  generationId: `generation-${generationNumber}`,
  generationNumber,
  revision,
  canRetry,
  failure,
});

test("only a stopped Core failure exposes the manual retry action", () => {
  const reducer = readyReducer();
  reducer.reduce(lifecycle("failed", 1, 2, false));
  assert.equal(reducer.current().canRetry, false);
  reducer.reduce(lifecycle("failed", 1, 3, true, {
    code: "unexpected_exit",
    message: "Core 进程意外退出。",
  }));
  assert.equal(reducer.current().canRetry, true);
  assert.equal(reducer.current().lifecycleHeadline, "Core 进程意外退出。");
  reducer.reduce(lifecycle("rehydrating", 2, 1, false));
  assert.equal(reducer.current().canRetry, false);
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


test("degraded lifecycle remains interactive when the selected character session is usable", () => {
  const reducer = createChatPresentationReducer({
    initialMessage: "受限状态问候",
    defaultPortraitKey: "__default__",
  });

  reducer.reduce(lifecycle("degraded"));
  assert.equal(reducer.current().phase, "ready");
  assert.equal(reducer.current().lifecycle, "degraded");
  assert.equal(reducer.reduce({
    type: "chat.started",
    generationId: "generation-1",
    generationNumber: 1,
    operationId: "degraded-op",
  }).applied, true);
  assert.equal(reducer.current().canCancel, true);
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
  assert.equal(Object.hasOwn(reducer.current(), "canSkip"), false);
  reducer.setTypingText("完整回复");
  reducer.finishTyping();
  assert.equal(reducer.current().phase, "settled");
  assert.equal(reducer.current().bubbleText, "完整回复");
});

test("completed replies preserve the waiting frame until their first text segment starts", () => {
  const reducer = readyReducer();
  reducer.reduce({ type: "chat.started", generationId: "generation-1", generationNumber: 1, operationId: "op-tts" });
  reducer.setWaitingText("....");

  reducer.reduce({
    type: "chat.completed",
    generationId: "generation-1",
    generationNumber: 1,
    operationId: "op-tts",
    reply: { segments: [{ text: "语音回复", portrait: "smile" }] },
  });
  assert.equal(reducer.current().phase, "typing");
  assert.equal(reducer.current().bubbleText, "....");

  assert.equal(reducer.setWaitingText(".....").applied, true);
  assert.equal(reducer.current().bubbleText, ".....");


  reducer.setTypingSegment(reducer.current().segments[0], 0);

  reducer.setTypingText("");
  assert.equal(reducer.current().bubbleText, "");
  reducer.finishTyping();
  assert.equal(reducer.setWaitingText("...").applied, false);
});

test("a new request replaces a completed reply that is still typing or playing audio", () => {
  const reducer = readyReducer();
  reducer.reduce({
    type: "chat.started",
    generationId: "generation-1",
    generationNumber: 1,
    operationId: "first",
  });
  reducer.reduce({
    type: "chat.completed",
    generationId: "generation-1",
    generationNumber: 1,
    operationId: "first",
    reply: { segments: [{ text: "仍在播放的第一轮回复", portrait: "smile" }] },
  });
  reducer.setTypingText("");

  const started = reducer.reduce({
    type: "chat.started",
    generationId: "generation-1",
    generationNumber: 1,
    operationId: "second",
  });
  assert.equal(started.applied, true);
  assert.equal(started.state.phase, "thinking");
  assert.equal(started.state.operationId, "second");
  assert.equal(started.state.bubbleText, ".");

  const completed = reducer.reduce({
    type: "chat.completed",
    generationId: "generation-1",
    generationNumber: 1,
    operationId: "second",
    reply: { segments: [{ text: "第二轮正常回复", portrait: "calm" }] },
  });
  assert.equal(completed.applied, true);
  assert.equal(completed.state.phase, "typing");
  assert.equal(completed.state.segments[0].text, "第二轮正常回复");
});

test("silent proactive requests preserve the current UI until the completed reply starts", () => {
  const reducer = readyReducer();
  reducer.reduce({ type: "chat.started", generationId: "generation-1", generationNumber: 1, operationId: "previous" });
  reducer.reduce({
    type: "chat.completed",
    generationId: "generation-1",
    generationNumber: 1,
    operationId: "previous",
    reply: { segments: [{ text: "上一条回复", portrait: "smile" }] },
  });
  reducer.setTypingSegment(reducer.current().segments[0], 0);
  reducer.setTypingText("上一条回复");
  reducer.finishTyping();

  const before = reducer.current();
  const started = reducer.reduce({
    type: "chat.started",
    generationId: "generation-1",
    generationNumber: 1,
    operationId: "proactive",
    presentation: "silent",
  });
  assert.equal(started.applied, true);
  assert.equal(started.state.phase, before.phase);
  assert.equal(started.state.bubbleText, before.bubbleText);

  assert.equal(started.state.canCancel, false);
  assert.equal(started.state.silentInteraction, true);
  assert.equal(reducer.setWaitingText("...").applied, false);

  reducer.reduce({
    type: "chat.completed",
    generationId: "generation-1",
    generationNumber: 1,
    operationId: "proactive",
    presentation: "silent",
    reply: { segments: [{ text: "我看到你还在继续。", portrait: "calm" }] },
  });
  assert.equal(reducer.current().phase, "typing");
  assert.equal(reducer.current().bubbleText, "上一条回复");
  assert.equal(reducer.current().silentInteraction, false);
});

test("silent terminals without a reply preserve the UI and release the next conversation", () => {
  for (const terminal of ["chat.failed", "chat.cancelled", "chat.completed"]) {
    const reducer = readyReducer();
    const before = reducer.current();
    reducer.reduce({
      type: "chat.started",
      generationId: "generation-1",
      generationNumber: 1,
      operationId: terminal,
      presentation: "silent",
    });
    reducer.reduce({
      type: terminal,
      generationId: "generation-1",
      generationNumber: 1,
      operationId: terminal,
      error: { message: "不应展示" },
      reply: { segments: [] },
    });
    assert.equal(reducer.current().phase, before.phase);
    assert.equal(reducer.current().bubbleText, before.bubbleText);
    assert.equal(reducer.current().operationId, null);
    assert.equal(reducer.current().silentInteraction, false);
    assert.equal(reducer.reduce({
      type: "chat.started", generationId: "generation-1", generationNumber: 1,
      operationId: "next-manual",
    }).applied, true);
    assert.equal(reducer.current().phase, "thinking");
  }
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
  assert.equal(reducer.current().bubbleText, ".");
});



test("subtitle changes do not replace cancellation or provider error copy with an older reply", () => {
  for (const terminal of [
    { type: "chat.cancelled", reason: "user" },
    { type: "chat.failed", error: { code: "PROVIDER_HTTP_429", message: "请求过于频繁。", retryable: true } },
  ]) {
    const reducer = readyReducer();
    reducer.reduce({ type: "chat.started", generationId: "generation-1", generationNumber: 1, operationId: "reply" });
    reducer.reduce({
      type: "chat.completed",
      generationId: "generation-1",
      generationNumber: 1,
      operationId: "reply",
      reply: { segments: [{ text: "原文", translation: "译文", portrait: "smile" }] },
    });
    reducer.setTypingSegment(reducer.current().segments[0], 0);
    reducer.setTypingText("译文\n原文", ["译文", "原文"]);
    reducer.finishTyping();
    assert.deepEqual(reducer.current().subtitleTracks, ["译文", "原文"]);
    reducer.reduce({ type: "chat.started", generationId: "generation-1", generationNumber: 1, operationId: "terminal" });
    reducer.reduce({ ...terminal, generationId: "generation-1", generationNumber: 1, operationId: "terminal" });

    const terminalCopy = reducer.current().bubbleText;
    assert.equal(reducer.refreshVisibleReply("原文").applied, false);
    assert.equal(reducer.current().bubbleText, terminalCopy);
    assert.deepEqual(reducer.current().subtitleTracks, []);
    const reviewed = reducer.reviewReplyAt(0, "译文\n原文", ["译文", "原文"]);
    assert.equal(reviewed.applied, true);
    assert.deepEqual(reviewed.state.subtitleTracks, ["译文", "原文"]);
    reducer.refreshVisibleReply("原文", ["原文"]);
    assert.deepEqual(reducer.current().subtitleTracks, ["原文"]);
  }
});

test("reply history navigation crosses turns and only changes text", () => {
  const reducer = readyReducer();
  reducer.reduce({ type: "chat.started", generationId: "generation-1", generationNumber: 1, operationId: "first" });
  reducer.reduce({
    type: "chat.completed",
    generationId: "generation-1",
    generationNumber: 1,
    operationId: "first",
    reply: { segments: [
      { text: "第一段", portrait: "calm" },
      { text: "第二段", portrait: "smile" },
    ] },
  });
  assert.equal(reducer.reviewReplyAt(0, "第一段").applied, false);
  reducer.setTypingSegment(reducer.current().segments[1], 1);
  reducer.setTypingText("第二段");
  reducer.finishTyping();

  reducer.reduce({ type: "chat.started", generationId: "generation-1", generationNumber: 1, operationId: "second" });
  reducer.reduce({
    type: "chat.completed",
    generationId: "generation-1",
    generationNumber: 1,
    operationId: "second",
    reply: { segments: [{ text: "第三段", portrait: "surprised" }] },
  });
  reducer.setTypingSegment(reducer.current().segments[0], 0);
  reducer.setTypingText("第三段");
  reducer.finishTyping();

  assert.deepEqual(reducer.current().replyHistorySegments.map(({ text }) => text), ["第一段", "第二段", "第三段"]);
  assert.deepEqual(
    reducer.current().replyHistorySegments.map(({ operationId, segmentIndex }) => ({
      operationId,
      segmentIndex,
    })),
    [
      { operationId: "first", segmentIndex: 0 },
      { operationId: "first", segmentIndex: 1 },
      { operationId: "second", segmentIndex: 0 },
    ],
  );
  assert.equal(reducer.current().replyHistoryIndex, 2);
  assert.equal(reducer.current().canReviewPrevious, true);
  assert.equal(reducer.current().canReviewNext, false);
  assert.equal(reducer.current().canReplayCurrentReply, true);

  let reviewed = reducer.reviewReplyAt(1, "第二段");
  assert.equal(reviewed.applied, true);
  assert.equal(reviewed.state.bubbleText, "第二段");

  assert.equal(reviewed.state.canReviewPrevious, true);
  assert.equal(reviewed.state.canReviewNext, true);
  assert.equal(reviewed.state.canReplayCurrentReply, true);

  reviewed = reducer.reviewReplyAt(0, "第一段");

  assert.equal(reviewed.state.canReviewPrevious, false);
  assert.equal(reviewed.state.canReplayCurrentReply, true);
  assert.equal(reducer.reviewReplyAt(-1, "越界").applied, false);
});

test("suppressed history segments cannot be replayed", () => {
  const reducer = readyReducer();
  reducer.reduce({ type: "chat.started", generationId: "generation-1", generationNumber: 1, operationId: "silent" });
  reducer.reduce({
    type: "chat.completed",
    generationId: "generation-1",
    generationNumber: 1,
    operationId: "silent",
    reply: { segments: [{ text: "不朗读", suppressTts: true }] },
  });
  reducer.setTypingSegment(reducer.current().segments[0], 0);
  reducer.setTypingText("不朗读");
  reducer.finishTyping();
  assert.equal(reducer.current().canReplayCurrentReply, false);
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
  reducer.reduce(lifecycle("failed", 1, 2));
  reducer.reduce(lifecycle("rehydrating", 2, 3));
  reducer.reduce(lifecycle("ready", 2, 4));
  assert.equal(reducer.current().phase, "settled");
  assert.equal(reducer.current().generationId, "generation-2");
  assert.equal(reducer.current().bubbleText, "切换前的回复");

  assert.deepEqual(reducer.current().replyHistorySegments.map(({ text }) => text), ["切换前的回复"]);
  assert.equal(
    reducer.reduce({ type: "chat.completed", generationId: "generation-1", generationNumber: 1, operationId: "old", reply: { segments: [{ text: "late" }] } }).applied,
    false,
  );
  assert.notEqual(reducer.current().bubbleText, "late");
});

test("Core failure gives active work one interrupted terminal and preserves earlier completed replies", () => {
  const reducer = readyReducer();
  reducer.reduce({ type: "chat.started", generationId: "generation-1", generationNumber: 1, operationId: "done" });
  reducer.reduce({
    type: "chat.completed",
    generationId: "generation-1",
    generationNumber: 1,
    operationId: "done",
    reply: { segments: [{ text: "已经完成", portrait: "smile" }] },
  });
  reducer.setTypingSegment(reducer.current().segments[0], 0);
  reducer.setTypingText("已经完成");
  reducer.finishTyping();

  reducer.reduce({ type: "chat.started", generationId: "generation-1", generationNumber: 1, operationId: "active" });
  reducer.reduce({
    type: "chat.completed",
    generationId: "generation-1",
    generationNumber: 1,
    operationId: "active",
    reply: { segments: [{ text: "不应跨代续播", portrait: "concerned" }] },
  });
  reducer.setTypingText("不应");

  reducer.reduce(lifecycle("failed", 1, 2, true, {
    code: "connection_lost",
    message: "与 Core 的连接已中断。",
  }));
  assert.equal(reducer.current().phase, "error");
  assert.equal(reducer.current().operationId, null);
  assert.equal(reducer.current().canCancel, false);
  assert.equal(reducer.current().bubbleText, "连接中断，本次回复已停止。");
  assert.equal(reducer.current().error.code, "CHAT_INTERRUPTED");
  assert.deepEqual(reducer.current().replyHistorySegments.map(({ text }) => text), ["已经完成"]);

  reducer.reduce(lifecycle("rehydrating", 2, 1));
  reducer.reduce(lifecycle("ready", 2, 2));
  assert.equal(reducer.current().bubbleText, "连接中断，本次回复已停止。");
  assert.deepEqual(reducer.current().replyHistorySegments.map(({ text }) => text), ["已经完成"]);
  assert.equal(reducer.current().lifecycle, "ready");
});

test("missing voice uses the longer pause and finished playback keeps the short pause", async () => {
  const timers = [];
  const typewriter = createTypewriter({
    intervalMs: 10,
    segmentPauseMs: 20,
    silentSegmentPauseMs: 800,
    setTimer(callback, delay) { timers.push({ callback, delay }); return timers.length; },
    clearTimer() {},
    onSegmentComplete(_segment, index) {
      return Promise.resolve(index === 0);
    },
  });
  typewriter.start([{ text: "a" }, { text: "b" }, { text: "c" }]);
  timers.shift().callback();
  await Promise.resolve();
  assert.equal(timers.at(-1).delay, 20);
  timers.shift().callback();
  timers.shift().callback();
  await Promise.resolve();
  assert.equal(timers.at(-1).delay, 800);
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
  assert.equal(typewriter.updateLanguage("bilingual"), true);
  timers.shift().callback();
  assert.equal(rendered.at(-1), "中\nか");
  assert.equal(typewriter.updateLanguage("bilingual_ja"), true);
  timers.shift().callback();
  assert.equal(rendered.at(-1), "か\n中");
  typewriter.skip();
  assert.equal(rendered.at(-1), "かな\n中文");
  while (timers.length) timers.shift().callback();
  assert.equal(rendered.at(-1), "次\n下一段");
});

test("bilingual typing starts both languages together and waits for the longer line", () => {
  const timers = [];
  const rendered = [];
  const completedSegments = [];
  let completed = false;
  const typewriter = createTypewriter({
    language: "bilingual",
    setTimer(callback) { timers.push(callback); return callback; },
    clearTimer(callback) { const index = timers.indexOf(callback); if (index >= 0) timers.splice(index, 1); },
    onText(text) { rendered.push(text); },
    onSegmentComplete(_segment, index) { completedSegments.push(index); },
    onComplete() { completed = true; },
  });
  typewriter.start([{ text: "かなよ", translation: "中文" }, { text: "次", translation: "下" }]);
  timers.shift()();
  assert.equal(rendered.at(-1), "中\nか");
  timers.shift()();
  assert.equal(rendered.at(-1), "中文\nかな");
  assert.deepEqual(completedSegments, []);
  timers.shift()();
  assert.equal(rendered.at(-1), "中文\nかなよ");
  assert.deepEqual(completedSegments, [0]);
  assert.equal(completed, false);
  while (timers.length) timers.shift()();
  assert.equal(rendered.at(-1), "下\n次");
  assert.deepEqual(completedSegments, [0, 1]);
  assert.equal(completed, true);
});

test("bilingual subtitles pair translation and original without empty or duplicate lines", () => {
  assert.equal(selectSegmentText({ text: "かな", translation: "中文" }, "bilingual"), "中文\nかな");
  assert.equal(selectSegmentText({ text: "かな", translation: "  " }, "bilingual"), "かな");
  assert.equal(selectSegmentText({ text: "", translation: "中文" }, "bilingual"), "中文");
  assert.equal(selectSegmentText({ text: "同文", translation: " 同文 " }, "bilingual"), "同文");
  assert.equal(selectSegmentText({ text: "かな", translation: "中文" }, "bilingual_ja"), "かな\n中文");
  assert.equal(selectSegmentText({ text: "かな", translation: " " }, "bilingual_ja"), "かな");
});

test("greeting keeps Chinese above the Japanese line, and the original stays hidden until enabled", () => {
  const reducer = createChatPresentationReducer({
    initialMessage: "……起動した。用事があるなら、呼んで。",
    initialMessageTranslation: "……启动了。有事的话，叫我。",
  });
  reducer.reduce(lifecycle("ready"));
  const greeting = reducer.beginGreeting();
  const segment = greeting.state.segments[0];
  assert.equal(segment.text, "……起動した。用事があるなら、呼んで。");
  assert.equal(segment.translation, "……启动了。有事的话，叫我。");
  const typing = {
    phase: "typing",
    bubbleText: "……启动了",
    segments: [segment],
    showingReplyHistorySegment: false,
  };
  assert.equal(bubbleJapaneseOriginal(typing, "zh", false), "");
  assert.equal(bubbleJapaneseOriginal(typing, "zh", true), segment.text);
  assert.equal(bubbleJapaneseOriginal({ ...typing, phase: "settled", bubbleText: segment.translation }, "zh", true), segment.text);
  assert.equal(bubbleJapaneseOriginal({ ...typing, phase: "settled", bubbleText: segment.translation }, "ja", true), "");
});

test("a character without a ready assistant shows the settled setup state instead of startup progress", () => {
  const reducer = createChatPresentationReducer({ initialMessage: "新角色的问候" });
  reducer.reduce(lifecycle("rehydrating", 1, 1));
  reducer.reduce(lifecycle("setup_required", 1, 2));
  assert.equal(reducer.current().lifecycle, "setup_required");
  assert.equal(reducer.current().bubbleText, reducer.current().lifecycleHeadline);
});
