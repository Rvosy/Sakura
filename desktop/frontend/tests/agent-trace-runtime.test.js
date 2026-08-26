import assert from "node:assert/strict";
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

test("WP-4L-02 saves only the enabled draft and hot-applies in place", async () => {
  const { control, document } = fixture();
  const calls = [];
  let restarted = false;
  const controller = createAgentTraceController({
    document,
    invoke: async (command, args) => {
      calls.push([command, args]);
      if (command === "settings_agent_trace_save") {
        restarted = true;
        return { saved: true, changePlan: "applied" };
      }
      if (command === "settings_agent_trace_get" && restarted) return snapshot("generation-a", false);
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
