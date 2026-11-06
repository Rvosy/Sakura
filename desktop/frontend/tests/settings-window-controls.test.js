import assert from "node:assert/strict";
import test from "node:test";
import { browserFixture } from "./fixtures/plugin-settings-fixture.js";
import { installSettingsWindowControls } from "../settings/window-controls.js";
import { executeSettingsClose } from "../settings/close-flow.js";

function fixture({ decorated = false, onClose = async () => {} } = {}) {
  const { document } = browserFixture();
  const region = document.createElement("header");
  region.setAttribute("data-window-drag", "");
  document.body.append(region);
  const controls = document.createElement("div"); controls.id = "windowControls"; controls.hidden = true;
  region.append(controls);
  const buttons = {};
  for (const name of ["Minimize", "Maximize", "Close"]) {
    const button = document.createElement("button"); button.id = `window${name}`;
    controls.append(button); buttons[name] = button;
  }
  const actions = [], errors = [];
  let maximized = false, resize;
  const appWindow = {
    setDecorations: async value => { assert.equal(value, false); },
    isMaximized: async () => maximized,
    minimize: async () => { actions.push("minimize"); },
    toggleMaximize: async () => { maximized = !maximized; actions.push(maximized ? "maximize" : "restore"); },
    startDragging: async () => { actions.push("drag"); },
    onResized: async handler => { resize = handler; return () => { resize = null; }; },
  };
  const controller = installSettingsWindowControls({ document, appWindow, useCustomTitlebar: !decorated, onClose, onError: error => errors.push(error) });
  return { controller, controls, region, buttons, actions, errors,
    async resized(value) { maximized = value; await resize?.(); } };
}

test("window controls minimize, toggle size and track changes from the operating system", async () => {
  const ui = fixture(); await ui.controller.ready;
  assert.equal(ui.controls.hidden, false);
  await ui.buttons.Minimize.fire("click");
  await ui.buttons.Maximize.fire("click");
  assert.equal(ui.buttons.Maximize.getAttribute("aria-label"), "还原");
  await ui.buttons.Maximize.fire("click");
  assert.equal(ui.buttons.Maximize.getAttribute("aria-label"), "最大化");
  await ui.resized(true);
  assert.equal(ui.buttons.Maximize.getAttribute("aria-label"), "还原");
  assert.deepEqual(ui.actions, ["minimize", "maximize", "restore"]);
  assert.deepEqual(ui.errors, []);
  ui.controller.dispose();
  await ui.buttons.Minimize.fire("click");
  assert.deepEqual(ui.actions, ["minimize", "maximize", "restore"]);
});

test("title dragging and double click leave interactive controls to their own actions", async () => {
  const ui = fixture(); await ui.controller.ready;
  await ui.region.fire("mousedown", { button: 0, detail: 1 });
  await ui.region.fire("mousedown", { button: 0, detail: 2 });
  await ui.region.fire("mousedown", { button: 0, detail: 2, target: ui.buttons.Close });
  await ui.region.fire("mousedown", { button: 2, detail: 1 });
  assert.deepEqual(ui.actions, ["drag", "maximize"]);
  assert.deepEqual(ui.errors, []);
  ui.controller.dispose();
});

test("custom close preserves the unsaved settings decision and waits for saving", async () => {
  const effects = []; let decision = "stay";
  const ui = fixture({ onClose: () => executeSettingsClose({
    dirty: true, choose: async () => decision,
    save: async () => { effects.push("saved"); },
    discard: async () => { effects.push("discarded"); },
    close: async () => { effects.push("closed"); },
  }) });
  await ui.controller.ready;
  await ui.buttons.Close.fire("click");
  assert.deepEqual(effects, []);
  decision = "save";
  await ui.buttons.Close.fire("click");
  assert.deepEqual(effects, ["saved", "closed"]);
  ui.controller.dispose();
});

test("native titlebars keep their own controls", async () => {
  const ui = fixture({ decorated: true }); await ui.controller.ready;
  assert.equal(ui.controls.hidden, true);
  await ui.region.fire("mousedown", { button: 0, detail: 1 });
  assert.deepEqual(ui.actions, []);
  ui.controller.dispose();
});
