import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import test from "node:test";

import * as pluginPresentation from "../settings/plugin-presentation.js";

const source = readFileSync(new URL("../settings/settings.js", import.meta.url), "utf8");

// Execute the Settings handlers with isolated DOM/IPC boundaries, without
// starting the native window or reading and changing installed plugins.
function fixture() {
  const document = { activeElement: null };
  const element = (tagName = "div") => ({
    tagName, children: [], dataset: {}, attributes: {}, listeners: {},
    className: "", value: "", scrollTop: 0, hidden: false, ownText: "",
    get textContent() { return this.ownText + this.children.map((child) => child.textContent).join(""); },
    set textContent(value) { this.ownText = String(value); this.children = []; },
    append(...children) {
      for (let child of children) {
        if (typeof child !== "object") child = Object.assign(element(), { textContent: child });
        if (child.parent) child.parent.children = child.parent.children.filter((item) => item !== child);
        child.parent = this;
        this.children.push(child);
      }
    },
    setAttribute(name, value) { this.attributes[name] = String(value); },
    addEventListener(name, handler) { this.listeners[name] = handler; },
    click() { this.listeners.click?.(); },
    focus() { document.activeElement = this; },
    scrollIntoView(options) { this.revealed = options.block; },
    matches(selector) {
      if (selector.startsWith(".")) return this.className.split(" ").includes(selector.slice(1));
      const match = selector.match(/^\[data-([a-z-]+)="([^"]+)"\]$/);
      return match && this.dataset[match[1].replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] === match[2];
    },
    closest(selector) { return this.matches(selector) ? this : this.parent?.closest(selector); },
    querySelectorAll(selector) {
      return this.children.flatMap((child) => [
        ...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector),
      ]);
    },
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; },
  });
  document.createElement = element;
  const fields = Object.fromEntries(["pluginSearch", "pluginList", "pluginDetail", "pluginRoleTabs", "pluginTotal"]
    .map((name) => [name, element()]));
  const plugin = (id, kind) => ({
    id, plugin_id: id, name: id, description: "Plugin description", enabled: true,
    state: "active", source: "user", presentation: { kind, category: "voice" },
  });
  const context = vm.createContext({
    document, fields, pluginPresentation, CSS: { escape: (value) => value },
    request: { plugins: { items: [plugin("voice", "provider"), plugin("hub", "infrastructure"), plugin("extension", "extension")] } },
    pluginState: { selectedId: "voice", role: "all", managementBusy: false },
    createIcon: () => element("span"), pluginIcon: () => element("span"),
    pluginLiveStatus: () => ({ state: "ready", label: "运行正常" }),
    setError(message) { assert.equal(message, ""); }, notify() {},
  });
  for (const name of ["pluginNode", "pluginFilters", "filteredPlugins", "clearPluginFilters", "renderPluginList",
    "renderSemanticStatus", "selectManagedPlugin", "installLocalPlugin"]) {
    const start = source.search(new RegExp(`^(?:async )?function ${name}\\(`, "m"));
    assert.notEqual(start, -1);
    vm.runInContext(source.slice(start, source.indexOf("\n}", start) + 2), context, { filename: `settings.js:${name}` });
  }
  context.renderPluginPage = () => context.renderPluginList();
  context.runtimePluginController = {
    async install() {
      context.request.plugins.items.push(plugin("installed-hub", "infrastructure"));
      return { installId: "installed-hub" };
    },
  };
  const cards = () => fields.pluginList.querySelectorAll(".plugin-card");
  const tab = (kind) => fields.pluginRoleTabs.querySelector(`[data-plugin-role="${kind}"]`);
  return { context, fields, cards, tab };
}

test("system components stay in the catalog and category navigation keeps selection in view", () => {
  const { context, fields, cards, tab } = fixture();
  context.renderPluginList();
  assert.deepEqual(cards().map((card) => card.dataset.pluginInstallId), ["extension", "voice", "hub"]);
  for (const card of cards()) {
    for (let ancestor = card; ancestor; ancestor = ancestor.parent) assert.equal(ancestor.hidden, false);
  }
  fields.pluginList.scrollTop = 400;
  fields.pluginDetail.scrollTop = 200;
  tab("infrastructure").click();
  assert.deepEqual(cards().map((card) => card.dataset.pluginInstallId), ["hub"]);
  assert.equal(context.pluginState.selectedId, "hub");
  assert.equal(fields.pluginList.scrollTop, 0);
  assert.equal(fields.pluginDetail.scrollTop, 0);
  tab("all").click();
  assert.equal(context.pluginState.selectedId, "extension");
  fields.pluginSearch.value = "no-such-plugin";
  context.renderPluginList();
  assert.equal(cards().length, 0);
  assert.equal(context.pluginState.selectedId, "");
  context.selectManagedPlugin("hub", { reveal: true });
  assert.equal(fields.pluginSearch.value, "");
  assert.equal(context.pluginState.selectedId, "hub");
  assert.equal(cards().find((card) => card.dataset.pluginInstallId === "hub").revealed, "nearest");
});

test("installation clears the previous search and category and reveals the newly installed component", async () => {
  const { context, fields, cards } = fixture();
  context.pluginState.role = "extension";
  fields.pluginSearch.value = "extension";
  context.renderPluginList();
  await context.installLocalPlugin("zip");
  assert.equal(context.pluginState.managementBusy, false);
  assert.equal(context.pluginState.role, "all");
  assert.equal(fields.pluginSearch.value, "");
  assert.equal(context.pluginState.selectedId, "installed-hub");
  const installed = cards().find((card) => card.dataset.pluginInstallId === "installed-hub");
  assert.equal(installed.revealed, "nearest");
  assert.equal(context.document.activeElement, installed);
});
