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

test("history projection keeps old left right grouping and current subtitle language", () => {
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

  assert.deepEqual(projected.map(({ role, align, content, showMeta }) => ({
    role, align, content, showMeta,
  })), [
    { role: "human", align: "right", content: "你好", showMeta: true },
    { role: "assistant", align: "left", content: "我回来了", showMeta: true },
    { role: "assistant", align: "left", content: "嗯", showMeta: false },
    { role: "assistant", align: "left", content: "我在这里", showMeta: false },
  ]);
  assert.equal(projected[1].metaText, "桜 · 2026/08/29 12:00:00");
});

test("observations and system facts become centered plain-text records", () => {
  const unsafe = "<script>alert('x')</script>\n视觉摘要";
  const projected = projectHistoryEntries([
    entry("observation", { text: unsafe }, { origin: "manual_screen" }),
    entry("system", { text: "已确认的关系事实" }, { entryId: "entry-system", origin: "host" }),
  ], { formatTime: () => "now" });

  assert.equal(projected[0].align, "center");
  assert.equal(projected[0].roleName, "屏幕记录");
  assert.equal(projected[0].content, unsafe);
  assert.equal(projected[1].roleName, "系统记录");
  assert.equal("visualId" in projected[0], false);
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
  assert.equal(projected[0].roleName, "屏幕观察");
  assert.equal(projected[0].content, "刚才留意了一下屏幕状态。");
  assert.equal(
    projected[0].detailsContent,
    "画面摘要：用户正在检查串口监控。\n关键元素：监控主界面",
  );
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
