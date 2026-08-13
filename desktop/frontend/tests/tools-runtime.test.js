import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  createToolsController,
  validateToolsSnapshot,
} from "../settings/tools-runtime.js";

function snapshot(coreGenerationId = "generation-a", overrides = {}) {
  return {
    schemaVersion: 1,
    runtimeLimits: {
      maxAgentStepsPerTurn: 4,
      maxToolCallsPerStep: 3,
      maxToolCallsPerTurn: 8,
    },
    confirmationPolicy: "risk_based",
    windowGeneration: 7,
    coreGenerationId,
    ...overrides,
  };
}

function control() {
  const listeners = {};
  return {
    value: "",
    min: "",
    max: "",
    disabled: false,
    addEventListener(name, listener) { listeners[name] = listener; },
    fire(name) { listeners[name]?.(); },
  };
}

function fixture() {
  const controls = {
    agentSteps: control(),
    toolCallsPerStep: control(),
    toolCallsPerTurn: control(),
  };
  return {
    controls,
    document: { getElementById: (id) => controls[id] },
  };
}

test("WP-4-02 Tools snapshots are exact, bounded, and generation scoped", () => {
  assert.equal(validateToolsSnapshot(snapshot()).coreGenerationId, "generation-a");
  assert.throws(() => validateToolsSnapshot({ ...snapshot(), arguments: {} }));
  assert.throws(() => validateToolsSnapshot(snapshot("", {})));
  assert.throws(() => validateToolsSnapshot(snapshot("generation-a", {
    runtimeLimits: {
      maxAgentStepsPerTurn: 4,
      maxToolCallsPerStep: 9,
      maxToolCallsPerTurn: 8,
    },
  })), /整轮工具数/);
});

test("WP-4-02 Tools save sends only bound identity and an exact draft then rebinds", async () => {
  const { controls, document } = fixture();
  const calls = [];
  let restarted = false;
  const controller = createToolsController({
    document,
    invoke: async (command, args) => {
      calls.push([command, args]);
      if (command === "settings_tools_save") {
        restarted = true;
        return { changePlan: "core_restart_required" };
      }
      if (command === "settings_tools_get" && restarted) return snapshot("generation-b", {
        runtimeLimits: {
          maxAgentStepsPerTurn: 6,
          maxToolCallsPerStep: 4,
          maxToolCallsPerTurn: 9,
        },
        confirmationPolicy: "confirm_writes",
      });
      throw new Error("unexpected call");
    },
    onDirty: () => {},
    wait: async () => {},
  });
  controller.initialize(snapshot());
  controls.agentSteps.value = "6";
  controls.toolCallsPerStep.value = "4";
  controls.toolCallsPerTurn.value = "9";
  assert.equal(controller.isDirty(), true);

  await controller.save();

  assert.deepEqual(calls[0], ["settings_tools_save", {
    windowGeneration: 7,
    coreGenerationId: "generation-a",
    settings: {
      runtimeLimits: {
        maxAgentStepsPerTurn: 6,
        maxToolCallsPerStep: 4,
        maxToolCallsPerTurn: 9,
      },
      confirmationPolicy: "risk_based",
    },
  }]);
  assert.deepEqual(calls[1], ["settings_tools_get", undefined]);
  assert.equal(controller.isDirty(), false);
});

test("WP-4-02 failed Tools save keeps the draft and committed discard baseline", async () => {
  const { controls, document } = fixture();
  const controller = createToolsController({
    document,
    invoke: async () => { throw new Error("CONFIG_SAVE_FAILED"); },
    onDirty: () => {},
  });
  controller.initialize(snapshot());
  controls.toolCallsPerTurn.value = "12";
  controls.toolCallsPerTurn.fire("input");
  assert.equal(controller.isDirty(), true);
  await assert.rejects(() => controller.save(), /CONFIG_SAVE_FAILED/);
  assert.equal(controls.toolCallsPerTurn.value, "12");
  assert.equal(controller.isDirty(), true);
  controller.discard();
  assert.equal(controls.toolCallsPerTurn.value, "8");
  assert.equal(controller.isDirty(), false);
});

test("WP-4-02 keeps Tools scoped while WP-4-03 owns desktop MCP", () => {
  const index = readFileSync(new URL("../settings/index.html", import.meta.url), "utf8");
  const settings = readFileSync(new URL("../settings/settings.js", import.meta.url), "utf8");
  const native = readFileSync(new URL("../../src-tauri/src/main.rs", import.meta.url), "utf8");
  const manifest = readFileSync(new URL("../../src-tauri/src/product_shell.rs", import.meta.url), "utf8");

  assert.match(index, /id="agentSteps"[^>]*data-settings-feature="tools\.runtime_limits"/);
  assert.doesNotMatch(index, /id="toolConfirmationPolicy"/);
  assert.match(index, /id="windowsMcp"[^>]*data-settings-feature="tools\.windows_mcp"/);
  assert.match(settings, /invoke\("settings_tools_get"\)/);
  assert.match(settings, /runtimeToolsController\.save\(\)/);
  assert.match(native, /async fn settings_tools_get/);
  assert.match(native, /async fn settings_tools_save/);
  assert.match(manifest, /"tools\.windows_mcp"\.to_string\(\), "available"/);
  assert.doesNotMatch(native, /confirm_action|confirm_tool_action/);
});
