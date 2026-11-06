import test from "node:test";
import assert from "node:assert/strict";
import { trendBuckets } from "../settings/usage-statistics.js";

test("hourly trends preserve individual hours, unknown usage and gaps", () => {
  const rows = trendBuckets({ granularity: "hour", start: "2026-10-28T00:00:00", end: "2026-10-28T03:15:00",
    hourly: [
      { hour: "2026-10-28T00:00:00", calls: 2, measuredCalls: 2, totalTokens: 30 },
      { hour: "2026-10-28T02:00:00", calls: 1, measuredCalls: 0, totalTokens: null },
    ], conversation: { hourly: [{ hour: "2026-10-28T01:00:00", turns: 2 }] } });
  assert.deepEqual(rows.map(row => [row.label, row.total, row.turns]), [
    ["2026-10-28T00:00:00", 30, 0], ["2026-10-28T01:00:00", 0, 2],
    ["2026-10-28T02:00:00", null, 0], ["2026-10-28T03:00:00", 0, 0],
  ]);
});

test("trend preserves provider totals, missing usage and days with only conversations", () => {
  const rows = trendBuckets({ start: "2026-10-27T00:00:00", end: "2026-10-30T12:00:00",
    daily: [
      { day: "2026-10-27", calls: 1, measuredCalls: 1, inputTokens: 100, outputTokens: 20, totalTokens: 150 },
      { day: "2026-10-28", calls: 1, measuredCalls: 0, inputTokens: 30, outputTokens: null, totalTokens: null },
    ], conversation: { daily: [{ day: "2026-10-30", turns: 2 }] } });
  assert.deepEqual(rows.map(row => [row.label, row.total, row.input, row.output, row.turns]), [
    ["2026-10-27", 150, 100, 20, 0], ["2026-10-28", null, 30, null, 0],
    ["2026-10-29", 0, null, null, 0], ["2026-10-30", 0, null, null, 2],
  ]);
});

test("all history includes conversations predating usage and aggregates long ranges by month", () => {
  const rows = trendBuckets({ start: null, end: "2026-10-30T12:00:00",
    daily: [
      { day: "2026-10-29", calls: 2, measuredCalls: 1, totalTokens: 20 },
      { day: "2026-10-30", calls: 1, measuredCalls: 1, totalTokens: 30 },
    ], conversation: { daily: [{ day: "2026-07-01", turns: 2 }] } });
  assert.deepEqual(rows.map(row => [row.label, row.total, row.turns]), [
    ["2026-07", 0, 2], ["2026-08", 0, 0], ["2026-09", 0, 0], ["2026-10", 50, 0],
  ]);
});
