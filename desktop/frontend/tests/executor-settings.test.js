import assert from "node:assert/strict";
import test from "node:test";
import { createExecutorSettingsController } from "../settings/executor-settings.js";

function snapshot(overrides = {}) {
  return {
    schema_version: 1, window_generation: 3, core_generation_id: "core-test",
    selected_service_key: "", applied_service_key: "", state: "ready", reason_code: "READY",
    candidates: [
      { serviceKey: "", displayName: "默认 Assistant", pluginId: "" },
      { serviceKey: "test.rules", displayName: "规则回复", pluginId: "test" },
    ],
    ...overrides,
  };
}

function fixture(invoke) {
  const listeners = new Map();
  const select = {
    value: "", children: [],
    addEventListener(name, callback) { listeners.set(name, callback); },
    removeEventListener(name) { listeners.delete(name); },
    replaceChildren(...children) { this.children = children; },
    choose(value) { this.value = value; listeners.get("change")(); },
  };
  const retry = {
    addEventListener(name, callback) { listeners.set(`retry:${name}`, callback); },
    removeEventListener(name) { listeners.delete(`retry:${name}`); },
    click() { return listeners.get("retry:click")(); },
  };
  const controls = { chatExecutor: select, chatExecutorStatus: {}, chatExecutorRetry: retry, executorSettings: { hidden: true } };
  let timer = null;
  const controller = createExecutorSettingsController({
    document: {
      getElementById: (id) => controls[id],
      createElement: () => ({}),
    },
    invoke,
    setTimer(callback) { timer = callback; return 1; },
    clearTimer() { timer = null; },
  });
  return { controller, select, retry, status: controls.chatExecutorStatus, get timer() { return timer; } };
}

test("executor selection saves independently and distinguishes pending from active", async () => {
  const calls = [];
  let current = snapshot();
  const view = fixture(async (name, payload) => {
    calls.push([name, payload]);
    if (name === "settings_executor_save") current = snapshot({ selected_service_key: payload.serviceKey, state: "pending" });
    return current;
  });
  await view.controller.initialize();
  view.select.choose("test.rules");
  assert.equal(view.controller.isDirty(), true);
  await view.controller.save();
  assert.equal(view.controller.isDirty(), false);
  assert.deepEqual(calls[1], ["settings_executor_save", {
    windowGeneration: 3, coreGenerationId: "core-test", serviceKey: "test.rules",
  }]);
  assert.match(view.status.textContent, /本轮结束后切换/);
  assert.equal(typeof view.timer, "function");
  current = snapshot({ selected_service_key: "test.rules", applied_service_key: "test.rules" });
  await view.controller.refreshCurrent();
  assert.equal(view.status.textContent, "已生效");
  assert.equal(view.timer, null);
  assert.ok(calls.every(([name]) => name.startsWith("settings_executor_")));
  view.controller.dispose();
});

test("a missing chosen plugin remains selected and can be replaced by the default", async () => {
  const view = fixture(async () => snapshot({
    selected_service_key: "missing.plugin", applied_service_key: null,
    state: "unavailable", reason_code: "EXECUTOR_UNAVAILABLE", candidates: [snapshot().candidates[0]],
  }));
  await view.controller.initialize();
  assert.equal(view.select.value, "missing.plugin");
  assert.equal(view.select.children.at(-1).disabled, true);
  assert.match(view.status.textContent, /所选插件不可用/);
  view.select.choose("");
  assert.equal(view.controller.isDirty(), true);
  view.controller.discard();
  assert.equal(view.select.value, "missing.plugin");
  view.controller.dispose();
});

test("failed saves keep the draft, and background refresh never erases an edit made in flight", async () => {
  let resolveRefresh;
  let reads = 0;
  const view = fixture(async (name) => {
    if (name === "settings_executor_save") throw new Error("CONFIG_SAVE_FAILED");
    if (reads++ === 0) return snapshot();
    return new Promise((resolve) => { resolveRefresh = resolve; });
  });
  await view.controller.initialize();
  const refresh = view.controller.refreshCurrent();
  view.select.choose("test.rules");
  resolveRefresh(snapshot());
  await refresh;
  assert.equal(view.select.value, "test.rules");
  await assert.rejects(view.controller.save(), /CONFIG_SAVE_FAILED/);
  assert.equal(view.controller.isDirty(), true);
  view.controller.dispose();
});

test("retry reapplies the same saved executor after its process is replaced", async () => {
  let saved = null;
  const view = fixture(async (name, payload) => {
    if (name === "settings_executor_save") {
      saved = payload;
      return snapshot({ selected_service_key: "test.rules", applied_service_key: "test.rules", state: "pending" });
    }
    return snapshot({ selected_service_key: "test.rules", applied_service_key: "test.rules", state: "unavailable", reason_code: "EXECUTOR_BINDING_EXPIRED" });
  });
  await view.controller.initialize();
  assert.equal(view.controller.isDirty(), false);
  assert.equal(view.retry.hidden, false);
  assert.equal(view.retry.disabled, false);
  await view.retry.click();
  assert.equal(saved.serviceKey, "test.rules");
  assert.match(view.status.textContent, /本轮结束后切换/);
  assert.equal(view.retry.hidden, true);
  view.controller.dispose();
});
