import assert from "node:assert/strict";
import test from "node:test";

import {
  applyViewerSnapshot,
  collapseViewerRecords,
  filterViewerRecords,
  validateViewerBootstrap,
  validateViewerSnapshot,
  viewerCopyText,
  viewerInlineSummary,
  viewerItemKey,
  viewerProblemCount,
  viewerScopeCounts,
} from "../runtime-log/runtime-log-presentation.js";

function record(sequence, overrides = {}) {
  return {
    sequence,
    timestamp: "12:34:56",
    scopes: ["software"],
    severity: "info",
    category: "APP",
    eventCode: "shell.started",
    message: "Sakura 已启动",
    details: [],
    ...overrides,
  };
}

function snapshot(records, overrides = {}) {
  return {
    schemaVersion: 2,
    runId: "run-a",
    latestSequence: records.at(-1)?.sequence || 0,
    resetRequired: false,
    records,
    ...overrides,
  };
}

test("viewer contract rejects uncontrolled fields and invalid scopes", () => {
  assert.equal(validateViewerSnapshot(snapshot([record(1)])).records.length, 1);
  assert.throws(
    () => validateViewerSnapshot(snapshot([{ ...record(1), content: "private" }])),
    /RUNTIME_LOG_VIEWER_RESPONSE_INVALID/,
  );
  assert.throws(
    () => validateViewerSnapshot(snapshot([{ ...record(1), scopes: ["private"] }])),
    /RUNTIME_LOG_VIEWER_RESPONSE_INVALID/,
  );
  assert.throws(
    () => validateViewerBootstrap({ schemaVersion: 2, themeTokens: {}, snapshot: {}, extra: true }),
    /RUNTIME_LOG_VIEWER_RESPONSE_INVALID/,
  );
  assert.throws(
    () => validateViewerSnapshot({ ...snapshot([record(1)]), schemaVersion: 1 }),
    /RUNTIME_LOG_VIEWER_RESPONSE_INVALID/,
  );
  assert.throws(
    () => validateViewerSnapshot(snapshot([record(1, { severity: "warning" })])),
    /RUNTIME_LOG_VIEWER_RESPONSE_INVALID/,
  );
  assert.throws(
    () => validateViewerSnapshot(snapshot([record(1, { description: "info 不应携带说明" })])),
    /RUNTIME_LOG_VIEWER_RESPONSE_INVALID/,
  );
});

test("incremental snapshots append once and reset snapshots replace stale state", () => {
  let state = applyViewerSnapshot(null, snapshot([record(1), record(2)]));
  state = applyViewerSnapshot(state, snapshot([record(3)], { latestSequence: 3 }));
  assert.deepEqual(state.records.map(({ sequence }) => sequence), [1, 2, 3]);

  state = applyViewerSnapshot(state, snapshot([record(9)], {
    runId: "run-b",
    latestSequence: 9,
    resetRequired: true,
  }));
  assert.equal(state.runId, "run-b");
  assert.deepEqual(state.records.map(({ sequence }) => sequence), [9]);
});



test("consecutive duplicate rows collapse and copied errors retain support details", () => {
  const error = {
    severity: "error",
    category: "API",
    eventCode: "api.request.failed",
    message: "模型请求失败",
    description: "模型服务没有接受当前凭据，这次回复无法生成。",
    correlationId: "op:12345678",
    details: [
      { label: "诊断", value: "身份验证失败" },
      { label: "错误码", value: "MODEL_REQUEST_FAILED" },
    ],
  };
  const collapsed = collapseViewerRecords([record(1, error), record(2, error)], "software");
  assert.equal(collapsed.length, 1);
  assert.equal(collapsed[0].repeatCount, 2);
  const copied = viewerCopyText(collapsed[0]);
  assert.match(copied, /\[错误\] 模型请求失败/);
  assert.match(copied, /说明：模型服务没有接受当前凭据，这次回复无法生成。/);
  assert.match(copied, /事件代码：api\.request\.failed/);
  assert.match(copied, /关联编号：op:12345678/);
  assert.match(copied, /连续重复：2 次/);
  assert.ok(copied.indexOf("诊断：") < copied.indexOf("错误码："));
  assert.ok(copied.indexOf("错误码：") < copied.indexOf("关联编号："));
});

test("inline summaries keep useful context and hide support-only diagnostics", () => {
  const summary = viewerInlineSummary(record(1, {
    details: [
      { label: "原因码", value: "TTS_DEVICE_PROBE_FAILED" },
      { label: "阶段", value: "runtime_start" },
      { label: "服务", value: "sakura.tts.gpt-sovits" },
      { label: "耗时", value: "32375 ms" },
      { label: "数据量", value: "741.3 KB" },
      { label: "状态", value: "ready" },
    ],
  }));

  assert.equal(summary, "服务=sakura.tts.gpt-sovits · 耗时=32375 ms · 数据量=741.3 KB");
  assert.doesNotMatch(summary, /原因码|阶段|TTS_DEVICE_PROBE_FAILED|runtime_start/);
});



test("collapsed rows have distinct instance keys even when identical records are non-consecutive", () => {
  const repeated = {
    severity: "warning",
    eventCode: "runtime.degraded",
    message: "运行提醒",
    description: "部分功能没有按预期工作。",
  };
  const collapsed = collapseViewerRecords([
    record(1, repeated),
    record(2),
    record(3, repeated),
  ], "software");

  assert.equal(collapsed[0].collapseKey, collapsed[2].collapseKey);
  assert.notEqual(viewerItemKey(collapsed[0], "software"), viewerItemKey(collapsed[2], "software"));
});
