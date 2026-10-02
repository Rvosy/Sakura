import assert from "node:assert/strict";
import test from "node:test";
import { createCharacterVisualSettings } from "../settings/character-visual-settings.js";
import { createRuntimeDiagnostics, RUNTIME_DIAGNOSTICS_COMMAND } from "../core/runtime-diagnostics.js";

function harness(invoke, extra = {}) {
  const elements = new Map();
  for (const id of ["visualSelect", "visualStatus", "visualStatusText", "visualPluginAction", "visualConfigure"]) {
    elements.set(id, { value: "", children: [], replaceChildren() { this.children = []; },
      append(child) { this.children.push(child); }, addEventListener() {}, removeEventListener() {} });
  }
  const errors = [];
  const settings = createCharacterVisualSettings({
    document: { getElementById: id => elements.get(id), createElement: () => ({}) },
    invoke, refreshSelect() {}, onDirty() {}, openPlugin() {}, onError: (...args) => errors.push(args), ...extra,
  });
  return { settings, errors, elements };
}
const snapshot = id => ({ characterId: id, resources: [{ id: "portrait", name: "Portrait", reasonCode: "READY" }], defaultResourceId: "portrait" });

test("generation lock defers visual reads until unlock and ignores a retired request", async () => {
  const calls = [];
  let rejectRetired;
  let refreshed;
  const ready = new Promise(resolve => { refreshed = resolve; });
  const env = harness(id => {
    calls.push(id);
    if (calls.length === 1) return new Promise((_, reject) => { rejectRetired = reject; });
    refreshed();
    return snapshot("next");
  });
  const retired = env.settings.refresh("old");
  await Promise.resolve();
  env.settings.sync("next", true);
  await env.settings.refresh();
  assert.equal(calls.length, 1, "no reads while the Core generation is locked");
  rejectRetired("SETTINGS_CORE_UNAVAILABLE");
  await retired;
  assert.deepEqual(env.errors, []);
  env.settings.sync("next", false);
  await ready;
  await env.settings.refresh();
  assert.equal(calls.length, 2, "unlock refreshes once and concurrent readers share it");
  assert.equal(env.elements.get("visualSelect").value, "portrait");
  env.settings.dispose();
});

test("a string IPC failure is reported once while validation failures remain visible", async () => {
  const batches = [];
  let value;
  const diagnostics = createRuntimeDiagnostics({
    invoke: async (command, args) => {
      if (command === RUNTIME_DIAGNOSTICS_COMMAND) { batches.push(...args.entries); return; }
      if (!value) throw "SETTINGS_CORE_UNAVAILABLE";
      return value;
    }, windowObject: null, setTimer: () => 1, clearTimer() {},
  });
  const env = harness(diagnostics.invoke, { reportError: diagnostics.reportError });
  await Promise.all([env.settings.refresh("character"), env.settings.refresh("character")]);
  await diagnostics.flush();
  assert.equal(batches.filter(entry => entry.outcome === "failed").length, 1);
  assert.equal(env.errors.length, 1);
  value = snapshot("wrong-character");
  await Promise.all([env.settings.refresh("character"), env.settings.refresh("character")]);
  await diagnostics.flush();
  assert.equal(batches.filter(entry => entry.code === "CHARACTER_VISUAL_SETTINGS_INVALID").length, 1);
  assert.equal(env.errors.length, 2);
  env.settings.dispose();
});
