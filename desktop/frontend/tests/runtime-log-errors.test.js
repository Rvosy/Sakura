import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";
import { applyViewerSnapshot, validateViewerBootstrap, validateViewerSnapshot } from "../runtime-log/runtime-log-presentation.js";

const source = await readFile(new URL("../runtime-log/runtime-log.js", import.meta.url), "utf8");
const pageSource = source.slice(0, source.indexOf("\nawait bootstrap();"))
  .replace(/^import[\s\S]*?;\r?\n/gm, "");
const snapshot = () => ({ schemaVersion: 3, runId: "run", latestSequence: 0, resetRequired: false, records: [], failedFiles: [] });

function fixture(invoke, writeText = async () => {}) {
  const elements = new Map(), errors = [];
  const document = {
    querySelector(selector) {
      if (!elements.has(selector)) elements.set(selector, {
        textContent: "", disabled: false, dataset: {}, handlers: {},
        addEventListener(name, callback) { this.handlers[name] = callback; },
      });
      return elements.get(selector);
    },
    querySelectorAll: () => [],
  };
  const context = vm.createContext({
    document,
    window: { __TAURI__: { core: { invoke } }, addEventListener() {} },
    navigator: { clipboard: { writeText } },
    createErrorDialog: () => ({ show: value => errors.push(value) }),
    enhanceSelect() {}, installDevtoolsShortcutGuard() {}, applyTheme() {},
    waitForRuntimeFonts: () => Promise.resolve(),
    applyViewerSnapshot, validateViewerBootstrap, validateViewerSnapshot,
  });
  vm.runInContext(pageSource, context);
  vm.runInContext("render = () => {}; pruneViewState = () => {}; scrollToLatest = () => {};", context);
  return { context, elements, errors };
}

test("runtime log startup failure goes to the dialog while the viewer remains reachable", async () => {
  const error = new Error("RUNTIME_LOG_VIEWER_RESPONSE_INVALID: stack");
  const commands = [];
  const f = fixture(async command => {
    commands.push(command);
    if (command === "runtime_log_viewer_bootstrap") throw error;
  });
  await f.context.bootstrap();
  await Promise.resolve();
  assert.equal(f.errors.length, 1);
  assert.equal(f.errors[0].error, error);
  assert.doesNotMatch(f.elements.get("#log-status").textContent, /RUNTIME_LOG_|stack/);
  assert.equal(f.elements.get("#refresh").disabled, false);
  assert.ok(commands.includes("reveal_runtime_log_viewer"));
});

test("continuous poll failures open once, recovery allows a later failure to be reported", async () => {
  let fail = false;
  const error = new Error("read syscall failed");
  const f = fixture(async command => {
    if (command === "runtime_log_viewer_bootstrap") return { schemaVersion: 3, themeTokens: {}, snapshot: snapshot() };
    if (command === "runtime_log_viewer_snapshot") {
      if (fail) throw error;
      return snapshot();
    }
  });
  await f.context.bootstrap();
  fail = true;
  await f.context.poll();
  await f.context.poll();
  assert.equal(f.errors.length, 1);
  assert.equal(f.errors[0].error, error);
  assert.doesNotMatch(f.elements.get("#log-status").textContent, /syscall/);
  fail = false;
  await f.context.poll();
  assert.match(f.elements.get("#log-status").textContent, /恢复/);
  fail = true;
  await f.context.poll();
  assert.equal(f.errors.length, 2);
});

test("invalid polling snapshots preserve displayed records and the recovery cursor", async () => {
  const first = {
    source: "rust", sequence: 1, timestamp: "12:34:56", scopes: ["software"],
    severity: "info", category: "APP", eventCode: "shell.started", message: "已显示的记录", details: [],
  };
  const second = { ...first, sequence: 2, message: "恢复后的记录" };
  let next = {
    ...snapshot(), runId: "invalid-run", latestSequence: 2, resetRequired: true,
    records: [{ ...second, content: "uncontrolled" }],
  };
  const cursors = [];
  const f = fixture(async (command, args) => {
    if (command === "runtime_log_viewer_bootstrap") return {
      schemaVersion: 3, themeTokens: {}, snapshot: { ...snapshot(), latestSequence: 1, records: [first] },
    };
    if (command === "runtime_log_viewer_snapshot") {
      cursors.push(args.afterSequence);
      return next;
    }
  });
  await f.context.bootstrap();
  await f.context.poll();
  assert.deepEqual(vm.runInContext("viewerState.records", f.context), [first]);
  assert.equal(f.errors.length, 1);
  assert.match(f.errors[0].error.message, /RUNTIME_LOG_VIEWER_RESPONSE_INVALID/);

  next = { ...snapshot(), latestSequence: 2, records: [second] };
  await f.context.poll();
  assert.deepEqual(cursors, [1, 1]);
  assert.deepEqual(vm.runInContext("viewerState.records", f.context), [first, second]);
  assert.equal(f.errors.length, 1);
});

test("copy failures show the dialog without replacing the selected log contents", async () => {
  const error = new Error("Clipboard permission denied");
  const f = fixture(async () => {}, async () => { throw error; });
  const copy = f.elements.get("#copy");
  copy.dataset.copyText = "original runtime log diagnostics";
  await copy.handlers.click();
  assert.equal(copy.dataset.copyText, "original runtime log diagnostics");
  assert.equal(f.errors[0].error, error);
  assert.doesNotMatch(f.elements.get("#log-status").textContent, /Clipboard/);
});
