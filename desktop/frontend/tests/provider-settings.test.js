import assert from "node:assert/strict";
import test from "node:test";

import { createProviderSettingsFeature } from "../settings/provider-settings.js";

const settle = () => new Promise((resolve) => setImmediate(resolve));

// The boundary supplies DOM controls and IPC. All rendering, event handling,
// draft collection, validation and protocol calls use the production feature.
function browserFixture() {
  const dataKey = (name) => name.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
  class Element {
    constructor(tagName) {
      this.tagName = tagName;
      this.children = [];
      this.parentElement = null;
      this.attributes = new Map();
      this.dataset = {};
      this.listeners = new Map();
      this.className = "";
      this.disabled = false;
      this.checked = false;
      this.classList = {
        contains: (name) => this.className.split(/\s+/).includes(name),
        add: (...names) => { this.className = [...new Set([...this.className.split(/\s+/), ...names])].filter(Boolean).join(" "); },
        remove: (...names) => { this.className = this.className.split(/\s+/).filter((name) => !names.includes(name)).join(" "); },
        toggle: (name, force = !this.classList.contains(name)) => {
          this.classList[force ? "add" : "remove"](name);
          return force;
        },
      };
    }
    get value() {
      if (this.tagName !== "select") return this._value ?? "";
      if (this._value === undefined) return this.options[0]?.value || "";
      return this.options.some((option) => option.value === this._value) ? this._value : "";
    }
    set value(value) { this._value = String(value); }
    get options() { return this.children.filter((child) => child.tagName === "option"); }
    get selectedIndex() { return this.options.findIndex((option) => option.value === this.value); }
    get textContent() { return (this.text || "") + this.children.map((child) => child.textContent).join(""); }
    set textContent(value) { this.children.slice().forEach((child) => child.remove()); this.text = String(value); }
    append(...children) {
      for (const child of children) { child.remove(); child.parentElement = this; this.children.push(child); }
    }
    remove() {
      if (this.parentElement) this.parentElement.children.splice(this.parentElement.children.indexOf(this), 1);
      this.parentElement = null;
    }
    setAttribute(name, value) {
      if (name.startsWith("data-")) this.dataset[dataKey(name)] = String(value);
      else this.attributes.set(name, String(value));
    }
    getAttribute(name) {
      return name.startsWith("data-") ? this.dataset[dataKey(name)] ?? null : this.attributes.get(name) ?? null;
    }
    addEventListener(type, listener) {
      if (!this.listeners.has(type)) this.listeners.set(type, new Set());
      this.listeners.get(type).add(listener);
    }
    removeEventListener(type, listener) { this.listeners.get(type)?.delete(listener); }
    async fire(type, detail = {}) {
      const event = { target: this, preventDefault() {}, stopPropagation() {}, ...detail };
      await Promise.all([...this.listeners.get(type) || []].map((listener) => listener(event)));
      await settle();
    }
    matches(selector) {
      const tokens = selector.match(/\[[^\]]+\]|[.#]?[\w-]+/g) || [];
      return tokens.every((token) => {
        if (token[0] === ".") return this.classList.contains(token.slice(1));
        if (token[0] === "#") return this.id === token.slice(1);
        if (token[0] === "[") {
          const [, name, value] = token.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/);
          return value === undefined ? this.getAttribute(name) !== null : this.getAttribute(name) === value;
        }
        return this.tagName === token;
      });
    }
    querySelectorAll(selector) {
      const parts = selector.trim().split(/\s+/);
      const matches = (element) => {
        if (!element.matches(parts.at(-1))) return false;
        let ancestor = element.parentElement;
        for (let index = parts.length - 2; index >= 0; index -= 1) {
          while (ancestor && !ancestor.matches(parts[index])) ancestor = ancestor.parentElement;
          if (!ancestor) return false;
          ancestor = ancestor.parentElement;
        }
        return true;
      };
      const descendants = this.children.flatMap((child) => [child, ...child.querySelectorAll("*")]);
      return selector === "*" ? descendants : descendants.filter(matches);
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  }
  const document = new Element("document");
  document.createElement = (tagName) => new Element(tagName);
  document.getElementById = (id) => document.querySelector(`#${id}`);
  document.body = new Element("body");
  document.append(document.body);
  for (const id of [
    "providerStatusStrip", "providerSearch", "addProviderButton", "providerList", "providerDetail",
    "modelSlots", "contextWindowTokens", "apiTimeout", "apiTemperature", "apiTopPEnabled",
    "apiTopP", "apiMaxTokensEnabled", "apiMaxTokens",
  ]) {
    const element = new Element(id === "addProviderButton" ? "button" : "input");
    element.id = id;
    document.body.append(element);
  }
  return { document, window: { crypto: { randomUUID: () => "new-provider" } } };
}

function ref(modelId = "fixture-model") {
  return { serviceKey: "model.remote", profileId: "fixture", modelId };
}
function snapshot() {
  return { schema_version: 2, window_generation: 4, core_generation_id: "generation-a",
    providers: [{ serviceKey: "model.remote", label: "Remote", profiles: [{ profileId: "fixture", label: "Fixture",
      models: ["fixture-model", "manual-model", "third-model"].map(modelId => ({ modelId, label: modelId })) }] }],
    model_slots: [
      { identity: "core:chat", ownerId: "sakura.core", label: "Chat", required: true, selection: ref() },
      { identity: "core:vision_chat", ownerId: "sakura.core", label: "Vision", required: false,
        selection: { serviceKey: "", profileId: "", modelId: "" } },
    ] };
}
function fixture(initial = snapshot(), intercept = () => undefined) {
  const browser = browserFixture();
  let current = initial;
  const calls = [], dirty = [];
  const feature = createProviderSettingsFeature({ ...browser,
    invoke: async (command, args) => {
      calls.push([command, args]);
      const value = intercept(command, args);
      if (value !== undefined) return value;
      if (command === "settings_provider_model_get") return current;
      if (command === "settings_provider_model_save") {
        current = { ...current, model_slots: current.model_slots.map(slot => ({ ...slot, selection: args.draft.model_slots[slot.identity] })) };
        return { change_plan: "applied" };
      }
      throw new Error(command);
    }, onDirty() { dirty.push(feature.isDirty()); }, onError(error) { throw error; },
  });
  return { ...browser, feature, calls, dirty, setCurrent(value) { current = value; },
    control(kind, slot) { return browser.document.querySelector(`[data-slot-${kind}="${slot}"]`); } };
}

test("slot edits send only neutral references and retain injected identity", async () => {
  const ui = fixture(); await ui.feature.initialize();
  ui.control("model", "core:chat").value = "third-model";
  await ui.control("model", "core:chat").fire("change");
  assert.equal(ui.feature.isDirty(), true);
  await ui.feature.save();
  const [command, payload] = ui.calls.find(([command]) => command.endsWith("_save"));
  assert.equal(command, "settings_provider_model_save");
  assert.equal(payload.windowGeneration, 4);
  assert.equal(payload.coreGenerationId, "generation-a");
  assert.deepEqual(Object.keys(payload.draft), ["model_slots"]);
  assert.deepEqual(payload.draft.model_slots["core:chat"], ref("third-model"));
  assert.equal(ui.feature.isDirty(), false);
});

test("inheritance shows the current chat model and restores a manual draft", async () => {
  const initial = snapshot(); initial.model_slots[1].selection = ref("manual-model");
  const ui = fixture(initial); await ui.feature.initialize();
  ui.control("inherit", "core:vision_chat").checked = true;
  await ui.control("inherit", "core:vision_chat").fire("change");
  assert.equal(ui.control("model", "core:vision_chat").value, "fixture-model");
  assert.equal(ui.control("model", "core:vision_chat").disabled, true);
  ui.control("model", "core:chat").value = "third-model";
  await ui.control("model", "core:chat").fire("change");
  assert.equal(ui.control("model", "core:vision_chat").value, "third-model");
  ui.control("inherit", "core:vision_chat").checked = false;
  await ui.control("inherit", "core:vision_chat").fire("change");
  assert.equal(ui.control("model", "core:vision_chat").value, "manual-model");
  await ui.feature.save();
  assert.deepEqual(ui.calls.find(([command]) => command.endsWith("_save"))[1].draft.model_slots["core:vision_chat"], ref("manual-model"));
});

test("unavailable selections stay visible and save without silently retargeting", async () => {
  const initial = snapshot(); initial.model_slots[0].selection = { serviceKey: "missing", profileId: "old", modelId: "lost" };
  const ui = fixture(initial); await ui.feature.initialize();
  const model = ui.control("model", "core:chat");
  assert.equal(model.value, "lost");
  assert.match(model.options.find(option => option.value === "lost").textContent, /不可用/);
  await ui.feature.save();
  assert.deepEqual(ui.calls.find(([command]) => command.endsWith("_save"))[1].draft.model_slots["core:chat"], initial.model_slots[0].selection);
});

test("invalid configuration remains editable with its recovery explanation", async () => {
  const initial = snapshot();
  initial.configuration_issue = { code: "CONFIG_DATA_INVALID", message: "重新选择模型。" };
  initial.model_slots[0].selection = { serviceKey: "", profileId: "", modelId: "" };
  const ui = fixture(initial); await ui.feature.initialize();
  assert.equal(ui.document.querySelector('[role="alert"]').textContent, initial.configuration_issue.message);
  ui.control("provider", "core:chat").value = JSON.stringify(["model.remote", "fixture"]);
  await ui.control("provider", "core:chat").fire("change");
  await ui.feature.save();
  assert.deepEqual(ui.calls.find(([command]) => command.endsWith("_save"))[1].draft.model_slots["core:chat"], ref());
});

test("partial saves refresh published state before reporting the failed slot", async () => {
  const ui = fixture(snapshot(), command => command.endsWith("_save") ? {
    change_plan: "applied", save_state: "partial", failed_slot: { identity: "plugin:memory:curation" },
  } : undefined);
  await ui.feature.initialize();
  ui.control("model", "core:chat").value = "third-model";
  await ui.control("model", "core:chat").fire("change");
  await assert.rejects(ui.feature.save(), /plugin:memory:curation/);
  assert.equal(ui.control("model", "core:chat").value, "fixture-model");
  assert.equal(ui.feature.isDirty(), false);
});

test("refresh removes retired plugin slots", async () => {
  const initial = snapshot(); initial.model_slots.push({ identity: "plugin:memory:curation", ownerId: "memory", label: "Memory", required: false, selection: ref() });
  const ui = fixture(initial); await ui.feature.initialize();
  assert.equal(ui.feature.hasModelSettings("memory"), true);
  ui.setCurrent(snapshot()); await ui.feature.refreshCurrent();
  assert.equal(ui.feature.hasModelSettings("memory"), false);
  assert.equal(ui.control("model", "plugin:memory:curation"), null);
});
