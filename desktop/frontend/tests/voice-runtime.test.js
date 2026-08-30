import assert from "node:assert/strict";
import test from "node:test";

import { createVoiceController, exactVoiceSnapshot } from "../settings/voice-runtime.js";

function field(overrides = {}) {
  return {
    key: "timeoutSeconds", label: "超时", type: "integer", default: 60, description: "",
    options: [], minimum: 1, maximum: 300, step: 1, maxLength: null, placement: "row", actionIds: [],
    enabledWhen: null, required: false, readonly: false,
    copyable: false, restartRequired: false, value: 60, ...overrides,
  };
}

function snapshot(overrides = {}) {
  return {
    schemaVersion: 1,
    character: { characterId: "alpha", displayName: "Alpha" },
    selection: {
      configured: true, enabled: true, providerId: "com.example.neural-voice", available: true,
    },
    providers: [
      { providerId: "com.example.neural-voice", label: "Neural Voice", available: true },
      { providerId: "org.demo.graph-voice", label: "Graph Voice", available: false },
    ],
    sections: [{
      pluginId: "com.example.neural-voice",
      sectionId: "runtime",
      title: "Neural Voice Provider",
      reasonCode: "READY",
      fields: [field()],
      values: { timeoutSeconds: 60 },
      actions: [],
      collections: [],
    }],
    windowGeneration: 7,
    coreGenerationId: "generation-a",
    ...overrides,
  };
}

function element(tagName = "div") {
  const listeners = {};
  return {
    tagName,
    children: [],
    checked: false,
    value: "",
    textContent: "",
    disabled: false,
    className: "",
    append(...items) { this.children.push(...items); },
    addEventListener(name, listener) { (listeners[name] ||= []).push(listener); },
    fire(name) { for (const listener of listeners[name] || []) listener(); },
    async fireAsync(name) { for (const listener of listeners[name] || []) await listener(); },
  };
}

function fixture() {
  const controls = Object.fromEntries([
    "page-voice", "voiceSettings", "voiceUnavailable", "ttsEnabled", "ttsProvider", "ttsProviderSettings",
  ].map((id) => [id, element()]));
  controls["page-voice"].dataset = {};
  controls.voiceSettings.hidden = false;
  controls.voiceUnavailable.hidden = true;
  const created = [];
  return {
    controls,
    created,
    document: {
      getElementById: (id) => controls[id],
      createElement: (tagName) => {
        const item = element(tagName);
        created.push(item);
        return item;
      },
    },
  };
}

test("voice settings accept unknown Provider IDs and reject private fields", () => {
  const value = snapshot();
  assert.deepEqual(exactVoiceSnapshot(value), value);
  assert.throws(() => exactVoiceSnapshot({ ...value, privatePath: "D:/secret" }), /INVALID/);
  assert.throws(() => exactVoiceSnapshot({ ...value, character: null }), /INVALID/);
});

test("voice shell renders Provider settings dynamically without redundant status summaries", () => {
  const { controls, document, created } = fixture();
  const controller = createVoiceController({ document, invoke: async () => {} });

  controller.initialize(snapshot());

  assert.equal(controls.ttsProvider.value, "com.example.neural-voice");
  assert.equal(controls.ttsProvider.children.length, 2);
  assert.equal(controls.ttsProviderSettings.children.length, 1);
  assert.equal(created.some((item) => item.tagName === "input" && item.value === "60"), true);
  assert.equal(controller.isDirty(), false);
});

test("voice shell keeps Provider settings editable without a current character", async () => {
  const { controls, document, created } = fixture();
  const calls = [];
  const withoutCharacter = snapshot({ character: null, selection: null });
  const controller = createVoiceController({
    document,
    invoke: async (command, args) => {
      calls.push([command, args]);
      if (command === "settings_voice_save") {
        return {
          snapshot: withoutCharacter,
          applicationState: "applied",
          saveState: "complete",
          savedSections: [{
            pluginId: "com.example.neural-voice", sectionId: "runtime",
          }],
          selectionSaved: false,
          reasonCode: "CHARACTER_REQUIRED",
        };
      }
      if (command === "settings_voice_get") return withoutCharacter;
      throw new Error(`unexpected ${command}`);
    },
  });

  controller.initialize(withoutCharacter);

  assert.deepEqual(exactVoiceSnapshot(withoutCharacter), withoutCharacter);
  assert.equal(controls.voiceSettings.hidden, false);
  assert.equal(controls.voiceUnavailable.hidden, true);
  assert.equal(controls.ttsEnabled.disabled, true);
  assert.equal(controls.ttsProvider.disabled, false);
  assert.equal(controls.ttsProvider.value, "com.example.neural-voice");
  assert.equal(created.some((item) => item.textContent.includes("尚未选择角色")
    && item.hidden === false), true);

  const timeout = created.find((item) => item.tagName === "input" && item.value === "60");
  timeout.value = "90";
  timeout.fire("input");
  assert.equal(controller.isDirty(), true);

  await controller.save();

  assert.deepEqual(calls[0], ["settings_voice_save", {
    windowGeneration: 7,
    coreGenerationId: "generation-a",
    draft: {
      characterId: null,
      enabled: false,
      providerId: null,
      sections: [{
        pluginId: "com.example.neural-voice",
        sectionId: "runtime",
        values: { timeoutSeconds: 90 },
      }],
    },
  }]);
  assert.equal(calls[1][0], "settings_voice_get");
  assert.equal(controller.isDirty(), false);
});

test("voice page shows only the selected engine and keeps advanced drafts while switching", () => {
  const { controls, document, created } = fixture();
  const enhancedSelects = [];
  const refreshedSelects = [];
  const endpointMode = field({
    key: "endpointMode", label: "服务来源", type: "select", default: "managed", value: "managed",
    options: [
      { label: "Sakura 内置（推荐）", value: "managed" },
      { label: "连接已有服务", value: "custom" },
    ],
  });
  const workDir = field({
    key: "workDir", label: "内置服务工作目录", type: "string", default: "", value: "D:\\tts",
    placement: "advanced", options: [], minimum: null, maximum: null, step: null,
    enabledWhen: { field: "endpointMode", equals: "custom" },
  });
  const second = {
    pluginId: "org.demo.graph-voice", sectionId: "runtime", title: "Graph Voice 语音服务",
    reasonCode: "READY", fields: [field()], values: { timeoutSeconds: 60 }, actions: [], collections: [],
  };
  const controller = createVoiceController({
    document,
    invoke: async () => {},
    enhanceSelect: (select) => enhancedSelects.push(select),
    refreshSelect: (select) => refreshedSelects.push(select),
  });
  controller.initialize(snapshot({
    sections: [{
      pluginId: "com.example.neural-voice", sectionId: "runtime", title: "Neural Voice 语音服务",
      reasonCode: "READY", fields: [endpointMode, workDir],
      values: { endpointMode: "managed", workDir: "D:\\tts" }, actions: [], collections: [],
    }, second],
  }));

  const neuralGroup = controls.ttsProviderSettings.children[0];
  const graphGroup = controls.ttsProviderSettings.children[1];
  const modeSelect = created.find((item) => item.tagName === "select"
    && item.children.some((option) => option.value === "custom"));
  const advanced = created.find((item) => item.tagName === "details");
  const conditionalInput = created.find((item) => item.tagName === "input" && item.value === "D:\\tts");
  assert.deepEqual(enhancedSelects, [controls.ttsProvider, modeSelect]);
  assert.equal(refreshedSelects.includes(controls.ttsProvider), true);
  assert.equal(neuralGroup.hidden, false);
  assert.equal(graphGroup.hidden, true);
  assert.equal(advanced.children[0].textContent, "高级设置");
  assert.equal(conditionalInput.disabled, true);

  modeSelect.value = "custom";
  modeSelect.fire("input");
  assert.equal(conditionalInput.disabled, false);

  controls.ttsProvider.value = "org.demo.graph-voice";
  controls.ttsProvider.fire("change");
  assert.equal(neuralGroup.hidden, true);
  assert.equal(graphGroup.hidden, false);
  controls.ttsProvider.value = "com.example.neural-voice";
  controls.ttsProvider.fire("change");
  assert.equal(modeSelect.value, "custom");
  assert.equal(conditionalInput.value, "D:\\tts");
  assert.equal(controller.isDirty(), true);
});

test("disabled TTS Hub skips voice IPC and can recover after the Hub is enabled", async () => {
  const { controls, document, created } = fixture();
  let available = false;
  let calls = 0;
  let availabilityRefreshes = 0;
  let pluginPageOpens = 0;
  const controller = createVoiceController({
    document,
    isAvailable: () => available,
    refreshAvailability: async () => { availabilityRefreshes += 1; available = true; },
    openPlugins: () => { pluginPageOpens += 1; },
    invoke: async (command) => {
      calls += 1;
      assert.equal(command, "settings_voice_get");
      return snapshot();
    },
  });

  assert.equal(await controller.refreshCurrent(), null);
  assert.equal(calls, 0);
  assert.equal(controls.ttsEnabled.disabled, true);
  assert.equal(controls.ttsProvider.disabled, true);
  assert.equal(controls.voiceSettings.hidden, true);
  assert.equal(controls.voiceUnavailable.hidden, false);
  assert.equal(controls["page-voice"].dataset.voiceState, "unavailable");
  assert.equal(created.some((item) => item.textContent === "语音管理暂不可用"), true);
  assert.equal(created.some((item) => item.textContent === "请确认语音插件已安装并启用。"), true);
  assert.equal(controller.isDirty(), false);

  const refresh = created.find((item) => item.textContent === "重新检查");
  const openPlugins = created.find((item) => item.textContent === "前往插件页");
  openPlugins.fire("click");
  assert.equal(pluginPageOpens, 1);
  await refresh.fireAsync("click");
  assert.equal(availabilityRefreshes, 1);
  assert.equal(calls, 1);
  assert.equal(controls.voiceSettings.hidden, false);
  assert.equal(controls.voiceUnavailable.hidden, true);
  assert.equal(controls["page-voice"].dataset.voiceState, "available");
  assert.equal(controls.ttsEnabled.disabled, false);
  assert.equal(controls.ttsProvider.disabled, false);
  assert.equal(controller.isDirty(), false);
});

test("enabled TTS Hub without an enabled voice engine shows the page-level unavailable state", async () => {
  const { controls, document, created } = fixture();
  const controller = createVoiceController({
    document,
    invoke: async () => snapshot({
      selection: { configured: false, enabled: false, providerId: null, available: false },
      providers: [],
      sections: [],
    }),
  });

  assert.equal(await controller.refreshCurrent(), null);
  assert.equal(controls.voiceSettings.hidden, true);
  assert.equal(controls.voiceUnavailable.hidden, false);
  assert.equal(created.some((item) => item.textContent === "语音管理暂不可用"), true);
  assert.equal(controller.isDirty(), false);
});

test("voice save applies character selection locally and submits only changed Provider sections", async () => {
  const { controls, document, created } = fixture();
  const calls = [];
  const controller = createVoiceController({
    document,
    invoke: async (command, args) => {
      calls.push([command, args]);
      if (command === "settings_voice_save") {
        return {
          applicationState: "restart_required",
          saveState: "complete",
          savedSections: [{
            pluginId: "com.example.neural-voice", sectionId: "runtime",
          }],
          selectionSaved: true,
          reasonCode: "READY",
          snapshot: {},
        };
      }
      if (command === "settings_voice_get") {
        return snapshot({
          sections: [{
            ...snapshot().sections[0],
            reasonCode: "CONFIG_RELOAD_REQUIRED",
            fields: [field({ value: 90 })],
            values: { timeoutSeconds: 90 },
            actions: [{
              actionId: "sakura.reload", label: "重新加载插件",
              description: "应用配置", danger: false,
            }],
          }],
        });
      }
      throw new Error(`unexpected ${command}`);
    },
  });
  controller.initialize(snapshot());
  const timeout = created.find((item) => item.tagName === "input" && item.value === "60");
  timeout.value = "90";
  timeout.fire("input");
  controls.ttsEnabled.checked = false;
  controls.ttsEnabled.fire("change");

  await controller.save();

  assert.deepEqual(calls[0], ["settings_voice_save", {
    windowGeneration: 7,
    coreGenerationId: "generation-a",
    draft: {
      characterId: "alpha",
      enabled: false,
      providerId: "com.example.neural-voice",
      sections: [{
        pluginId: "com.example.neural-voice",
        sectionId: "runtime",
        values: { timeoutSeconds: 90 },
      }],
    },
  }]);
  assert.equal(calls[1][0], "settings_voice_get");
  assert.equal(controller.isDirty(), false);
});

test("voice partial save refreshes actual state and remains an explicit failure", async () => {
  const { controls, document, created } = fixture();
  const calls = [];
  const statuses = [];
  const controller = createVoiceController({
    document,
    onStatus: (...args) => statuses.push(args),
    invoke: async (command, args) => {
      calls.push([command, args]);
      if (command === "settings_voice_save") {
        return {
          applicationState: "restart_required",
          saveState: "partial",
          savedSections: [{
            pluginId: "com.example.neural-voice", sectionId: "runtime",
          }],
          selectionSaved: false,
          reasonCode: "TTS_SELECTION_SAVE_FAILED",
          snapshot: {},
        };
      }
      if (command === "settings_voice_get") {
        return snapshot({
          selection: {
            configured: true, enabled: true,
            providerId: "com.example.neural-voice", available: true,
          },
          sections: [{
            ...snapshot().sections[0],
            reasonCode: "CONFIG_RELOAD_REQUIRED",
            fields: [field({ value: 90 })],
            values: { timeoutSeconds: 90 },
          }],
        });
      }
      throw new Error(`unexpected ${command}`);
    },
  });
  controller.initialize(snapshot());
  const timeout = created.find((item) => item.tagName === "input" && item.value === "60");
  timeout.value = "90";
  controls.ttsEnabled.checked = false;

  await assert.rejects(
    controller.save(),
    /语音引擎配置已保存，但角色语音选择未保存/,
  );

  assert.equal(calls[0][0], "settings_voice_save");
  assert.equal(calls[1][0], "settings_voice_get");
  assert.match(statuses.at(-1)[0], /页面已刷新为实际状态/);
  assert.equal(statuses.at(-1)[1], "error");
  assert.equal(controller.isDirty(), false);
});

test("voice partial save identifies a later Provider section failure", async () => {
  const { document } = fixture();
  const statuses = [];
  const controller = createVoiceController({
    document,
    onStatus: (...args) => statuses.push(args),
    invoke: async (command) => {
      if (command === "settings_voice_save") {
        return {
          applicationState: "restart_required",
          saveState: "partial",
          savedSections: [{
            pluginId: "com.example.neural-voice", sectionId: "runtime",
          }],
          selectionSaved: false,
          reasonCode: "TTS_PROVIDER_SETTINGS_SAVE_FAILED",
          snapshot: {},
        };
      }
      if (command === "settings_voice_get") return snapshot();
      throw new Error(`unexpected ${command}`);
    },
  });
  controller.initialize(snapshot());

  await assert.rejects(
    controller.save(),
    /部分语音引擎配置已保存，但后续引擎配置和角色语音选择未保存/,
  );

  assert.match(statuses.at(-1)[0], /后续引擎配置/);
  assert.equal(statuses.at(-1)[1], "error");
});
