import assert from "node:assert/strict";
import test from "node:test";

import {
  preservePrependScroll,
  projectHistoryEntries,
  validateHistoryPage,
} from "../history/history-presentation.js";

const NOW = "2026-08-29T12:00:00+08:00";

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

test("observation content is preserved as plain text", () => {
  const unsafe = "<script>alert('x')</script>\n视觉摘要";
  const projected = projectHistoryEntries([
    entry("observation", { text: unsafe }, { origin: "manual_screen" }),
    entry("system", { text: "已确认的关系事实" }, { entryId: "entry-system", origin: "host" }),
  ], { formatTime: () => "now" });

  assert.equal(projected[0].content, unsafe);
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
    assert.equal(projected.length, 5);
    assert.equal(projected[0].detailsContent, "画面摘要：用户正在检查界面。");
    assert.equal(projected[1].detailsContent, "另一次观察");
    assert.ok(projected[2].content.includes(sourcePluginId));
    assert.equal(projected[3].content, "你分享了 1 张屏幕截图。");
    assert.equal(projected[4].content, trigger);
    assert.deepEqual(entries, saved);
  }
});

test("page validation keeps generation identity and cursor shape strict", () => {
  const page = {
    schemaVersion: 1,
    coreGenerationId: "generation-a",
    characterId: "sakura",
    totalCount: 1,
    entries: [entry("human", { text: "hello" })],
    beforeCursor: null,
    hasMore: false,
  };
  assert.equal(validateHistoryPage(page), page);
  assert.throws(
    () => validateHistoryPage({ ...page, hasMore: true }),
    /HISTORY_PAGE_INVALID/,
  );
  assert.throws(
    () => validateHistoryPage({ ...page, totalCount: -1 }),
    /HISTORY_PAGE_INVALID/,
  );
});

test("prepending earlier messages preserves the visible reading anchor", () => {
  assert.equal(
    preservePrependScroll({ scrollTop: 120, scrollHeight: 800 }, 1120),
    440,
  );
  assert.equal(preservePrependScroll(null, 100), 100);
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
