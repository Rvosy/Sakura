import test from "node:test";
import assert from "node:assert/strict";
import { featureFixture, field, snapshot } from "./fixtures/plugin-settings-fixture.js";

function contributed() {
  const value = snapshot();
  const owner = value.plugins[0];
  owner.pages = [{ pageId: "fixture_plugin:schedule", title: "日程", group: "behavior", icon: "calendar", order: 10, regions: ["extensions"] }];
  owner.sections[0].placement = { pageId: "fixture_plugin:schedule", region: "content", available: true, order: 10 };
  owner.sections[0].presentation = { component: "form" };
  owner.sections.push({ ...structuredClone(owner.sections[0]), sectionId: "engine", title: "引擎", placement: null });
  const guest = structuredClone(owner);
  guest.installId = "pi_bundled_6775657374"; guest.pluginId = "guest"; guest.name = "Guest";
  guest.pages[0].pageId = "guest:schedule";
  guest.sections = [{ ...guest.sections[0], placement: { pageId: "fixture_plugin:schedule", region: "extensions", available: true, order: 20 } }];
  value.plugins.push(guest);
  return value;
}

test("namespaced pages share exported regions, append navigation and keep one primary editor", async () => {
  const state = contributed(); const calls = [], navigations = [];
  const ui = featureFixture(async (command, args) => { calls.push([command, args]); return state; }, { showPage: id => navigations.push(id) });
  ui.feature.initialize(state);
  const owner = ui.document.getElementById("page-fixture_plugin:schedule");
  assert.equal(owner.querySelectorAll("[data-plugin-field=\"label\"]").length, 2);
  assert.equal(ui.document.getElementById("page-guest:schedule").textContent, "暂无设置");
  const group = ui.document.getElementById("navgrp-behavior").parentElement;
  assert.deepEqual(group.children.slice(1).map(node => node.dataset.page), ["fixture_plugin:schedule", "guest:schedule"]);
  const input = owner.querySelector("input"); input.value = "page draft"; await input.fire("input");
  await ui.openSettings();
  const dialog = ui.feature.dialogElement();
  assert.equal(dialog.querySelectorAll("[data-plugin-section]").length, 1);
  assert.equal(dialog.querySelector("[data-plugin-section]").dataset.pluginSection, "engine");
  await dialog.querySelector(".plugin-dialog-close").fire("click");
  assert.equal(input.value, "page draft");
  assert.equal(ui.feature.isDirty(), true);
  assert.equal(calls.length, 0);
  await ui.document.querySelector(".plugin-settings-link").fire("click");
  assert.equal(navigations.at(-1), "fixture_plugin:schedule");
  ui.feature.dispose();
});

test("page drafts survive reload and load failures; disable withdraws contributions", async () => {
  let state = contributed(); const ui = featureFixture(async () => state);
  ui.feature.initialize(state);
  const page = () => ui.document.getElementById("page-fixture_plugin:schedule");
  const input = page().querySelector("input"); input.value = "unsaved"; await input.fire("input");
  state = structuredClone(state); state.plugins[0].sections[0].reasonCode = "SETTINGS_LOAD_FAILED";
  await ui.feature.refreshCurrent();
  assert.match(page().textContent, /设置加载失败/); assert.equal(ui.feature.isDirty(), true);
  state = structuredClone(state); state.plugins[0].sections[0].reasonCode = "READY";
  await ui.feature.refreshCurrent();
  assert.equal(page().querySelector("input").value, "unsaved");
  state = structuredClone(state); state.plugins[0].enabled = false; state.plugins[0].pages = []; state.plugins[0].sections = [];
  state.plugins[1].sections[0].placement.available = false;
  await ui.feature.refreshCurrent();
  assert.equal(page(), null);
  ui.feature.dispose();
});

for (const persisted of [false, true]) test(`only failed sections are retried after a partial save (persisted=${persisted})`, async () => {
  let state = contributed(), fail = true; const writes = [];
  const ui = featureFixture(async (command, args) => {
    if (command === "settings_plugins_get") return state;
    if (command === "settings_plugins_save") {
      writes.push(args.sectionId);
      if (args.sectionId === "engine" && fail && !persisted) throw new Error("SETTINGS_SAVE_FAILED");
      const section = state.plugins[0].sections.find(section => section.sectionId === args.sectionId);
      section.values = structuredClone(args.values); section.fields[0].value = args.values.label;
      if (args.sectionId === "engine" && fail) {
        section.reasonCode = "CONFIG_APPLY_FAILED";
        throw new Error("CONFIG_APPLY_FAILED: 插件设置已保存，但应用失败。");
      }
      section.reasonCode = "READY";
      return { saved: true, pluginId: args.pluginId, sectionId: args.sectionId, changePlan: "applied", applicationState: "applied", applicationReasonCode: "READY" };
    }
    throw new Error(command);
  });
  ui.feature.initialize(state);
  const input = ui.document.getElementById("page-fixture_plugin:schedule").querySelector("input");
  input.value = "page changed"; await input.fire("input");
  await ui.openSettings(); const privateInput = ui.feature.dialogElement().querySelector("input");
  privateInput.value = "engine changed"; await privateInput.fire("input");
  await ui.feature.dialogElement().querySelector("form").fire("submit");
  await assert.rejects(ui.feature.save(), /已保存 1 个设置区块/);
  assert.equal(ui.feature.isDirty(), true);
  await ui.openSettings();
  assert.equal(ui.feature.dialogElement().querySelector("input").value, "engine changed");
  await ui.feature.dialogElement().querySelector("form").fire("submit");
  fail = false; await ui.feature.save();
  assert.deepEqual(writes, ["general", "engine", "engine"]);
  assert.equal(ui.feature.isDirty(), false); ui.feature.dispose();
});

test("an empty contributed page appears immediately after its last section is removed", async () => {
  let state = contributed();
  const ui = featureFixture(async () => state); ui.feature.initialize(state);
  const page = ui.document.getElementById("page-fixture_plugin:schedule");
  assert.equal(page.querySelector(".empty-state").hidden, true);
  state = structuredClone(state); state.plugins.forEach(plugin => { plugin.sections = []; });
  await ui.feature.refreshCurrent();
  assert.equal(page.querySelector(".empty-state").hidden, false);
  ui.feature.dispose();
});

test("invalid contributed numeric drafts prevent all writes", async () => {
  const state = contributed(); const section = state.plugins[0].sections[0];
  section.fields = [field("limit", { type: "integer", minimum: 1, maximum: 10, value: 3 })]; section.values = { limit: 3 };
  const calls = []; const ui = featureFixture(async command => calls.push(command)); ui.feature.initialize(state);
  const input = ui.document.getElementById("page-fixture_plugin:schedule").querySelector("input");
  input.value = "99"; await input.fire("input");
  await assert.rejects(ui.feature.save(), /请检查/); assert.deepEqual(calls, []); ui.feature.dispose();
});
