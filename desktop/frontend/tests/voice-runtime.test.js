import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  createVoiceController, exactBundleStatus, exactSettings, exactVoiceStatus,
  voiceResourcePresentation,
} from "../settings/voice-runtime.js";

const settingsEntry = readFileSync(new URL("../settings/settings.js", import.meta.url), "utf8");

function settings(overrides = {}) {
  return {
    enabled: true,
    provider: "gpt-sovits",
    apiUrl: "http://127.0.0.1:9880/tts",
    customBaseUrl: "",
    ttsPath: "/tts",
    remoteReferenceRoot: "",
    workDir: "D:/tts",
    pythonPath: "D:/tts/python.exe",
    timeoutSeconds: 60,
    ...overrides,
  };
}

function snapshot(coreGenerationId = "generation-a", overrides = {}) {
  return {
    settings: settings(),
    coreRestartRequired: false,
    windowGeneration: 7,
    coreGenerationId,
    ...overrides,
  };
}

function voiceStatus(coreGenerationId = "generation-a", overrides = {}) {
  return {
    schemaVersion: 1,
    enabled: true,
    selectedProvider: "gpt-sovits",
    providers: [
      { id: "gpt-sovits", label: "GPT-SoVITS", availability: "not_installed" },
      { id: "genie-tts", label: "Genie TTS", availability: "not_installed" },
    ],
    bundles: [],
    runtime: { provider: "gpt-sovits", endpointKind: "managed", state: "ready", errorCode: null, updatedAt: "2026-08-16T12:00:00+00:00" },
    activeTask: null,
    windowGeneration: 7,
    coreGenerationId,
    ...overrides,
  };
}

function control() {
  const listeners = {};
  return {
    checked: false,
    value: "",
    textContent: "",
    disabled: false,
    append() {},
    addEventListener(name, listener) { listeners[name] = listener; },
    fire(name) { listeners[name]?.(); },
  };
}

function fixture() {
  const controls = Object.fromEntries([
    "ttsEnabled", "ttsProvider", "ttsApiUrl", "ttsApiUrlRow",
    "ttsCustomBaseUrl", "ttsCustomBaseUrlRow", "ttsPath", "ttsPathRow",
    "ttsRemoteReferenceRoot", "ttsRemoteReferenceRootRow", "ttsWorkDir", "ttsPythonPath",
    "ttsTimeout", "ttsTestButton", "ttsResourceCard",
  ].map((id) => [id, control()]));
  return {
    controls,
    document: {
      getElementById: (id) => controls[id],
      createElement: () => control(),
    },
  };
}

test("WP-4-05 voice settings DTO is exact and bounded", () => {
  const valid = settings();
  assert.deepEqual(exactSettings(valid), valid);
  assert.throws(() => exactSettings({ ...valid, path: "private" }), /INVALID/);
  assert.throws(() => exactSettings({ ...valid, timeoutSeconds: 301 }), /INVALID/);
});

test("WP-4-05 voice settings initialization ignores transport key order", () => {
  const { document } = fixture();
  const reordered = Object.fromEntries(Object.entries(settings()).reverse());
  const controller = createVoiceController({ document, invoke: async () => {} });

  controller.initialize(snapshot("generation-a", { settings: reordered }));

  assert.equal(controller.isDirty(), false);
});

test("WP-4-05 bundle status accepts resumable task state without private paths", () => {
  const status = {
    windowGeneration: 3,
    coreGenerationId: "generation-3",
    bundles: [{
      key: "gpt_sovits_v2pro",
      label: "GPT-SoVITS",
      provider: "gpt-sovits",
      installed: false,
      size: 1024,
    }],
    activeTask: {
      taskId: "task-1",
      bundleKey: "gpt_sovits_v2pro",
      state: "cancelled",
      progress: 37,
      cancellable: false,
      result: null,
      error: null,
    },
  };
  assert.deepEqual(exactBundleStatus(status), status);
  assert.throws(() => exactBundleStatus({ ...status, bundles: [{ ...status.bundles[0], path: "D:/private" }] }), /INVALID|private/);
  assert.throws(() => exactBundleStatus({ ...status, activeTask: { ...status.activeTask, state: "unknown" } }), /INVALID/);
});

test("WP-4-05 unified voice status is exact and contains all provider availability", () => {
  const value = voiceStatus();
  assert.deepEqual(exactVoiceStatus(value), value);
  assert.throws(() => exactVoiceStatus({ ...value, audioPath: "D:/private.wav" }), /INVALID/);
  assert.throws(() => exactVoiceStatus({
    ...value,
    runtime: { ...value.runtime, state: "probing" },
  }), /INVALID/);
});

test("installed bundles stay installed when the managed runtime has a stale failure", () => {
  const presentation = voiceResourcePresentation({
    availability: "installed",
    taskState: "completed",
    runtimeFailed: true,
  });

  assert.deepEqual(presentation, {
    status: "",
    ready: true,
    readyLabel: "已安装",
    runtimeFailed: true,
    installationFailed: false,
  });
});

test("an installation task failure still wins over a previously installed bundle", () => {
  const presentation = voiceResourcePresentation({
    availability: "installed",
    taskState: "failed",
    runtimeFailed: false,
  });

  assert.equal(presentation.status, "failed");
  assert.equal(presentation.ready, true);
  assert.equal(presentation.installationFailed, true);
});

test("WP-4-05 voice save sends the bound identity and exact draft then rebinds", async () => {
  const { controls, document } = fixture();
  const calls = [];
  let restarted = false;
  const controller = createVoiceController({
    document,
    invoke: async (command, args) => {
      calls.push([command, args]);
      if (command === "settings_voice_save") {
        restarted = true;
        return { settings: settings({ timeoutSeconds: 75 }), coreRestartRequired: true };
      }
      if (command === "settings_voice_get" && restarted) {
        return snapshot("generation-b", { settings: settings({ timeoutSeconds: 75 }) });
      }
      if (command === "settings_voice_status_get") {
        return voiceStatus("generation-b");
      }
      throw new Error("unexpected call");
    },
    wait: async () => {},
  });
  controller.initialize(snapshot());
  controls.ttsTimeout.value = "75";
  controls.ttsTimeout.fire("input");
  assert.equal(controller.isDirty(), true);

  await controller.save();

  assert.deepEqual(calls[0], ["settings_voice_save", {
    windowGeneration: 7,
    coreGenerationId: "generation-a",
    draft: settings({ timeoutSeconds: 75 }),
  }]);
  assert.deepEqual(calls[1], ["settings_voice_get", undefined]);
  assert.equal(controller.isDirty(), false);

  calls.length = 0;
  await controller.refreshBundles();
  assert.deepEqual(calls[0], ["settings_voice_status_get", undefined]);
});

test("WP-4-05 failed voice save keeps the unsaved draft", async () => {
  const { controls, document } = fixture();
  const controller = createVoiceController({
    document,
    invoke: async () => { throw new Error("CONFIG_SAVE_FAILED"); },
  });
  controller.initialize(snapshot());
  controls.ttsWorkDir.value = "D:/draft-tts";
  controls.ttsWorkDir.fire("input");

  await assert.rejects(() => controller.save(), /CONFIG_SAVE_FAILED/);
  assert.equal(controls.ttsWorkDir.value, "D:/draft-tts");
  assert.equal(controller.isDirty(), true);
});

test("WP-4-05 voice refresh preserves its draft across another settings restart", async () => {
  const { controls, document } = fixture();
  const calls = [];
  let saved = false;
  const controller = createVoiceController({
    document,
    invoke: async (command, args) => {
      calls.push([command, args]);
      if (command === "settings_voice_get") {
        return snapshot(saved ? "generation-c" : "generation-b", {
          settings: settings(saved ? { workDir: "D:/draft-tts" } : {}),
        });
      }
      if (command === "settings_voice_save") {
        saved = true;
        return { settings: settings({ workDir: "D:/draft-tts" }), coreRestartRequired: true };
      }
      if (command === "settings_voice_status_get") return voiceStatus(saved ? "generation-c" : "generation-b");
      throw new Error("unexpected call");
    },
    wait: async () => {},
  });
  controller.initialize(snapshot());
  controls.ttsWorkDir.value = "D:/draft-tts";
  controls.ttsWorkDir.fire("input");

  await controller.refreshCurrent();
  assert.equal(controls.ttsWorkDir.value, "D:/draft-tts");
  assert.equal(controller.isDirty(), true);

  await controller.save();
  const saveCall = calls.find(([command]) => command === "settings_voice_save");
  assert.deepEqual(saveCall, ["settings_voice_save", {
    windowGeneration: 7,
    coreGenerationId: "generation-b",
    draft: settings({ workDir: "D:/draft-tts" }),
  }]);
});

test("WP-4-05 disabled TTS keeps provider controls, install, and test available", async () => {
  const { controls, document } = fixture();
  const calls = [];
  const controller = createVoiceController({
    document,
    invoke: async (command, args) => {
      calls.push([command, args]);
      if (command === "settings_voice_status_get") return voiceStatus("generation-a", {
        enabled: false,
        runtime: { provider: "gpt-sovits", endpointKind: "managed", state: "disabled", errorCode: null, updatedAt: "2026-08-16T12:00:00+00:00" },
        bundles: [{
          key: "gpt_sovits_v2pro", label: "GPT-SoVITS", provider: "gpt-sovits",
          installed: false, size: 1024, recommended: true,
        }],
      });
      if (command === "settings_voice_test") {
        return { provider: "genie-tts", status: "finished", errorCode: null };
      }
      throw new Error("unexpected call");
    },
  });
  controller.initialize(snapshot("generation-a", { settings: settings({ enabled: false }) }));
  await controller.refreshStatus();
  assert.equal(controls.ttsProvider.disabled, false);
  assert.equal(controls.ttsTestButton.disabled, false);
  controls.ttsProvider.value = "genie-tts";
  controls.ttsProvider.fire("change");
  assert.equal(controls.ttsApiUrl.value, "http://127.0.0.1:9881/");
  controls.ttsTestButton.fire("click");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls.some(([command, args]) => (
    command === "settings_voice_test"
    && args.draft.enabled === false
    && args.draft.provider === "genie-tts"
  )), true);
});

test("GPT-SoVITS endpoint fields derive managed or custom deployment without a third provider", async () => {
  const { controls, document } = fixture();
  const notices = [];
  const custom = settings({
    customBaseUrl: "https://tts.example.com",
    remoteReferenceRoot: "/data/voices",
  });
  const controller = createVoiceController({
    document,
    onStatus: (message, kind) => notices.push([message, kind]),
    invoke: async (command) => {
      if (command === "settings_voice_test") {
        return { provider: "gpt-sovits", status: "finished", errorCode: null };
      }
      if (command === "settings_voice_status_get") {
        return voiceStatus("generation-a", {
          providers: [
            { id: "gpt-sovits", label: "GPT-SoVITS", availability: "configured" },
            { id: "genie-tts", label: "Genie TTS", availability: "not_installed" },
          ],
          runtime: {
            provider: "gpt-sovits", endpointKind: "custom", state: "ready",
            errorCode: null, updatedAt: "2026-08-16T12:00:00+00:00",
          },
        });
      }
      throw new Error("unexpected call");
    },
  });

  controller.initialize(snapshot("generation-a", { settings: custom }));
  assert.equal(controls.ttsApiUrlRow.hidden, true);
  assert.equal(controls.ttsCustomBaseUrlRow.hidden, false);
  assert.equal(controls.ttsWorkDir.disabled, true);
  assert.equal(controls.ttsPythonPath.disabled, true);
  assert.equal(controls.ttsRemoteReferenceRoot.disabled, false);

  controls.ttsTestButton.fire("click");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(notices.some(([message, kind]) => (
    kind === "success" && message.includes("自定义 GPT-SoVITS 服务已连接")
  )), true);
});

test("WP-4-05 Runtime v2 legacy TTS handlers fail closed", () => {
  assert.match(settingsEntry, /function syncTtsState\(\) \{\s*if \(runtimeSettingsHost\) return;/);
  assert.match(settingsEntry, /async function testTtsSettings\(\) \{\s*if \(runtimeSettingsHost\) return;/);
  assert.match(settingsEntry, /function handleTtsProviderChange\(\) \{\s*if \(runtimeSettingsHost\) return;/);
});

test("WP-4-05 settings save uses the persisted capability manifest while rebinding Core domains", () => {
  const saveRuntimeSettings = settingsEntry.slice(
    settingsEntry.indexOf("async function saveRuntimeSettings()"),
    settingsEntry.indexOf("function collectTtsSettings()"),
  );
  assert.match(saveRuntimeSettings, /await refreshRuntimeVoiceCurrent\(\)/);
  assert.match(saveRuntimeSettings, /runtimeFeatureAvailable\("voice\.bundle"\)/);
  assert.doesNotMatch(saveRuntimeSettings, /featureStatus\(manifest/);
});
