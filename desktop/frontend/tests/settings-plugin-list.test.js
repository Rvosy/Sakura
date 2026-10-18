import assert from "node:assert/strict";
import test from "node:test";
import { snapshot, featureFixture, field } from "./fixtures/plugin-settings-fixture.js";

const installId = (index, source = "bundled") => `pi_${source}_${Buffer.from(String(index)).toString("hex")}`;

function catalog() {
  const data = snapshot();
  const template = data.plugins[0];
  data.plugins = ["provider", "infrastructure", "extension"].map((kind, index) => ({
    ...template, installId: installId(index + 1), pluginId: kind,
    name: kind, presentation: { kind, category: "voice" }, sections: [],
  }));
  return data;
}

test("system components begin collapsed and category navigation reveals them", async () => {
  const ui = featureFixture(async () => assert.fail("catalog navigation must not write"));
  const { document } = ui;
  ui.feature.initialize(catalog());
  const list = document.getElementById("pluginList");
  const detail = document.getElementById("pluginDetail");
  const cards = () => list.querySelectorAll(".plugin-card");
  const tab = (kind) => document.querySelector(`[data-plugin-role="${kind}"]`);
  assert.deepEqual(cards().map((card) => card.dataset.pluginInstallId), [3, 1, 2].map((i) => installId(i)));
  assert.equal(document.getElementById("plugin-infrastructure-items").hidden, true);
  assert.equal(document.querySelector(".plugin-group-toggle").getAttribute("aria-expanded"), "false");
  list.scrollTop = 400;
  detail.scrollTop = 200;
  await tab("infrastructure").fire("click");
  assert.equal(cards().length, 1);
  assert.equal(document.getElementById("plugin-infrastructure-items").hidden, false);
  assert.equal(detail.dataset.pluginId, installId(2));
  assert.equal(list.scrollTop, 0);
  assert.equal(detail.scrollTop, 0);
  await tab("all").fire("click");
  assert.equal(detail.dataset.pluginId, installId(3));
  const search = document.getElementById("pluginSearch");
  search.value = "no-such-plugin";
  await search.fire("input");
  assert.equal(cards().length, 0);
  assert.equal(detail.dataset.pluginId, "");
  ui.feature.dispose();
});

test("installation clears search and category and reveals the newly installed component", async () => {
  const data = catalog();
  const installed = {
    ...data.plugins[1], installId: installId(4, "user"), pluginId: "installed_hub", name: "Installed Hub",
    enabled: false, state: "disabled", reasonCode: "PLUGIN_DISABLED", source: "user", canUninstall: true,
  };
  const calls = [];
  const ui = featureFixture(async (command, args) => {
    calls.push([command, args]);
    assert.equal(command, "settings_plugins_install");
    return { ...data, plugins: [...data.plugins, installed], managementAction: "installed",
      installId: installed.installId, pluginId: installed.pluginId };
  });
  const { document } = ui;
  ui.feature.initialize(data);
  await document.querySelector('[data-plugin-role="extension"]').fire("click");
  const search = document.getElementById("pluginSearch");
  search.value = "extension";
  await search.fire("input");
  await document.getElementById("pluginInstallZipButton").fire("click");
  assert.equal(calls.length, 1);
  assert.equal(calls[0][1].sourceKind, "zip");
  assert.equal(search.value, "");
  assert.equal(document.querySelector('[data-plugin-role="all"]').getAttribute("aria-selected"), "true");
  assert.equal(document.getElementById("pluginDetail").dataset.pluginId, installed.installId);
  const card = document.querySelector(`.plugin-card[data-plugin-install-id="${installed.installId}"]`);
  assert.equal(card.revealed, "nearest");
  assert.equal(document.activeElement, card);
  assert.equal(document.getElementById("plugin-infrastructure-items").hidden, false);
  assert.deepEqual(ui.errors, []);
  ui.feature.dispose();
});

test("search and explicit plugin navigation reveal a collapsed component", async () => {
  const ui = featureFixture(async () => assert.fail("navigation must not write"));
  ui.feature.initialize(catalog());
  const { document } = ui;
  const search = document.getElementById("pluginSearch");
  search.value = "infrastructure";
  await search.fire("input");
  assert.equal(document.getElementById("plugin-infrastructure-items").hidden, false);
  await document.querySelector(".plugin-group-toggle").fire("click");
  assert.equal(document.getElementById("plugin-infrastructure-items").hidden, true);
  ui.feature.openPlugin(installId(2));
  assert.equal(document.getElementById("plugin-infrastructure-items").hidden, false);
  assert.equal(document.getElementById("pluginDetail").dataset.pluginId, installId(2));
  ui.feature.dispose();
});

test("installed plugin with a startup problem shows one warning and keeps its settings accessible", async () => {
  const data = catalog();
  const installed = { ...data.plugins[0], installId: installId(4, "user"), pluginId: "needs_config",
    enabled: true, state: "failed", reasonCode: "SETUP_FAILED", source: "user", canUninstall: true };
  const notifications = [];
  const detail = "需要配置模型服务";
  const ui = featureFixture(async () => ({ ...data, plugins: [...data.plugins, installed],
    managementAction: "installed", installId: installed.installId, pluginId: installed.pluginId,
    warning: { message: "已安装，需要完成配置", detail } }),
  { notify: (...args) => notifications.push(args) });
  ui.feature.initialize(data);
  await ui.document.getElementById("pluginInstallZipButton").fire("click");
  assert.deepEqual(ui.errors, [detail]);
  assert.deepEqual(notifications, []);
  assert.equal(ui.document.getElementById("pluginDetail").dataset.pluginId, installed.installId);
  ui.feature.dispose();
});

test("screen settings surface edits and saves the ordinary plugin section", async () => {
  const data = snapshot();
  data.plugins[0].sections = [{ sectionId: "screen_awareness", title: "主动屏幕感知", surface: "screen_awareness",
    reasonCode: "READY", fields: [field("intervalMinutes", { type: "number", value: 20 })],
    values: { intervalMinutes: 20 }, actions: [], collections: [] }];
  const saves = [];
  const ui = featureFixture(async (command, args) => {
    if (command === "settings_plugins_get") return data;
    assert.equal(command, "settings_plugins_save");
    saves.push(args);
    data.plugins[0].sections[0].values = args.values;
    data.plugins[0].sections[0].fields[0].value = args.values.intervalMinutes;
    return { saved: true, pluginId: "fixture_plugin", sectionId: "screen_awareness", changePlan: "applied",
      applicationState: "applied", applicationReasonCode: "READY" };
  });
  ui.feature.initialize(data);
  const input = ui.document.getElementById("screenAwarenessSurface").querySelector("input");
  assert.ok(input);
  input.value = "30";
  await input.fire("input");
  assert.equal(ui.feature.isDirty(), true);
  await ui.feature.save();
  assert.deepEqual(saves, [{ windowGeneration: 7, coreGenerationId: "generation-a", pluginId: "fixture_plugin",
    sectionId: "screen_awareness", values: { intervalMinutes: 30 } }]);
  assert.equal(ui.feature.isDirty(), false);
  assert.deepEqual(ui.errors, []);
  ui.feature.dispose();
});


test("installed plugin update opens its release without changing settings", async () => {
  const ui = featureFixture(async () => assert.fail("viewing updates must not write settings"));
  const data = catalog();
  ui.feature.initialize(data);
  const plugin = data.plugins[0];
  const opened = [];
  ui.feature.setUpdates([{ id: plugin.pluginId, currentVersion: "1.0.0", version: "1.1.0" }], id => opened.push(id));
  ui.feature.openPlugin(plugin.installId);
  const button = ui.document.getElementById("pluginDetail").querySelectorAll("button").find(item => item.textContent.includes("1.0.0 → 1.1.0"));
  assert.ok(button);
  await button.fire("click");
  assert.deepEqual(opened, [plugin.pluginId]);
  assert.equal(ui.feature.isDirty(), false);
  ui.feature.setUpdates([], () => {});
  assert.equal(ui.document.getElementById("pluginDetail").textContent.includes("1.0.0 → 1.1.0"), false);
  ui.feature.dispose();
});


test("duplicate installations show their own version and directory", () => {
  const ui = featureFixture(async () => assert.fail("viewing duplicate copies must not change them"));
  const data = catalog();
  data.plugins = ["old-memory", "memory-copy"].map((directoryName, index) => ({
    ...data.plugins[2], installId: installId(index + 1, "user"), pluginId: "memory", name: "Memory",
    directoryName, version: index ? "0.2.1" : "0.1.0", source: "user", canUninstall: true,
    state: "failed", reasonCode: "PLUGIN_ID_CONFLICT",
  }));
  ui.feature.initialize(data);
  const cards = ui.document.getElementById("pluginList").querySelectorAll(".plugin-card");
  assert.equal(cards.length, 2);
  for (const [index, card] of cards.entries()) {
    assert.ok(card.textContent.includes(data.plugins[index].directoryName));
    assert.ok(card.textContent.includes(data.plugins[index].version));
  }
  ui.feature.dispose();
});
