import { errorText } from '../core/error-display.js';
import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import vm from "node:vm";

import { createVoiceController as createController, exactVoiceSnapshot } from "../settings/voice-runtime.js";

function hub(overrides = {}) {
  return { pluginId: "example.voice-hub", provides: ["sakura.tts"], enabled: true, state: "active", ...overrides };
}

function createVoiceController(options) {
  return createController({ getPlugins: () => [hub()], ...options });
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
    _textContent: "",
    get textContent() { return this._textContent; },
    set textContent(value) { this._textContent = value; this.children = []; },
    disabled: false,
    className: "",
    setAttribute(name, value) { this[name] = String(value); },
    append(...items) {
      for (const item of items) {
        if (item.parentNode) item.parentNode.children = item.parentNode.children.filter((child) => child !== item);
        item.parentNode = this;
        this.children.push(item);
      }
    },
    addEventListener(name, listener) { (listeners[name] ||= []).push(listener); },
    fire(name) { for (const listener of listeners[name] || []) listener(); },
    async fireAsync(name) { for (const listener of listeners[name] || []) await listener(); },
  };
}

function fixture() {
  const controls = Object.fromEntries([
    "page-voice", "voiceSettings", "voiceUnavailable", "ttsEnabled", "ttsProvider", "ttsPluginSettings",
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

for (const sameCharacter of [true, false]) {
  test(`Studio refresh ${sameCharacter ? "preserves the voice selection" : "does not carry voice drafts to another character"}`, async () => {
    const { controls, document, created } = fixture();
    const original = snapshot();
    const next = structuredClone(original);
    next.coreGenerationId = "generation-b";
    if (!sameCharacter) next.character = { characterId: "beta", displayName: "Beta" };
    const calls = [];
    const controller = createVoiceController({
      document,
      invoke: async (command, args) => {
        calls.push([command, args]);
        if (command === "settings_voice_get") return next;
        if (command === "settings_voice_save") return {
          applicationState: "applied", saveState: "complete",
          selectionSaved: true, reasonCode: "READY", snapshot: {},
        };
        throw new Error(`unexpected ${command}`);
      },
    });
    controller.initialize(original);
    controls.ttsEnabled.checked = false;
    controls.ttsProvider.value = "org.demo.graph-voice";
    controls.ttsProvider.fire("change");

    await controller.refreshCurrent({ preserveDraft: true });

    assert.deepEqual(calls.map(([command]) => command), ["settings_voice_get"]);
    assert.equal(controller.isDirty(), sameCharacter);
    assert.equal(controls.ttsEnabled.checked, !sameCharacter);
    assert.equal(controls.ttsProvider.value, sameCharacter ? "org.demo.graph-voice" : "com.example.neural-voice");
    await controller.save();
    const saved = calls.find(([command]) => command === "settings_voice_save")[1];
    assert.equal(saved.coreGenerationId, "generation-b");
    assert.equal(saved.draft.characterId, sameCharacter ? "alpha" : "beta");
    assert.equal(Object.hasOwn(saved.draft, "sections"), false);
    assert.equal(controller.isDirty(), false);
  });
}

test("a failed Studio refresh retains voice drafts for the next successful refresh", async () => {
  const { controls, document } = fixture();
  let failing = true;
  const controller = createVoiceController({
    document,
    invoke: async () => {
      if (failing) throw new Error("Core unavailable");
      return snapshot({ coreGenerationId: "generation-b" });
    },
  });
  controller.initialize(snapshot());
  controls.ttsEnabled.checked = false;
  await assert.rejects(controller.refreshCurrent({ preserveDraft: true }), /Core unavailable/);
  assert.equal(controls.ttsEnabled.checked, false);
  assert.equal(controller.isDirty(), true);
  failing = false;
  await controller.refreshCurrent({ preserveDraft: true });
  assert.equal(controls.ttsEnabled.checked, false);
  assert.equal(controller.isDirty(), true);
});

test("voice settings accept additive Core fields", () => {
  const value = { ...snapshot(), futureField: true };
  assert.deepEqual(exactVoiceSnapshot(value), value);
});


test("disabled TTS Hub skips voice IPC and can recover after the Hub is enabled", async () => {
  const { controls, document, created } = fixture();
  let available = false;
  let calls = 0;
  let availabilityRefreshes = 0;
  let pluginPageOpens = 0;
  const controller = createVoiceController({
    document,
    getPlugins: () => [hub({ enabled: available, state: available ? "active" : "disabled" })],
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
  assert.equal(controls.voiceSettings.hidden, false);
  assert.equal(controls.voiceUnavailable.hidden, false);
  assert.equal(controls["page-voice"].dataset.voiceState, "disabled");
  assert.equal(created.some((item) => item.textContent === "语音管理暂不可用"), true);
  assert.equal(created.some((item) => item.textContent === "语音插件已停用。"), true);
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

test("entering voice refreshes prepared providers without applying or losing edits", async () => {
  const { controls, document, created } = fixture();
  const initial = snapshot();
  const next = snapshot();
  next.providers[1].available = true;
  let plugins = [hub({ state: "starting" })];
  const calls = [];
  const controller = createVoiceController({
    document,
    getPlugins: () => plugins,
    refreshAvailability: async () => { calls.push("plugins"); plugins = [hub()]; },
    invoke: async (command) => { calls.push(command); return next; },
  });
  controller.initialize(initial);
  controls.ttsProvider.value = initial.providers[1].providerId;
  controls.ttsEnabled.checked = false;
  await controller.onPageChanged("plugins");
  assert.deepEqual(calls, []);
  await controller.onPageChanged("voice");
  assert.deepEqual(calls, ["plugins", "settings_voice_get"]);
  assert.equal(controls.ttsProvider.children[1].textContent, next.providers[1].label);
  assert.equal(controls.ttsProvider.value, initial.providers[1].providerId);
  assert.equal(controls.ttsEnabled.checked, false);
  assert.equal(controller.isDirty(), true);
  controller.dispose();
  await controller.onPageChanged("voice");
  assert.equal(calls.length, 2);
});

test("settings startup connects the voice controller to the installed plugin snapshot", async () => {
  const source = await readFile(new URL("../settings/settings.js", import.meta.url), "utf8");
  const start = source.indexOf("runtimeVoiceController = createVoiceController({");
  const endMarker = "await runtimeVoiceController.refreshCurrent();";
  const end = source.indexOf(endMarker, start) + endMarker.length;
  const { controls, document, created } = fixture();
  let plugins = [hub({ enabled: false, state: "disabled" })];
  let calls = 0;
  const context = {
    createVoiceController: createController, document,
    invoke: async () => { calls++; return snapshot(); },
    runtimePluginController: {
      installedPlugins: () => plugins,
      refreshCurrent: async () => { plugins = [hub()]; },
      onVoiceSectionsRendered() {},
    },
    enhanceSelect() {}, refreshSelect() {}, refreshDirty() {}, notify() {}, showPage() {}, openVoicePlugin() {},
    runtimeDiagnostics: { reportError() {} },
  };
  await vm.runInNewContext(`(async () => { let runtimeVoiceController; ${source.slice(start, end)} })()`, context);
  assert.equal(calls, 0);
  assert.equal(controls["page-voice"].dataset.voiceState, "disabled");
  await created.find((item) => item.textContent === "重新检查").fireAsync("click");
  assert.equal(calls, 1);
  assert.equal(controls["page-voice"].dataset.voiceState, "available");
});

test("empty voice engine list keeps the selector visible with an installation hint", async () => {
  const { controls, document, created } = fixture();
  const controller = createVoiceController({
    document,
    invoke: async () => snapshot({
      selection: { configured: false, enabled: false, providerId: null, available: false },
      providers: [],
    }),
  });

  assert.equal(await controller.refreshCurrent(), null);
  assert.equal(controls.voiceSettings.hidden, false);
  assert.equal(controls.voiceUnavailable.hidden, true);
  assert.equal(controls.ttsProvider.children.at(-1).textContent, "未安装语音插件");
  assert.equal(controls.ttsPluginSettings.disabled, true);
  assert.equal(controller.isDirty(), false);
});

test("voice settings retain startup diagnostics when the selected provider never registered", async () => {
  const { document, created } = fixture();
  const controller = createVoiceController({ document, invoke: async () => snapshot({ providers: [],
    selection: { enabled: true, providerId: "fixture.tts", diagnostics: {
      diagnostic: "fixture-tts.dll not found", exception_stack: "at initialize:42",
    } },
  }) });
  await controller.refreshCurrent();
  assert.ok(created.some(item => item.textContent.includes("fixture-tts.dll not found") && item.textContent.includes("initialize:42")));
});

for (const state of ["missing", "starting", "failed"]) {
  test(`${state} Hub does not issue a voice read and retains its state`, async () => {
    const { controls, document } = fixture();
    let calls = 0;
    const controller = createController({
      document,
      getPlugins: () => state === "missing" ? [] : [hub({ state })],
      invoke: async () => { calls++; throw new Error("Hub has no service"); },
    });
    await controller.refreshCurrent();
    assert.equal(calls, 0);
    assert.equal(controls["page-voice"].dataset.voiceState, state);
  });
}

test("Hub state changing after the plugin snapshot is shown without a fabricated selection", async () => {
  const { controls, document } = fixture();
  const controller = createVoiceController({
    document,
    invoke: async () => snapshot({
      availability: { state: "disabled", reasonCode: "TTS_HUB_DISABLED" },
      selection: null, providers: [],
    }),
  });
  await controller.refreshCurrent();
  assert.equal(controls["page-voice"].dataset.voiceState, "disabled");
  assert.equal(controller.isDirty(), false);
});

test("a failed voice read is displayed as an error and recovers on the next read", async () => {
  const { controls, document } = fixture();
  const failure = new Error("TTS_SERVICE_UNAVAILABLE");
  const statuses = [];
  let failed = true;
  const controller = createVoiceController({
    document,
    invoke: async () => {
      if (failed) throw failure;
      return snapshot();
    },
    onStatus: (error, type) => statuses.push({ error, type }),
  });
  await controller.refreshCurrent();
  assert.equal(controls["page-voice"].dataset.voiceState, "error");
  assert.equal(statuses.length, 1);
  assert.equal(statuses[0].error, failure);
  assert.equal(statuses[0].type, "error");
  failed = false;
  await controller.refreshCurrent();
  assert.equal(controls["page-voice"].dataset.voiceState, "available");
  assert.equal(statuses.length, 1);
});

test("voice rendering failures retain the original exception after a successful IPC read", async () => {
  const { controls, document } = fixture();
  const failure = new TypeError("voice control rendering failed");
  const reports = [];
  let failing = true;
  const controller = createVoiceController({
    document,
    invoke: async () => snapshot(),
    refreshSelect() {
      if (failing) { failing = false; throw failure; }
    },
    reportError: (error, context) => reports.push({ error, context }),
  });
  await controller.refreshCurrent();
  assert.equal(reports.length, 1);
  assert.equal(reports[0].error, failure);
  assert.equal(reports[0].context.stage, "voice.render");
  assert.equal(controls["page-voice"].dataset.voiceState, "error");
});

test("voice save applies only character selection and leaves provider settings to their plugin", async () => {
  const { controls, document, created } = fixture();
  const calls = [];
  const controller = createVoiceController({
    document,
    invoke: async (command, args) => {
      calls.push([command, args]);
      if (command === "settings_voice_save") {
        return {
          applicationState: "applied",
          saveState: "complete",
          selectionSaved: true,
          reasonCode: "READY",
          snapshot: {},
        };
      }
      if (command === "settings_voice_get") {
        return snapshot();
      }
      throw new Error(`unexpected ${command}`);
    },
  });
  controller.initialize(snapshot());
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
    },
  }]);
  assert.equal(calls[1][0], "settings_voice_get");
  assert.equal(controller.isDirty(), false);
});

test("voice plugin shortcut follows the selected engine before settings are applied", async () => {
  const { controls, document } = fixture();
  const opened = [];
  const controller = createVoiceController({ document, invoke: async () => snapshot(),
    openPlugin: id => opened.push(id) });
  controller.initialize(snapshot({ providers: [
    { providerId: "first", label: "First", available: true },
    { providerId: "second", label: "Second", available: true },
  ], selection: { enabled: true, providerId: "first" } }));
  assert.equal(controls.ttsPluginSettings.disabled, false);
  controls.ttsProvider.value = "second";
  controls.ttsProvider.fire("change");
  controls.ttsPluginSettings.fire("click");
  assert.deepEqual(opened, ["second"]);
  assert.equal(controller.isDirty(), true);
  controller.dispose();
});

for (const installed of [false, true]) {
  test(`TTS absent saved engine uses the empty selector while installed failure retains diagnostics (${installed})`, async () => {
    const { document, controls, created } = fixture();
    const controller = createVoiceController({ document,
      getPlugins: () => [hub(), ...(installed ? [{ pluginId: "saved.tts", enabled: true, state: "failed", provides: [] }] : [])],
      invoke: async () => snapshot({ providers: [], selection: {
        providerId: "saved.tts", enabled: true, stage: "provider_selection", reasonCode: "TTS_PROVIDER_UNAVAILABLE",
        diagnostics: { diagnostic: "selected provider diagnostic" },
      } }),
    });
    await controller.refreshCurrent();
    assert.equal(controls.voiceUnavailable.hidden, !installed);
    assert.equal(controls.voiceSettings.hidden, false);
    if (installed) assert.ok(created.some(item => item.textContent.includes("selected provider diagnostic")));
    assert.equal(controller.isDirty(), false);
    controller.dispose();
  });
}
