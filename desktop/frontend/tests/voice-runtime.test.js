import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { createVoiceController, exactVoiceSnapshot } from "../settings/voice-runtime.js";

const voiceSource = readFileSync(new URL("../settings/voice-runtime.js", import.meta.url), "utf8");
const settingsEntry = readFileSync(new URL("../settings/settings.js", import.meta.url), "utf8");

function field(overrides = {}) {
  return {
    key: "timeoutSeconds", label: "超时", type: "integer", default: 60, description: "",
    options: [], minimum: 1, maximum: 300, step: 1, required: false, readonly: false,
    copyable: false, restartRequired: false, value: 60, ...overrides,
  };
}

function snapshot(overrides = {}) {
  return {
    schemaVersion: 2,
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
  };
}

function fixture() {
  const controls = Object.fromEntries([
    "ttsCharacterLabel", "ttsEnabled", "ttsProvider", "ttsProviderSettings", "ttsResourceCard",
  ].map((id) => [id, element()]));
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

test("voice settings accept unknown Provider IDs and expose no built-in ID branches", () => {
  const value = snapshot();
  assert.deepEqual(exactVoiceSnapshot(value), value);
  assert.doesNotMatch(voiceSource, /gpt-sovits|genie-tts/);
  assert.throws(() => exactVoiceSnapshot({ ...value, privatePath: "D:/secret" }), /INVALID/);
});

test("voice shell identifies the character and renders Provider settings dynamically", () => {
  const { controls, document, created } = fixture();
  const controller = createVoiceController({ document, invoke: async () => {} });

  controller.initialize(snapshot());

  assert.equal(controls.ttsCharacterLabel.textContent, "正在配置角色：Alpha");
  assert.equal(controls.ttsProvider.value, "com.example.neural-voice");
  assert.equal(controls.ttsProvider.children.length, 2);
  assert.equal(controls.ttsProviderSettings.children.length, 1);
  assert.equal(created.some((item) => item.tagName === "input" && item.value === "60"), true);
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
    /Provider 配置已保存，但角色语音选择未保存/,
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
    /部分 Provider 配置已保存，但后续 Provider 配置和角色语音选择未保存/,
  );

  assert.match(statuses.at(-1)[0], /后续 Provider 配置/);
  assert.equal(statuses.at(-1)[1], "error");
});

test("Runtime v2 legacy TTS handlers remain fail-closed while the capability shell owns Voice", () => {
  assert.match(settingsEntry, /function syncTtsState\(\) \{\s*if \(runtimeSettingsHost\) return;/);
  assert.match(settingsEntry, /async function testTtsSettings\(\) \{\s*if \(runtimeSettingsHost\) return;/);
  assert.match(settingsEntry, /function handleTtsProviderChange\(\) \{\s*if \(runtimeSettingsHost\) return;/);
});
