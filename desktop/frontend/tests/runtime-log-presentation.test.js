import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  applyViewerSnapshot,
  collapseViewerRecords,
  validateViewerBootstrap,
  validateViewerSnapshot,
  viewerCopyText,
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
    schemaVersion: 1,
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
    () => validateViewerBootstrap({ schemaVersion: 1, themeTokens: {}, snapshot: {}, extra: true }),
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

test("software and TTS scopes preserve main-window grouping semantics", () => {
  const records = [
    record(1),
    record(2, { scopes: ["software", "tts"], category: "TTS", eventCode: "tts.synthesis.finished" }),
    record(3, { scopes: ["tts"], category: "TTS", eventCode: "tts.service.ready" }),
  ];
  assert.deepEqual(viewerScopeCounts(records), { software: 2, tts: 2 });
  assert.deepEqual(
    collapseViewerRecords(records, "software").map(({ record: item }) => item.sequence),
    [1, 2],
  );
  assert.deepEqual(
    collapseViewerRecords(records, "tts").map(({ record: item }) => item.sequence),
    [2, 3],
  );
});

test("consecutive duplicate rows collapse and copied errors retain support details", () => {
  const error = {
    severity: "error",
    category: "API",
    eventCode: "api.request.failed",
    message: "模型请求失败",
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
  assert.match(copied, /事件代码：api\.request\.failed/);
  assert.match(copied, /关联编号：op:12345678/);
  assert.match(copied, /连续重复：2 次/);
  assert.ok(copied.indexOf("诊断：") < copied.indexOf("错误码："));
  assert.ok(copied.indexOf("错误码：") < copied.indexOf("关联编号："));
});

test("runtime log entrypoint is a module and styles honor reduced motion", () => {
  const html = readFileSync(new URL("../runtime-log/index.html", import.meta.url), "utf8");
  const css = readFileSync(new URL("../runtime-log/styles.css", import.meta.url), "utf8");
  const runtimeLogJs = readFileSync(new URL("../runtime-log/runtime-log.js", import.meta.url), "utf8");
  assert.match(html, /<script type="module" src="\.\/runtime-log\.js"><\/script>/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
  assert.doesNotMatch(css, /\.record-signal|grid-template-columns:\s*4px|grid-column:\s*2/);
  assert.doesNotMatch(runtimeLogJs, /record-signal/);
  assert.doesNotMatch(css, /(?:linear|radial|conic)-gradient\s*\(/i);
});

test("runtime log removes decorative signals and theme-styles auto scroll", () => {
  const html = readFileSync(new URL("../runtime-log/index.html", import.meta.url), "utf8");
  const css = readFileSync(new URL("../runtime-log/styles.css", import.meta.url), "utf8");
  const runtimeLogJs = readFileSync(new URL("../runtime-log/runtime-log.js", import.meta.url), "utf8");

  assert.doesNotMatch(html, /log-mark|live-state|live-signal|live-text|record-signal/);
  assert.doesNotMatch(css, /\.log-mark|\.live-state|\.live-signal|\.record-signal/);
  assert.doesNotMatch(runtimeLogJs, /liveSignal|liveText|setConnected|record-signal/);
  assert.match(
    html,
    /<input id="auto-scroll" type="checkbox" checked \/>\s*<span class="auto-scroll-track"/,
  );
  assert.match(css, /\.auto-scroll-control input\s*\{[\s\S]*?appearance:\s*none/);
  assert.match(css, /input:checked \+ \.auto-scroll-track/);
  assert.match(css, /input:focus-visible \+ \.auto-scroll-track/);
});

test("runtime log text cannot be dragged into native text selection", () => {
  const css = readFileSync(new URL("../runtime-log/styles.css", import.meta.url), "utf8");
  const bodyRule = css.match(/\nbody\s*\{[\s\S]*?\}/)?.[0] || "";

  assert.match(bodyRule, /-webkit-user-select:\s*none/);
  assert.match(bodyRule, /user-select:\s*none/);
  assert.doesNotMatch(css, /user-select:\s*text/);
});

test("runtime log suppresses browser context menus and installs the shared devtools guard", () => {
  const runtimeLogJs = readFileSync(new URL("../runtime-log/runtime-log.js", import.meta.url), "utf8");

  assert.match(runtimeLogJs, /addEventListener\("contextmenu",[\s\S]*?preventDefault\(\)/);
  assert.match(runtimeLogJs, /import \{ installDevtoolsShortcutGuard \}/);
  assert.match(runtimeLogJs, /installDevtoolsShortcutGuard\(\);/);
});

test("runtime log reveals only after theme bootstrap and runtime fonts settle", () => {
  const runtimeLogJs = readFileSync(new URL("../runtime-log/runtime-log.js", import.meta.url), "utf8");

  assert.match(runtimeLogJs, /const runtimeFontsReady = waitForRuntimeFonts\(\)/);
  assert.match(
    runtimeLogJs,
    /applyTheme\(result\.themeTokens\);\s*await revealInitialWindow\(\);/,
  );
  assert.match(runtimeLogJs, /invoke\("reveal_runtime_log_viewer"\)/);
});
