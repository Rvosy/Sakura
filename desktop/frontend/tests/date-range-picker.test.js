import assert from "node:assert/strict";
import test from "node:test";
import { parseDate, presetDateRange } from "../core/date-range-picker.js";

test("calendar date conversion preserves leap days and years below 100", () => {
  assert.equal(parseDate("2024-02-29").getDate(), 29);
  assert.equal(parseDate("0026-01-01").getFullYear(), 26);
});

test("date presets include today and cross year and leap-month boundaries", () => {
  assert.deepEqual(presetDateRange(7, new Date(2026, 0, 3, 23, 59)), {
    startDate: "2025-12-28", endDate: "2026-01-03",
  });
  assert.deepEqual(presetDateRange(7, new Date(2024, 2, 2)), {
    startDate: "2024-02-25", endDate: "2024-03-02",
  });
  assert.deepEqual(presetDateRange(1, new Date(2026, 9, 20)), {
    startDate: "2026-10-20", endDate: "2026-10-20",
  });
});
