import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  createProviderModelController,
  validateProviderModelSnapshot,
} from "../settings/provider-model-runtime.js";

const settingsEntry = readFileSync(new URL("../settings/settings.js", import.meta.url), "utf8");
const deepSeekIcon = readFileSync(
  new URL("../settings/assets/providers/deepseek.svg", import.meta.url),
  "utf8",
);

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
    model_slots: {
      chat: { profile_id: "fixture", model: "fixture-model" },
      vision_chat: { profile_id: "", model: "" },
    },
    settings: { timeout_seconds: 30, temperature: null, top_p: null, max_tokens: null },
    setup_complete: true,
    change_plans: ["core_restart_required"],
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
});

test("provider controller binds save and probes to injected window/core identity", async () => {
  let draft = { providers: [], model_slots: { chat: {}, vision_chat: {} }, settings: {} };
  const calls = [];
  const controller = createProviderModelController({
    invoke: async (command, args) => {
      calls.push([command, args]);
      if (command === "settings_provider_model_probe") return { models: ["a"] };
      if (command === "settings_provider_model_get") {
        return { ...snapshot(), core_generation_id: "generation-b" };
      }
      return { saved: true, change_plan: "core_restart_required" };
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
  assert.equal(calls[2][1].coreGenerationId, "generation-b");
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

test("deleted provider selections fall back to a real remaining model", () => {
  const resolveSource = settingsEntry.match(
    /function resolveModelOptions\(models, selectedModel, preserveMissing\) \{[\s\S]*?\n\}/,
  )?.[0];
  assert.ok(resolveSource);
  const resolveModelOptions = Function(`return (${resolveSource})`)();
  assert.deepEqual(
    resolveModelOptions(["gpt-5.6-sol"], "removed-provider-model", false),
    { options: ["gpt-5.6-sol"], value: "gpt-5.6-sol" },
  );
  assert.deepEqual(
    resolveModelOptions(["gpt-5.6-sol"], "removed-provider-model", true),
    {
      options: ["gpt-5.6-sol", "removed-provider-model"],
      value: "removed-provider-model",
    },
  );
});

test("programmatic settings navigation synchronizes the native hidden state", () => {
  const showPage = settingsEntry.match(/function showPage\(page\) \{[\s\S]*?\n\}/)?.[0] || "";
  assert.match(showPage, /element\.hidden\s*=\s*key !== page/);
});

test("DeepSeek provider preset references a packaged SVG icon", () => {
  assert.match(settingsEntry, /iconUrl:\s*"\.\/assets\/providers\/deepseek\.svg"/);
  assert.match(deepSeekIcon, /<title>DeepSeek<\/title>/);
  assert.match(deepSeekIcon, /fill="#4D6BFE"/);
});
