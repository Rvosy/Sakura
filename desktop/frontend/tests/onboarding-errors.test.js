import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";
import { beginLegacyInspection, legacyInspectionProgress } from "../onboarding/legacy-import-state.js";

const source = await readFile(new URL("../onboarding/onboarding.js", import.meta.url), "utf8");
const pageSource = source.slice(0, source.indexOf("\nstart().catch("))
  .replace(/^import[\s\S]*?;\r?\n/gm, "");

function element() {
  return {
    textContent: "", hidden: true, disabled: false, dataset: {}, children: [], handlers: {},
    style: { setProperty() {} }, classList: { add() {}, remove() {} },
    append(...items) { this.children.push(...items); },
    replaceChildren(...items) { this.children = items; },
    addEventListener(name, callback) { this.handlers[name] = callback; },
    removeEventListener() {}, removeAttribute() {}, focus() {},
    querySelector() { return element(); },
  };
}

function fixture(invoke = async () => {}) {
  const elements = new Map(), errors = [];
  const document = {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, element());
      return elements.get(id);
    },
    addEventListener() {}, createElement: element,
  };
  const context = vm.createContext({
    document,
    window: {
      __TAURI__: { core: { invoke } }, addEventListener() {},
      requestAnimationFrame: () => 1, cancelAnimationFrame() {},
      clearTimeout() {}, setTimeout: () => 1,
    },
    createRuntimeDiagnostics: () => ({ invoke }),
    createErrorDialog: () => ({ show: value => errors.push(value), dispose() {} }),
    installDevtoolsShortcutGuard() {}, beginLegacyInspection, legacyInspectionProgress,
  });
  vm.runInContext(pageSource, context);
  return { context, elements, errors };
}

test("first startup failures keep diagnostics in the dialog and re-enable the action", async () => {
  const error = new Error("CORE_START_FAILED: OSError private traceback");
  const f = fixture(async () => { throw error; });
  await f.context.openFirstUseGuide();
  assert.equal(f.elements.get("firstUseButton").disabled, false);
  assert.doesNotMatch(f.elements.get("startupStatus").textContent, /CORE_START_FAILED|traceback/);
  assert.equal(f.errors.length, 1);
  assert.equal(f.errors[0].error, error);
});

test("failed directory inspection restores the selection and opens its original error", async () => {
  const selected = { state: "selected", selectionId: "source-one", sourceLabel: "old Sakura" };
  const error = { code: "LEGACY_SOURCE_UNREADABLE", diagnostic: "PermissionError: source" };
  const f = fixture(async command => {
    if (command === "legacy_import_choose_source") return selected;
    throw error;
  });
  await f.context.chooseLegacySource();
  assert.equal(f.elements.get("migrationStartButton").disabled, true);
  assert.equal(f.elements.get("migrationChooseButton").disabled, false);
  assert.equal(f.errors[0].error, error);
  assert.doesNotMatch(f.elements.get("migrationError").textContent, /LEGACY_|PermissionError/);
});

test("repeated migration failure events open one dialog without putting diagnostics in progress", () => {
  const f = fixture();
  const error = { code: "LEGACY_COPY_FAILED", diagnostic: "PermissionError: copied file" };
  const snapshot = { state: "failed", percent: 42, message: "technical failure traceback", error };
  f.context.renderProgress(snapshot);
  f.context.renderProgress(snapshot);
  assert.equal(f.errors.length, 1);
  assert.equal(f.errors[0].error, error);
  assert.doesNotMatch(f.elements.get("migrationError").textContent, /LEGACY_|PermissionError/);
  assert.doesNotMatch(f.elements.get("migrationStage").textContent, /technical|traceback/);
  assert.equal(f.elements.get("migrationMessage").textContent, "");
});

test("inspection validation keeps an actionable hint and opens technical details on request", () => {
  const f = fixture();
  const issue = { code: "LEGACY_SOURCE_ACTIVE", diagnostic: "legacy process lock path" };
  f.context.renderInspection({ selectionId: "old", inspection: { compatible: false, blockers: [issue] } });
  const item = f.elements.get("migrationIssues").children[0];
  assert.match(item.textContent, /关闭/);
  assert.doesNotMatch(item.textContent, /LEGACY_|lock path/);
  assert.equal(f.errors.length, 0);
  item.children[0].handlers.click();
  assert.equal(f.errors[0].error, issue);
});
