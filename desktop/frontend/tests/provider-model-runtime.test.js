import assert from "node:assert/strict";
import test from "node:test";

import {
  createProviderModelController,
  validateProviderModelSnapshot,
} from "../settings/provider-model-runtime.js";

function snapshot() {
  return {
    schema_version: 1,
    window_generation: 4,
    core_generation_id: "generation-a",
    providers: [{
      id: "fixture",
      alias: "Fixture",
      base_url: "https://fixture.invalid/v1",
      configured: true,
      models: ["fixture-model"],
    }],
    model_slots: [
      {
        identity: "core:chat",
        ownerType: "core",
        ownerId: "sakura.core",
        slotId: "chat",
        label: "对话模型",
        description: "日常对话",
        modelKind: "chat_completion",
        required: true,
        order: 10,
        reasonCode: "READY",
        selection: {
          profile_id: "fixture",
          model: "fixture-model",
          context_window_tokens: 131072,
        },
      },
      {
        identity: "core:vision_chat",
        ownerType: "core",
        ownerId: "sakura.core",
        slotId: "vision_chat",
        label: "视觉对话模型",
        description: "视觉对话",
        modelKind: "chat_completion",
        required: false,
        order: 20,
        reasonCode: "READY",
        selection: { profile_id: "", model: "" },
      },
    ],
    settings: { timeout_seconds: 30, temperature: null, top_p: null, max_tokens: null },
    setup_complete: true,
    change_plans: ["applied"],
  };
}

function dynamicSnapshot() {
  const current = snapshot();
  return {
    ...current,
    model_slots: [
      ...current.model_slots,
      {
        identity: "plugin:sakura.memory.mem0:curation",
        ownerType: "plugin",
        ownerId: "sakura.memory.mem0",
        slotId: "curation",
        label: "记忆整理模型",
        description: "整理长期记忆",
        modelKind: "chat_completion",
        required: false,
        order: 30,
        reasonCode: "READY",
        selection: { profile_id: "removed", model: "removed-model" },
      },
    ],
  };
}

test("provider snapshot validates identity and rejects credential-shaped response fields", () => {
  assert.equal(validateProviderModelSnapshot(snapshot()).providers[0].configured, true);
  assert.throws(
    () => validateProviderModelSnapshot({ ...snapshot(), api_key: "must-not-return" }),
    /sensitive/,
  );
  assert.throws(
    () => validateProviderModelSnapshot({ ...snapshot(), core_generation_id: "" }),
    /generation/,
  );
  assert.throws(
    () => validateProviderModelSnapshot({
      ...snapshot(),
      model_slots: {
        chat: { profile_id: "fixture", model: "fixture-model" },
        vision_chat: { profile_id: "", model: "" },
      },
    }),
    /model slots/,
  );
});

test("the current schema preserves active plugin slots and unavailable selections without credentials", () => {
  const validated = validateProviderModelSnapshot(dynamicSnapshot());
  assert.equal(validated.model_slots.length, 3);
  assert.equal(validated.model_slots[0].selection.context_window_tokens, 131072);
  assert.equal(validateProviderModelSnapshot({
    ...dynamicSnapshot(),
    model_slots: dynamicSnapshot().model_slots.map((slot, index) => (
      index === 0
        ? { ...slot, selection: { ...slot.selection, context_window_tokens: 1_000_000 } }
        : slot
    )),
  }).model_slots[0].selection.context_window_tokens, 1_000_000);
  assert.deepEqual(validated.model_slots[2].selection, {
    profile_id: "removed",
    model: "removed-model",
  });
  assert.throws(
    () => validateProviderModelSnapshot({
      ...dynamicSnapshot(),
      model_slots: dynamicSnapshot().model_slots.map((slot, index) => (
        index === 2 ? { ...slot, apiKey: "must-not-return" } : slot
      )),
    }),
    /sensitive/,
  );
  assert.throws(
    () => validateProviderModelSnapshot({
      ...dynamicSnapshot(),
      model_slots: dynamicSnapshot().model_slots.map((slot, index) => (
        index === 0
          ? { ...slot, selection: { ...slot.selection, context_window_tokens: 2048 } }
          : slot
      )),
    }),
    /invalid model slot/,
  );
});

test("refreshing Provider settings adds and removes plugin slots from the applied snapshot", async () => {
  const applied = [];
  const withoutPlugin = {
    ...dynamicSnapshot(),
    core_generation_id: "generation-b",
    model_slots: dynamicSnapshot().model_slots.slice(0, 2),
  };
  let next = dynamicSnapshot();
  const controller = createProviderModelController({
    invoke: async () => next,
    readDraft: () => ({ providers: [], model_slots: {}, settings: {} }),
    applySnapshot(value) { applied.push(value.model_slots.map((slot) => slot.identity)); },
    onDirty() {},
    onError(error) { throw error; },
  });
  await controller.initialize(dynamicSnapshot());
  next = withoutPlugin;
  await controller.refreshCurrent();
  assert.deepEqual(applied, [
    ["core:chat", "core:vision_chat", "plugin:sakura.memory.mem0:curation"],
    ["core:chat", "core:vision_chat"],
  ]);
});

test("partial multi-owner save refreshes real state and reports the failed slot", async () => {
  const next = { ...dynamicSnapshot(), core_generation_id: "generation-b" };
  const controller = createProviderModelController({
    invoke: async (command) => {
      if (command === "settings_provider_model_save") {
        return {
          change_plan: "applied",
          save_state: "partial",
          failed_slot: { identity: "plugin:sakura.memory.mem0:curation" },
        };
      }
      return next;
    },
    readDraft: () => ({ providers: [], model_slots: {}, settings: {} }),
    applySnapshot() {},
    onDirty() {},
    onError(error) { throw error; },
  });
  await controller.initialize(dynamicSnapshot());
  await assert.rejects(
    controller.save(),
    /plugin:sakura\.memory\.mem0:curation/,
  );
});

test("hot save refreshes plugin model slots in the same Core generation", async () => {
  let reads = 0;
  const applied = [];
  const controller = createProviderModelController({
    invoke: async (command) => {
      if (command === "settings_provider_model_save") {
        return { change_plan: "applied" };
      }
      assert.equal(command, "settings_provider_model_get");
      reads += 1;
      const current = dynamicSnapshot();
      return {
        ...current,
        core_generation_id: "generation-a",
        model_slots: current.model_slots,
      };
    },
    readDraft: () => ({ providers: [], model_slots: {}, settings: {} }),
    applySnapshot(value) { applied.push(value.model_slots.map((slot) => slot.identity)); },
    onDirty() {},
    onError(error) { throw error; },
  });
  await controller.initialize(dynamicSnapshot());

  await controller.save();

  assert.equal(reads, 1);
  assert.deepEqual(applied, [
    ["core:chat", "core:vision_chat", "plugin:sakura.memory.mem0:curation"],
    ["core:chat", "core:vision_chat", "plugin:sakura.memory.mem0:curation"],
  ]);
});

test("provider controller binds save and probes to injected window/core identity", async () => {
  let draft = { providers: [], model_slots: { chat: {}, vision_chat: {} }, settings: {} };
  const calls = [];
  const controller = createProviderModelController({
    invoke: async (command, args) => {
      calls.push([command, args]);
      if (command === "settings_provider_model_probe") return { models: ["a"] };
      if (command === "settings_provider_model_get") {
        return { ...snapshot(), core_generation_id: "generation-a" };
      }
      return { saved: true, change_plan: "applied" };
    },
    readDraft: () => draft,
    applySnapshot() {},
    onDirty() {},
    onError(error) { throw error; },
  });
  await controller.initialize(snapshot());
  draft = { ...draft, settings: { timeout_seconds: 15 } };
  assert.equal(controller.isDirty(), true);
  await controller.save();
  assert.equal(controller.isDirty(), false);
  await controller.listModels({ profile_id: "fixture" });
  assert.equal(calls[0][1].windowGeneration, 4);
  assert.equal(calls[0][1].coreGenerationId, "generation-a");
  assert.equal(calls[1][0], "settings_provider_model_get");
  assert.equal(calls[2][0], "settings_provider_model_probe");
  assert.equal(calls[2][1].coreGenerationId, "generation-a");
  assert.equal(typeof calls[2][1].operationId, "string");
});

test("closing with an active provider probe sends an identity-bound cancellation", async () => {
  let finishProbe;
  const calls = [];
  const controller = createProviderModelController({
    invoke: async (command, args) => {
      calls.push([command, args]);
      if (command === "settings_provider_model_probe") {
        return new Promise((resolve) => { finishProbe = resolve; });
      }
      return true;
    },
    readDraft: () => ({ providers: [], model_slots: { chat: {}, vision_chat: {} }, settings: {} }),
    applySnapshot() {},
    onDirty() {},
    onError(error) { throw error; },
  });
  await controller.initialize(snapshot());
  const probe = controller.listModels({ profile_id: "fixture" });
  await controller.cancelOperations();
  const cancellation = calls.find(([command]) => command === "settings_provider_model_cancel");
  assert.equal(cancellation[1].windowGeneration, 4);
  assert.equal(cancellation[1].coreGenerationId, "generation-a");
  assert.equal(cancellation[1].operationId, calls[0][1].operationId);
  finishProbe({ models: [] });
  await probe;
});

test("provider controller refreshes only its Core identity after another settings domain restarts", async () => {
  let applied = 0;
  const calls = [];
  const controller = createProviderModelController({
    invoke: async (command, args) => {
      calls.push([command, args]);
      if (command === "settings_provider_model_get") {
        return { ...snapshot(), core_generation_id: "generation-b" };
      }
      if (command === "settings_provider_model_probe") return { models: [] };
      throw new Error("unexpected call");
    },
    readDraft: () => ({ providers: [], model_slots: { chat: {}, vision_chat: {} }, settings: {} }),
    applySnapshot() { applied += 1; },
    onDirty() {},
    onError(error) { throw error; },
  });
  await controller.initialize(snapshot());

  await controller.refreshCurrent();
  await controller.listModels({ profile_id: "fixture" });

  assert.equal(applied, 2);
  assert.equal(calls[1][1].coreGenerationId, "generation-b");
});
