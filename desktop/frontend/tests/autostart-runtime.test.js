import assert from "node:assert/strict";
import test from "node:test";

import {
  autostartErrorMessage,
  createAutostartSettingsController,
  validateAutostartSnapshot,
} from "../settings/autostart-runtime.js";

function checkbox() {
  const listeners = new Map();
  return {
    checked: false,
    disabled: true,
    addEventListener(name, listener) { listeners.set(name, listener); },
    removeEventListener(name, listener) {
      if (listeners.get(name) === listener) listeners.delete(name);
    },
    fire(name) { listeners.get(name)?.(); },
  };
}

function snapshot(launchAtLogin = false) {
  return {
    schemaVersion: 1,
    windowGeneration: 7,
    launchAtLogin,
  };
}

test("autostart snapshot is strict and generation-scoped", () => {
  assert.equal(validateAutostartSnapshot(snapshot(true)).launchAtLogin, true);
  assert.throws(() => validateAutostartSnapshot({ ...snapshot(), windowGeneration: 0 }));
  assert.throws(() => validateAutostartSnapshot({ ...snapshot(), extra: true }));
});

test("autostart platform errors retain the original OS reason", () => {
  assert.equal(
    autostartErrorMessage("AUTOSTART_SETTINGS_UPDATE_FAILED: Access denied (os error 5)"),
    "AUTOSTART_SETTINGS_UPDATE_FAILED: Access denied (os error 5)",
  );
});

test("autostart draft is committed only when settings are saved", async () => {
  const control = checkbox();
  const calls = [];
  const controller = createAutostartSettingsController({
    document: { getElementById: () => control },
    invoke: async (command, args) => {
      calls.push([command, args]);
      return snapshot(args.launchAtLogin);
    },
    onDirty: () => {},
  });
  controller.initialize(snapshot(false));
  assert.equal(control.disabled, false);
  control.checked = true;
  control.fire("change");
  assert.equal(controller.isDirty(), true);
  assert.deepEqual(calls, []);

  await controller.save();
  assert.equal(controller.isDirty(), false);
  assert.deepEqual(calls, [["settings_autostart_save", {
    windowGeneration: 7,
    launchAtLogin: true,
  }]]);
});

test("failed autostart save keeps the draft dirty and discard restores the platform state", async () => {
  const control = checkbox();
  const controller = createAutostartSettingsController({
    document: { getElementById: () => control },
    invoke: async () => { throw new Error("AUTOSTART_SETTINGS_UPDATE_FAILED"); },
    onDirty: () => {},
  });
  controller.initialize(snapshot(false));
  control.checked = true;
  control.fire("change");
  await assert.rejects(() => controller.save(), /AUTOSTART_SETTINGS_UPDATE_FAILED/);
  assert.equal(controller.isDirty(), true);
  controller.discard();
  assert.equal(control.checked, false);
  assert.equal(controller.isDirty(), false);
});
