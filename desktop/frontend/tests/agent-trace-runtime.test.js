import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  createAgentTraceController,
  validateAgentTraceSnapshot,
} from "../settings/agent-trace-runtime.js";

function snapshot(coreGenerationId = "generation-a", enabled = true) {
  return {
    schemaVersion: 1,
    enabled,
    windowGeneration: 7,
    coreGenerationId,
  };
}

function fixture() {
  const listeners = {};
  const control = {
    checked: false,
    addEventListener(name, callback) { listeners[name] = callback; },
    fire(name) { listeners[name]?.(); },
  };
  return {
    control,
    document: { getElementById: (id) => (id === "agentTraceEnabled" ? control : null) },
  };
}

test("WP-4L-02 Agent trace snapshot is exact and generation scoped", () => {
  assert.equal(validateAgentTraceSnapshot(snapshot()).enabled, true);
  assert.throws(() => validateAgentTraceSnapshot({ ...snapshot(), path: "private" }));
  assert.throws(() => validateAgentTraceSnapshot({ ...snapshot(), enabled: "yes" }));
  assert.throws(() => validateAgentTraceSnapshot(snapshot("")));
});

test("WP-4L-02 saves only the enabled draft and rebinds after Core restart", async () => {
  const { control, document } = fixture();
  const calls = [];
  let restarted = false;
  const controller = createAgentTraceController({
    document,
    invoke: async (command, args) => {
      calls.push([command, args]);
      if (command === "settings_agent_trace_save") {
        restarted = true;
        return { saved: true, changePlan: "core_restart_required" };
      }
      if (command === "settings_agent_trace_get" && restarted) return snapshot("generation-b", false);
      throw new Error("unexpected call");
    },
    onDirty: () => {},
    wait: async () => {},
  });
  controller.initialize(snapshot());
  control.checked = false;
  control.fire("change");
  assert.equal(controller.isDirty(), true);
  await controller.save();
  assert.deepEqual(calls[0], ["settings_agent_trace_save", {
    windowGeneration: 7,
    coreGenerationId: "generation-a",
    settings: { enabled: false },
  }]);
  assert.equal(controller.isDirty(), false);
});

test("WP-4L-02 exposes one local-private trace switch", () => {
  const index = readFileSync(new URL("../settings/index.html", import.meta.url), "utf8");
  const settings = readFileSync(new URL("../settings/settings.js", import.meta.url), "utf8");
  const native = readFileSync(new URL("../../src-tauri/src/main.rs", import.meta.url), "utf8");
  const manifest = readFileSync(new URL("../../src-tauri/src/product_shell.rs", import.meta.url), "utf8");
  assert.match(index, /id="agentTraceEnabled"[^>]*data-settings-feature="agent_trace\.enabled"/);
  assert.match(index, /仅保存在本机/);
  assert.match(index, /用户对话、历史、召回记忆和工具内容明文/);
  assert.match(settings, /settings_agent_trace_get/);
  assert.match(native, /async fn settings_agent_trace_save/);
  assert.match(manifest, /"agent_trace\.enabled"\.to_string\(\)/);
});
