import assert from "node:assert/strict";
import test from "node:test";
import { errorText } from "../core/error-display.js";
import { createProviderModelController } from "../settings/provider-model-runtime.js";
const ref = { serviceKey: "model.remote", profileId: "p", modelId: "m" };
const snapshot = () => ({ schema_version: 2, window_generation: 4, core_generation_id: "a",
  providers: [{ serviceKey: "model.remote", profiles: [{ profileId: "p", models: [{ modelId: "m" }] }] }],
  model_slots: [{ identity: "core:chat", label: "Chat", required: true, selection: ref }] });

test("generation rebinding preserves a pending draft and routes its save correctly", async () => {
  let draft = { model_slots: { "core:chat": ref } }; const calls = [];
  const controller = createProviderModelController({
    invoke: async (command, args) => { calls.push([command, args]); return command.endsWith("_save") ? { change_plan: "applied" } : { ...snapshot(), core_generation_id: "b" }; },
    readDraft: () => draft, applySnapshot() {}, onDirty() {},
  });
  await controller.initialize(snapshot());
  draft = { model_slots: { "core:chat": { ...ref } }, extra: "draft" };
  controller.rebindIdentity("b"); assert.equal(controller.isDirty(), true);
  await controller.save(); assert.equal(calls[0][1].coreGenerationId, "b"); assert.deepEqual(calls[0][1].draft, draft);
  assert.equal(controller.isDirty(), false);
});

test("disposing prevents an in-flight snapshot from rendering into a closed page", async () => {
  let complete, applied = 0;
  const controller = createProviderModelController({ invoke: () => new Promise(resolve => { complete = resolve; }),
    readDraft: () => ({}), applySnapshot() { applied++; }, onDirty() {} });
  const pending = controller.refreshCurrent(); controller.dispose(); complete(snapshot()); await pending;
  assert.equal(applied, 0);
});

test("an earlier refresh cannot replace a newer snapshot or a rebound generation", async () => {
  const pending = [], applied = [];
  const controller = createProviderModelController({ invoke: () => new Promise(resolve => pending.push(resolve)),
    readDraft: () => ({}), applySnapshot(value) { applied.push(value.core_generation_id); }, onDirty() {} });
  const first = controller.refreshCurrent(), second = controller.refreshCurrent();
  pending[1]({ ...snapshot(), core_generation_id: "new" }); await second;
  pending[0]({ ...snapshot(), core_generation_id: "old" }); await first;
  assert.deepEqual(applied, ["new"]);
  const third = controller.refreshCurrent();
  controller.rebindIdentity("rebound");
  pending[2]({ ...snapshot(), core_generation_id: "stale" }); await third;
  assert.deepEqual(applied, ["new"]);
});

for (const refreshFails of [false, true]) {
  test(`partial model save preserves source diagnostics and draft with refresh failure=${refreshFails}`, async () => {
    const draft = { model_slots: { "core:chat": ref } };
    const controller = createProviderModelController({
      invoke: async command => {
        if (command.endsWith("_save")) return { change_plan: "applied", save_state: "partial", saved_slots: [],
          failed_slot: { identity: "core:chat", diagnostics: { diagnostic: "fixture config write denied", exception_stack: "save_slot:42" } } };
        if (refreshFails) throw new Error("fixture catalog refresh failed");
        return snapshot();
      },
      readDraft: () => draft, applySnapshot() {}, onDirty() {},
    });
    await controller.initialize(snapshot());
    await assert.rejects(controller.save(), error => {
      const text = errorText(error);
      assert.match(text, /fixture config write denied/);
      assert.match(text, /save_slot:42/);
      assert.equal(text.includes("fixture catalog refresh failed"), refreshFails);
      return true;
    });
    assert.deepEqual(draft.model_slots["core:chat"], ref);
  });
}
