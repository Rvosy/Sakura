import assert from "node:assert/strict";
import test from "node:test";

import { projectHistoryEntries } from "../history/history-presentation.js";

const NOW = "2026-08-29T12:00:00+08:00";

test("screen observation groups preserve image ownership without duplicating the summary", () => {
  const image = { id: "saved-image", mediaType: "image/png", width: 1920, height: 1080 };
  const projected = projectHistoryEntries([
    entry("observation", { text: "刚才留意了一下屏幕状态。", sourcePluginId: "screen",
      visual: { imageCount: 1 }, images: [image] }, { origin: "host", entryId: "capture" }),
    entry("observation", { text: "画面摘要：正在阅读", sourcePluginId: "screen",
      visual: { imageCount: 1 } }, { origin: "host", entryId: "analysis" }),
    entry("observation", { text: "你分享了 1 张屏幕截图。", images: [image] },
      { origin: "manual_screen", entryId: "manual", turnId: "another-turn" }),
  ]);
  assert.equal(projected.length, 2);
  assert.deepEqual(projected[0].images, [{ ...image, entryId: "capture" }]);
  assert.equal(projected[0].detailsContent, "画面摘要：正在阅读");
  assert.deepEqual(projected[1].images, [{ ...image, entryId: "manual" }]);
  assert.equal(projected[1].role, "human");
  assert.equal(projected[1].align, "right");
  assert.equal(projected[1].content, "");
});

test("image-only sends omit the empty text bubble and keep user text beside pictures", () => {
  const images = [{ id: "image", mediaType: "image/png", width: 100, height: 200 }];
  const projection = projectHistoryEntries([
    entry("human", { text: "" }),
    entry("observation", { text: "你分享了 1 张屏幕截图。", images }, { origin: "manual_screen" }),
    entry("human", { text: "看看这里" }, { turnId: "turn-2", entryId: "human-2" }),
    entry("observation", { text: "你分享了 1 张屏幕截图。", images },
      { origin: "manual_screen", turnId: "turn-2", entryId: "image-2" }),
  ]);
  assert.deepEqual(projection.map(item => item.content), ["", "看看这里", ""]);
  assert.ok(projection.every(item => item.role === "human" && item.align === "right"));
});

function entry(kind, payload, overrides = {}) {
  return {
    entryId: `entry-${kind}`,
    turnId: "turn-1",
    kind,
    origin: "chat",
    createdAt: NOW,
    payload,
    ...overrides,
  };
}

test("history projection preserves participants and current subtitle language", () => {
  const projected = projectHistoryEntries([
    entry("human", { text: "你好" }),
    entry("assistant", {
      segments: [
        { text: "ただいま", translation: "我回来了" },
        { text: "うん", translation: "嗯" },
      ],
    }),
    entry("assistant", {
      segments: [{ text: "ここにいるよ", translation: "我在这里" }],
    }, { entryId: "entry-assistant-2", turnId: "turn-2" }),
  ], {
    assistantName: "桜",
    subtitleLanguage: "zh",
    formatTime: () => "2026/08/29 12:00:00",
  });

  assert.deepEqual(projected.map(({ role, content }) => ({ role, content })), [
    { role: "human", content: "你好" },
    { role: "assistant", content: "我回来了" },
    { role: "assistant", content: "嗯" },
    { role: "assistant", content: "我在这里" },
  ]);
});

test("consecutive lifecycle records each display their own timestamp", () => {
  const events = [
    ["app.closed", "2026-10-15T03:29:13+08:00"],
    ["app.started", "2026-10-15T08:10:00+08:00"],
    ["app.closed", "2026-10-15T20:45:02+08:00"],
    ["app.started", "2026-10-16T00:30:00+08:00"],
  ];
  const projected = projectHistoryEntries(events.map(([eventType, createdAt], index) =>
    entry("system", { text: eventType, eventType }, {
      entryId: `lifecycle-${index}`, turnId: `lifecycle-${index}`, origin: "host", createdAt,
    })), { formatTime: value => value });

  assert.equal(projected.length, events.length);
  projected.forEach((bubble, index) => {
    assert.equal(bubble.showMeta, true);
    assert.ok(bubble.metaText.includes(events[index][1]));
  });
});

test("game notices keep the summary short and put results and record actions in details", () => {
  const projected = projectHistoryEntries([
    entry("system", { text: "开始了五子棋", activity: {
      sourcePluginId: "sakura.gomoku", recordId: "game-1", details: "", actionLabel: "查看棋谱",
    } }),
    entry("system", { text: "结束了五子棋", activity: {
      sourcePluginId: "sakura.gomoku", recordId: "game-1", details: "你获胜，共 9 手。", actionLabel: "查看棋谱",
    } }, { entryId: "game-end" }),
  ]);
  assert.deepEqual(projected.map(item => item.content), ["开始了五子棋", "结束了五子棋"]);
  assert.deepEqual(projected.map(item => item.detailsContent), ["", "你获胜，共 9 手。"]);
  assert.ok(projected.every(item => item.showMeta && item.actionLabel === "查看棋谱"));
});

test("only assistant bubbles carry stable recording identity with original segment indices", () => {
  const projected = projectHistoryEntries([
    entry("human", { text: "hello" }),
    entry("assistant", { segments: [{ text: "silent", suppressTts: true }, { text: "read me" }] }),
    entry("system", { text: "system fact" }),
  ]);
  assert.deepEqual(projected.filter(item => item.historyEntryId).map(item => ({
    historyEntryId: item.historyEntryId, segmentIndex: item.segmentIndex, suppressTts: item.suppressTts,
  })), [
    { historyEntryId: "entry-assistant", segmentIndex: 0, suppressTts: true },
    { historyEntryId: "entry-assistant", segmentIndex: 1, suppressTts: false },
  ]);
});

test("bilingual history pairs each assistant segment and preserves human text", () => {
  const projected = projectHistoryEntries([
    entry("human", { text: "你好" }),
    entry("assistant", { segments: [
      { text: "ただいま", translation: "我回来了" },
      { text: "うん", translation: "" },
    ] }),
  ], { subtitleLanguage: "bilingual" });
  assert.deepEqual(projected.map(({ content }) => content), ["你好", "我回来了\nただいま", "うん"]);
});

test("scheduled screen summaries fold into one humanized observation record", () => {
  const projected = projectHistoryEntries([
    entry("observation", {
      text: "定时屏幕观察已提交给对话模型，共 2 张截图。",
    }, { entryId: "screen-trigger", origin: "scheduled_screen" }),
    entry("observation", {
      text: "画面摘要：用户正在检查串口监控。\n关键元素：监控主界面",
    }, { entryId: "screen-summary", origin: "scheduled_screen" }),
  ], { formatTime: () => "now" });

  assert.equal(projected.length, 1);
  assert.equal(
    projected[0].detailsContent,
    "画面摘要：用户正在检查串口监控。\n关键元素：监控主界面",
  );
});

test("plugin screen observations retain the character perspective and fold only their own summaries", () => {
  for (const trigger of ["插件分享了 3 张图片。", "刚才留意了一下屏幕状态。"]) {
    const visual = { imageCount: 3, capturedAt: NOW };
    const sourcePluginId = "sakura.screen_awareness";
    const entries = [
      entry("observation", { text: trigger, sourcePluginId, visual }, { origin: "host", entryId: "trigger" }),
      entry("observation", {
        text: "画面摘要：用户正在检查界面。", sourcePluginId,
        visual: { ...visual, analysisStatus: "succeeded" },
      }, { origin: "host", entryId: "summary" }),
      entry("observation", { text: "另一次观察", sourcePluginId, visual },
        { origin: "host", turnId: "turn-2", entryId: "other-turn" }),
      entry("observation", { text: "插件发起了一次互动。", sourcePluginId },
        { origin: "host", entryId: "non-visual" }),
      entry("observation", { text: "你分享了 1 张屏幕截图。", visual },
        { origin: "manual_screen", entryId: "manual" }),
      entry("human", { text: trigger }),
    ];
    const saved = structuredClone(entries);
    const projected = projectHistoryEntries(entries);
    assert.equal(projected.length, 4);
    assert.equal(projected[0].detailsContent, "画面摘要：用户正在检查界面。");
    assert.equal(projected[1].detailsContent, "另一次观察");
    assert.ok(projected[2].content.includes(sourcePluginId));
    assert.equal(projected[3].content, trigger);
    assert.deepEqual(entries, saved);
  }
});

test("plugin notifications use explicit purpose while legacy messages remain generic", () => {
  const payloads = [
    { text: "SakuraTTS 有可用更新", sourcePluginId: "tts", sourcePluginName: "SakuraTTS", notificationKind: "update" },
    { text: "想和你聊聊。", sourcePluginId: "tts" },
    { text: "插件消息", sourcePluginId: "other", sourcePluginName: "示例插件" },
  ];
  const entries = payloads.map((payload, index) => entry("observation", payload, { origin: "host", entryId: String(index) }));
  const before = JSON.stringify(entries);
  const projected = projectHistoryEntries(entries);
  assert.equal(projected[0].content, payloads[0].text);
  assert.ok(projected[1].content.includes(payloads[1].sourcePluginId));
  assert.ok(projected[2].content.includes(payloads[2].sourcePluginName));
  assert.equal(JSON.stringify(entries), before);
});
