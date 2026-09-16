import assert from "node:assert/strict";
import test from "node:test";
import { createAsrSettingsController } from "../settings/asr-runtime.js";

function element() {
  const events = new Map();
  return { value: "", children: [], hidden: false, textContent: "",
    append(...items) { this.children.push(...items); },
    replaceChildren(...items) { this.children = items; },
    setAttribute(name, value) { this[name] = value; },
    addEventListener(name, handler) { events.set(name, handler); },
    async fire(name) { await events.get(name)?.(); },
  };
}
function fixture(snapshot, { devices = [], defaultDeviceId = null } = {}) {
  const controls = Object.fromEntries(["asrProvider", "asrStatus", "asrLocation",
    "asrSettings", "asrSettingsHome", "asrInputDevice", "asrRefreshDevices",
    "asrInputControls", "asrInputControlsHome"].map((key) => [key, element()]));
  const calls = [];
  const controller = createAsrSettingsController({
    document: { getElementById: (key) => controls[key], createElement: element },
    invoke: async (name, args) => {
      calls.push([name, args]);
      if (name === "settings_asr_devices") return typeof devices === "function" ? devices() : { devices, defaultDeviceId };
      if (name === "settings_asr_save") Object.assign(snapshot, args.payload);
      return structuredClone(snapshot);
    },
  });
  return { controls, calls, controller };
}

test("ASR choices are Hub supplied, unavailable explicit choice survives refresh, selection does not auto-save", async () => {
  const f = fixture({ providers: [{ providerId: "example.local", label: "Local", processingLocation: "local", available: true },
    { providerId: "example.remote", label: "Remote", processingLocation: "remote", available: true }],
  selectedProviderId: "example.missing", language: "auto", sections: [] });
  await f.controller.refresh();
  assert.equal(f.controls.asrProvider.value, "example.missing");
  assert.equal(f.controller.isDirty(), false);
  f.controls.asrProvider.value = "example.remote";
  await f.controls.asrProvider.fire("change");
  assert.equal(f.controller.isDirty(), true);
  assert.match(f.controls.asrLocation.textContent, /远端/);
  await f.controller.refresh({ preserveDraft: true });
  assert.equal(f.controls.asrProvider.value, "example.remote");
  assert.equal(f.calls.every(([name]) => name === "settings_asr_get"), true);
  await f.controller.save();
  assert.deepEqual(f.calls.find(([name]) => name === "settings_asr_save")[1].payload,
    { selectedProviderId: "example.remote" });
  assert.equal(f.controller.isDirty(), false);
  f.controller.dispose();
});

test("microphone changes save independently while Hub is disabled and retain its saved choices", async () => {
  const controls = Object.fromEntries(["asrProvider", "asrStatus", "asrLocation",
    "asrSettings", "asrSettingsHome", "asrInputDevice"].map((key) => [key, element()]));
  const saved = { selectedProviderId: "engine.saved", language: "ja", inputDeviceId: "old-mic" };
  let enabled = false;
  const controller = createAsrSettingsController({
    document: { getElementById: (key) => controls[key], createElement: element },
    invoke: async (name, args) => {
      if (name === "settings_asr_save") {
        if (!enabled && Object.keys(args.payload).some((key) => key !== "inputDeviceId")) {
          throw new Error("SERVICE_MISSING");
        }
        Object.assign(saved, args.payload);
      }
      return { providers: [], sections: [], available: enabled, inputDeviceId: saved.inputDeviceId,
        ...(enabled ? saved : { selectedProviderId: null }) };
    },
  });
  try {
    await controller.refresh();
    controls.asrInputDevice.value = "new-mic";
    await controller.save();
    assert.equal(controller.isDirty(), false);
    enabled = true; // The settings save may now continue to the staged plugin enable.
    await controller.refresh();
    assert.deepEqual(saved, { selectedProviderId: "engine.saved", language: "ja", inputDeviceId: "new-mic" });
    assert.equal(controls.asrProvider.value, "engine.saved");
  } finally { controller.dispose(); }
});

test("Hub controls belong to its runtime plugin identity and cancelled edits restore the entire draft", async () => {
  const f = fixture({ hubPluginId: "thirdparty.hub", providers: [
    { providerId: "thirdparty.asr", label: "Local", processingLocation: "local", available: true },
  ], selectedProviderId: "thirdparty.asr", language: "auto", inputDeviceId: "saved-mic" });
  await f.controller.refresh();
  assert.equal(f.controller.hasPluginControls("thirdparty.hub"), true);
  assert.equal(f.controller.hasPluginControls("unrelated.plugin"), false);
  const initial = f.controller.pluginDraft();
  const dialog = element();
  f.controller.mountPluginControls("unrelated.plugin", dialog);
  assert.equal(dialog.children.length, 0);
  f.controller.mountPluginControls("thirdparty.hub", dialog);
  assert.deepEqual(dialog.children, [f.controls.asrSettings]);
  assert.equal(f.calls.some(([name]) => name === "settings_asr_devices"), false);
  f.controls.asrProvider.value = "";
  await f.controls.asrProvider.fire("change");
  assert.equal(f.controller.isDirty(), true);
  await f.controller.refresh({ preserveDraft: true });
  assert.equal(f.controls.asrProvider.value, "");
  f.controller.restorePluginDraft(initial);
  assert.equal(f.controller.isDirty(), false);
  assert.equal(f.controls.asrProvider.value, "thirdparty.asr");
  assert.equal(Object.hasOwn(f.controller.pluginDraft(), "language"), false);
  f.controller.unmountPluginControls();
  assert.equal(f.controls.asrSettingsHome.children.at(-1), f.controls.asrSettings);
  assert.equal(f.calls.some(([name]) => name === "settings_asr_save"), false);
  f.controller.dispose();
});

test("installed idle engines stay selectable without a failure label or a redundant empty choice", async () => {
  const f = fixture({ selectedProviderId: "test.asr", providers: [
    { providerId: "test.asr", label: "Engine", state: "unloaded", available: false, processingLocation: "local" },
  ] });
  await f.controller.refresh();
  assert.deepEqual(f.controls.asrProvider.children.map((item) => [item.value, item.textContent]), [["test.asr", "Engine"]]);
  assert.equal(f.controls.asrStatus.textContent, "");
  assert.equal(f.controls.asrLocation.textContent, "");
  f.controller.dispose();
});

test("microphone enumeration preserves missing selection, plugin dialog draft rolls back without saving", async () => {
  const f = fixture({ providers: [{ providerId: "asr.engine", state: "ready", available: true }],
    selectedProviderId: "asr.engine", inputDeviceId: "disconnected", language: "auto", sections: [] }, {
    devices: [{ id: "mic-a", label: "USB microphone" }], defaultDeviceId: "mic-a",
  });
  await f.controller.refresh(); await f.controller.refreshDevices();
  assert.equal(f.controls.asrInputDevice.value, "disconnected");
  assert.equal(f.controller.isDirty(), false);
  const initial = f.controller.pluginDraft();
  const dialog = element();
  f.controller.mountPluginControls("asr.engine", dialog);
  assert.equal(dialog.children[0], f.controls.asrInputControls);
  f.controls.asrInputDevice.value = "mic-a";
  await f.controls.asrInputDevice.fire("change");
  assert.equal(f.controller.isDirty(), true);
  f.controller.restorePluginDraft(initial);
  assert.equal(f.controls.asrInputDevice.value, "disconnected");
  f.controller.unmountPluginControls();
  assert.equal(f.controls.asrInputControlsHome.children.at(-1), f.controls.asrInputControls);
  assert.equal(f.calls.some(([name]) => name === "settings_asr_save"), false);
  f.controls.asrInputDevice.value = "";
  await f.controller.save();
  assert.equal(f.calls.find(([name]) => name === "settings_asr_save")[1].payload.inputDeviceId, "");
  f.controller.dispose();
});

test("device enumeration preserves edits made while waiting and rejects out-of-order lists", async () => {
  const requests = [];
  const f = fixture({ providers: [], selectedProviderId: null, inputDeviceId: "original", language: "auto", sections: [] }, {
    devices: () => new Promise((resolve) => requests.push(resolve)),
  });
  await f.controller.refresh();
  const first = f.controller.refreshDevices();
  const second = f.controller.refreshDevices();
  f.controls.asrInputDevice.value = "chosen-while-waiting";
  requests[1]({ devices: [{ id: "new", label: "New" }], defaultDeviceId: "new" });
  await second;
  assert.equal(f.controls.asrInputDevice.value, "chosen-while-waiting");
  requests[0]({ devices: [{ id: "obsolete", label: "Old" }], defaultDeviceId: "obsolete" });
  await first;
  assert.equal(f.controls.asrInputDevice.children.some((item) => item.value === "obsolete"), false);
  assert.equal(f.controls.asrInputDevice.value, "chosen-while-waiting");
  f.controller.dispose();
});
