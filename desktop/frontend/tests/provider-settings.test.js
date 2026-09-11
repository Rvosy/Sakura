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

function snapshot() {
  const slot = (id, required, model) => ({
    identity: `core:${id}`, ownerType: "core", ownerId: "sakura.core", slotId: id,
    label: id, description: id, modelKind: "chat_completion", required, order: 10,
    reasonCode: "READY", selection: { profile_id: model ? "fixture" : "", model },
  });
  return {
    schema_version: 1, window_generation: 4, core_generation_id: "generation-a",
    providers: [{
      id: "fixture", alias: "Fixture", base_url: "https://fixture.invalid/v1", configured: true,
      models: ["fixture-model", "manual-model", "third-model"],
    }],
    model_slots: [
      { ...slot("chat", true, "fixture-model"), selection: {
        profile_id: "fixture", model: "fixture-model", context_window_tokens: 131072,
      } },
      slot("vision_chat", false, ""),
    ],
    settings: { timeout_seconds: 30, temperature: null, top_p: null, max_tokens: null },
    setup_complete: true, change_plans: ["applied"],
  };
}

function featureFixture(initial = snapshot(), intercept = () => undefined) {
  const browser = browserFixture();
  let current = initial;
  const calls = [];
  const errors = [];
  const notifications = [];
  const pages = [];
  const dirtyStates = [];
  let dirtyNotifications = 0;
  const feature = createProviderSettingsFeature({
    ...browser,
    invoke: async (command, args) => {
      calls.push([command, args]);
      const intercepted = intercept(command, args);
      if (intercepted !== undefined) return intercepted;
      if (command === "settings_provider_model_get") return current;
      if (command === "settings_provider_model_save") {
        current = {
          ...current,
          providers: args.draft.providers.map(({ credential, ...provider }) => ({
            ...provider, configured: credential.action !== "clear",
          })),
          model_slots: current.model_slots.map((slot) => ({
            ...slot, selection: args.draft.model_slots[slot.identity],
          })),
          settings: args.draft.settings,
        };
        return { change_plan: "applied" };
      }
      if (command === "settings_provider_model_cancel") return true;
      throw new Error(`unexpected command ${command}`);
    },
    onDirty() { dirtyNotifications += 1; dirtyStates.push(feature.isDirty()); },
    onError(message) { errors.push(String(message)); },
    notify(message, type) { notifications.push([message, type]); },
    showPage(page) { pages.push(page); feature.onPageChanged(page); },
    enhanceSelect() {},
    refreshSelect() {},
    markInvalid(input, invalid) { input?.classList.toggle("is-invalid", Boolean(invalid)); },
    setControlDisabled(control, disabled) { control.disabled = Boolean(disabled); },
    setNumericBounds(input, bounds) { [input.min, input.max] = bounds.map(String); },
  });
  const { document } = browser;
  return {
    ...browser, feature, calls, errors, notifications, pages, dirtyStates,
    get dirtyNotifications() { return dirtyNotifications; },
    field: (key) => document.querySelector(`[data-provider-field="${key}"]`),
    control: (id) => document.getElementById(id),
    button: (text, container = document.getElementById("providerDetail")) => (
      container.querySelectorAll("button").find((button) => button.textContent === text)
    ),
    setCurrent(value) { current = value; },
  };
}

test("real provider edits preserve keep, clear and replace credentials and rebase from one save readback", async (t) => {
  for (const action of ["keep", "clear", "replace"]) {
    await t.test(action, async () => {
      const ui = featureFixture();
      await ui.feature.initialize();
      assert.equal(ui.feature.isDirty(), false);
      assert.equal(ui.field("api_key").value, "");
      ui.field("alias").value = "Renamed";
      const aliasInput = ui.field("alias");
      await aliasInput.fire("input");
      assert.equal(ui.field("alias"), aliasInput, "typing must preserve the focused detail control");
      if (action === "keep") {
        ui.field("api_key").value = "discarded-fixture-key";
        await ui.field("api_key").fire("input");
        ui.field("api_key").value = "";
        await ui.field("api_key").fire("input");
      }
      if (action === "clear") await ui.button("清除凭据").fire("click");
      if (action === "replace") {
        ui.field("api_key").value = " transient-fixture-key ";
        await ui.field("api_key").fire("input");
      }
      assert.equal(ui.feature.isDirty(), true);
      const notificationsBeforeSave = ui.dirtyNotifications;
      await ui.feature.save();
      const saves = ui.calls.filter(([command]) => command === "settings_provider_model_save");
      assert.equal(saves.length, 1);
      assert.deepEqual(saves[0][1].draft.providers[0].credential, {
        action, value: action === "replace" ? "transient-fixture-key" : "",
      });
      assert.equal(saves[0][1].draft.providers[0].alias, "Renamed");
      assert.equal(saves[0][1].windowGeneration, 4);
      assert.equal(ui.calls.filter(([command]) => command === "settings_provider_model_get").length, 2);
      assert.equal(ui.field("api_key").value, "");
      assert.equal(ui.feature.isDirty(), false);
      assert.equal(ui.dirtyNotifications - notificationsBeforeSave, 1);
      assert.equal(Boolean(ui.button("清除凭据")), action !== "clear");
      ui.feature.dispose();
    });
  }
});

test("model controls preserve manual inheritance drafts and collect optional numeric settings", async () => {
  const initial = snapshot();
  initial.model_slots[1].selection = { profile_id: "fixture", model: "manual-model" };
  const ui = featureFixture(initial);
  await ui.feature.initialize();
  const slotControl = (kind, slot) => ui.document.querySelector(`[data-slot-${kind}="core:${slot}"]`);
  const inherit = slotControl("inherit", "vision_chat");
  inherit.checked = true;
  await inherit.fire("change");
  assert.equal(slotControl("model", "vision_chat").value, "fixture-model");
  assert.equal(slotControl("model", "vision_chat").disabled, true);
  slotControl("model", "chat").value = "third-model";
  await slotControl("model", "chat").fire("change");
  assert.equal(slotControl("model", "vision_chat").value, "third-model");
  inherit.checked = false;
  await inherit.fire("change");
  assert.equal(slotControl("model", "vision_chat").value, "manual-model");
  assert.equal(slotControl("model", "vision_chat").disabled, false);
  ui.control("contextWindowTokens").value = "1000000";
  ui.control("apiTemperature").value = "0";
  ui.control("apiTopPEnabled").checked = true;
  await ui.control("apiTopPEnabled").fire("change");
  ui.control("apiTopP").value = "0";
  ui.control("apiMaxTokensEnabled").checked = true;
  await ui.control("apiMaxTokensEnabled").fire("change");
  ui.control("apiMaxTokens").value = "4096";
  assert.equal(ui.control("apiTopP").disabled, false);
  assert.equal(ui.control("apiMaxTokens").disabled, false);
  await ui.feature.save();
  const draft = ui.calls.find(([command]) => command === "settings_provider_model_save")[1].draft;
  assert.deepEqual(draft.settings, { timeout_seconds: 30, temperature: 0, top_p: 0, max_tokens: 4096 });
  assert.deepEqual(draft.model_slots["core:vision_chat"], { profile_id: "fixture", model: "manual-model" });
  assert.equal(draft.model_slots["core:chat"].context_window_tokens, 1000000);
  assert.equal(ui.feature.isDirty(), false);
});

test("unavailable provider/model references remain visible and block save until the real slot is repaired", async () => {
  const initial = snapshot();
  initial.model_slots.push({
    ...initial.model_slots[1], identity: "plugin:sakura.memory.mem0:curation", ownerType: "plugin",
    ownerId: "sakura.memory.mem0", slotId: "curation", label: "Curation",
    selection: { profile_id: "removed", model: "removed-model" },
  });
  const ui = featureFixture(initial);
  await ui.feature.initialize();
  assert.equal(ui.feature.hasModelSettings("sakura.memory.mem0"), true);
  assert.equal(ui.feature.hasModelSettings("another.plugin"), false);
  const select = ui.document.querySelector('[data-slot-model="plugin:sakura.memory.mem0:curation"]');
  assert.equal(select.value, "removed-model");
  assert.match(select.options[0].textContent, /原选择不可用/);
  await assert.rejects(ui.feature.save(), /未通过校验/);
  assert.equal(ui.calls.some(([command]) => command === "settings_provider_model_save"), false);
  assert.equal(ui.pages.at(-1), "model");
  assert.match(ui.errors.at(-1), /Curation/);
  ui.setCurrent(snapshot());
  await ui.feature.refreshCurrent();
  assert.equal(ui.feature.hasModelSettings("sakura.memory.mem0"), false);
  assert.equal(ui.document.querySelector('[data-slot-model="plugin:sakura.memory.mem0:curation"]'), null);
});

test("provider and model edits immediately publish dirty state and clear it when restored", async (t) => {
  for (const [name, select, event, changed, original] of [
    ["provider input", (ui) => ui.field("alias"), "input", "Pending name", "Fixture"],
    ["model selection", (ui) => ui.document.querySelector('[data-slot-model="core:chat"]'), "change", "third-model", "fixture-model"],
    ["numeric option", (ui) => ui.control("apiTimeout"), "input", "60", "30"],
    ["optional setting", (ui) => ui.control("apiTopPEnabled"), "change", true, false],
    ["slot inheritance", (ui) => ui.document.querySelector('[data-slot-inherit="core:vision_chat"]'), "change", false, true],
  ]) {
    await t.test(name, async () => {
      const ui = featureFixture();
      await ui.feature.initialize();
      const control = select(ui);
      const property = typeof changed === "boolean" ? "checked" : "value";
      control[property] = changed;
      await control.fire(event);
      assert.equal(ui.feature.isDirty(), true);
      assert.equal(ui.dirtyStates.at(-1), true, "the visible draft marker must update on the edit");
      control[property] = original;
      await control.fire(event);
      assert.equal(ui.feature.isDirty(), false);
      assert.equal(ui.dirtyStates.at(-1), false, "restoring the saved value must clear the marker");
      const notifications = ui.dirtyNotifications;
      ui.feature.dispose();
      if (name === "numeric option" || name === "optional setting") {
        await control.fire(event);
        assert.equal(ui.dirtyNotifications, notifications, "disposed controls must detach their listeners");
      }
    });
  }
});

test("adding and removing a model updates the draft marker without another page refresh", async () => {
  const ui = featureFixture();
  await ui.feature.initialize();
  const input = ui.control("providerDetail").querySelector(".model-add-row input");
  input.value = "temporary-model";
  await input.fire("keydown", { key: "Enter" });
  assert.equal(ui.dirtyStates.at(-1), true);
  const remove = ui.control("providerDetail").querySelectorAll("button")
    .find((button) => button.getAttribute("aria-label") === "移除 temporary-model");
  await remove.fire("click");
  assert.equal(ui.feature.isDirty(), false);
  assert.equal(ui.dirtyStates.at(-1), false);
});

test("partial save replaces secret drafts with actual state before reporting the failed slot", async () => {
  const ui = featureFixture(snapshot(), (command) => command === "settings_provider_model_save" ? {
    change_plan: "applied", save_state: "partial", failed_slot: { identity: "plugin:fixture:curation" },
  } : undefined);
  await ui.feature.initialize();
  ui.field("api_key").value = "transient-fixture-key";
  await ui.field("api_key").fire("input");
  const applied = snapshot();
  applied.providers[0].alias = "Actual saved name";
  ui.setCurrent(applied);
  await assert.rejects(ui.feature.save(), /plugin:fixture:curation/);
  assert.equal(ui.field("api_key").value, "");
  assert.equal(ui.field("alias").value, "Actual saved name");
  assert.equal(ui.feature.isDirty(), false);
});

test("provider chooser, search and manual model events edit the same draft consumed by save", async () => {
  const ui = featureFixture();
  await ui.feature.initialize();
  await ui.control("addProviderButton").fire("click");
  const chooser = ui.document.querySelector(".provider-add-dialog");
  await ui.button("自定义", chooser).fire("click");
  assert.equal(ui.document.querySelector(".confirm-overlay"), null);
  ui.field("base_url").value = "https://new.invalid/v1";
  await ui.field("base_url").fire("input");
  const modelInput = ui.control("providerDetail").querySelector(".model-add-row input");
  modelInput.value = " new-model ";
  await modelInput.fire("keydown", { key: "Enter" });
  ui.control("providerSearch").value = "new.invalid";
  await ui.control("providerSearch").fire("input");
  assert.equal(ui.control("providerList").children.length, 1);
  ui.feature.onPageChanged("model");
  await ui.feature.save();
  const draft = ui.calls.find(([command]) => command === "settings_provider_model_save")[1].draft;
  assert.equal(draft.providers.length, 2);
  assert.deepEqual(draft.providers[1], {
    id: "new-provider", alias: "新模型服务", base_url: "https://new.invalid/v1",
    models: ["new-model"], credential: { action: "clear", value: "" },
  });
});

test("Google preset discovers and probes the selected model with the official endpoint", async () => {
  const ui = featureFixture(snapshot(), (command, args) => {
    if (command !== "settings_provider_model_probe") return undefined;
    return args.kind === "list_models"
      ? { models: ["gemini-fixture"] }
      : { message: "OK" };
  });
  await ui.feature.initialize();
  await ui.control("addProviderButton").fire("click");
  await ui.button("Google 官方", ui.document.querySelector(".provider-add-dialog")).fire("click");
  assert.equal(ui.field("base_url").value, "https://generativelanguage.googleapis.com/v1beta/openai");
  assert.equal(ui.field("api_key").value, "");
  ui.field("api_key").value = "fixture-google-key";
  await ui.field("api_key").fire("input");
  await ui.button("获取模型列表").fire("click");
  await ui.button("添加", ui.document.querySelector(".model-picker-dialog")).fire("click");
  await ui.button("测试连接").fire("click");
  const probes = ui.calls.filter(([command]) => command === "settings_provider_model_probe");
  assert.deepEqual(probes.map(([, args]) => args.kind), ["list_models", "test_connection"]);
  for (const [, args] of probes) {
    assert.equal(args.profile.base_url, "https://generativelanguage.googleapis.com/v1beta/openai");
    assert.deepEqual(args.profile.credential, { action: "replace", value: "fixture-google-key" });
  }
  assert.equal(probes[1][1].profile.model, "gemini-fixture");
  await ui.feature.save();
  const draft = ui.calls.find(([command]) => command === "settings_provider_model_save")[1].draft;
  assert.equal(draft.providers[1].base_url, probes[1][1].profile.base_url);
  assert.deepEqual(draft.providers[1].models, ["gemini-fixture"]);
});

test("model discovery adds only selected new models and connection testing reports the actual probe result", async () => {
  let failure = false;
  const ui = featureFixture(snapshot(), (command, args) => {
    if (command !== "settings_provider_model_probe") return undefined;
    if (failure) return Promise.reject(new Error("PROVIDER_HTTP_ERROR: HTTP 429"));
    return args.kind === "list_models"
      ? { models: ["fixture-model", "detected-model"] }
      : { message: "fixture accepted" };
  });
  await ui.feature.initialize();
  await ui.button("获取模型列表").fire("click");
  const picker = ui.document.querySelector(".model-picker-dialog");
  assert.deepEqual(picker.querySelectorAll("input").map((input) => input.checked), [false, true]);
  await ui.button("添加", picker).fire("click");
  assert.equal(ui.document.querySelector(".confirm-overlay"), null);
  await ui.feature.save();
  const draft = ui.calls.find(([command]) => command === "settings_provider_model_save")[1].draft;
  assert.deepEqual(draft.providers[0].models, ["fixture-model", "manual-model", "third-model", "detected-model"]);
  const button = ui.button("测试连接");
  await button.fire("click");
  const connection = ui.calls.filter(([command]) => command === "settings_provider_model_probe").at(-1)[1];
  assert.equal(connection.kind, "test_connection");
  assert.equal(connection.profile.model, "fixture-model");
  assert.match(ui.notifications.at(-1)[0], new RegExp(connection.profile.model));
  assert.equal(button.disabled, false);
  failure = true;
  await button.fire("click");
  assert.match(ui.document.querySelector(".provider-probe-error p").textContent, /PROVIDER_HTTP_ERROR: HTTP 429/);
  assert.doesNotMatch(ui.errors.at(-1), /HTTP 429/);
  assert.equal(button.disabled, false);
});

test("provider probe failures separate known causes from safe diagnostic details and clear details on retry", async () => {
  let error = "";
  const ui = featureFixture(snapshot(), (command) => {
    if (command !== "settings_provider_model_probe") return undefined;
    return error ? Promise.reject(new Error(error)) : {};
  });
  await ui.feature.initialize();
  for (const [code, message] of [
    ["AUTHENTICATION_FAILED", "验证失败，请检查 API Key。"],
    ["PROVIDER_ACCESS_FORBIDDEN", "服务拒绝访问。"],
    ["PROVIDER_TIMEOUT", "请求超时。"],
  ]) {
    error = `${code}|providers.test_connection||HTTP 403; code=access_denied; <img src=x>`;
    await ui.button("测试连接").fire("click");
    assert.equal(ui.errors.at(-1), message);
    const details = ui.document.querySelector(".provider-probe-error");
    assert.match(details.textContent, /HTTP 403; code=access_denied; <img src=x>/);
    assert.equal(details.querySelector("img"), null);
    assert.equal(Boolean(details.open), false);
    error = "";
    await ui.button("测试连接").fire("click");
    assert.equal(ui.document.querySelector(".provider-probe-error"), null);
    assert.equal(ui.errors.at(-1), "");
  }
});

test("probe and cancellation use rebound identity; disposal removes owned overlays and ignores late results", async () => {
  let finishProbe;
  const ui = featureFixture(snapshot(), (command) => {
    if (command === "settings_provider_model_probe") return new Promise((resolve) => { finishProbe = resolve; });
    return undefined;
  });
  await ui.feature.initialize();
  ui.field("alias").value = "Pending";
  await ui.field("alias").fire("input");
  ui.feature.rebindIdentity("generation-b");
  assert.equal(ui.feature.isDirty(), true);
  ui.control("apiTimeout").value = "300";
  const probing = ui.button("获取模型列表").fire("click");
  await settle();
  const probe = ui.calls.find(([command]) => command === "settings_provider_model_probe")[1];
  assert.equal(probe.coreGenerationId, "generation-b");
  assert.equal(probe.kind, "list_models");
  assert.equal(probe.profile.timeout_seconds, 60);
  assert.deepEqual(probe.profile.credential, { action: "keep", value: "" });
  await ui.feature.cancelOperations();
  const cancellation = ui.calls.find(([command]) => command === "settings_provider_model_cancel")[1];
  assert.equal(cancellation.operationId, probe.operationId);
  assert.equal(cancellation.coreGenerationId, "generation-b");
  await ui.control("addProviderButton").fire("click");
  assert.ok(ui.document.querySelector(".confirm-overlay"));
  ui.feature.dispose();
  assert.equal(ui.document.querySelector(".confirm-overlay"), null);
  await ui.control("addProviderButton").fire("click");
  finishProbe({ models: ["late-model"] });
  await probing;
  assert.equal(ui.document.querySelector(".confirm-overlay"), null);
  assert.equal(ui.feature.isDirty(), false);
  assert.deepEqual(ui.notifications, []);
  assert.deepEqual(ui.errors, [""]);
});

for (const change of ["remove", "refresh", "rebind"]) {
  for (const outcome of ["success", "failure"]) {
    test(`model discovery ignores late ${outcome} after provider ${change}`, async () => {
      let finish;
      const ui = featureFixture(snapshot(), (command) => {
        if (command === "settings_provider_model_probe") {
          return new Promise((resolve, reject) => {
            finish = () => outcome === "success"
              ? resolve({ models: ["stale-model"] })
              : reject(new Error("STALE_DISCOVERY_FAILURE"));
          });
        }
        return undefined;
      });
      await ui.feature.initialize();
      const probing = ui.button("获取模型列表").fire("click");
      await settle();
      if (change === "remove") await ui.button("删除模型服务").fire("click");
      if (change === "refresh") await ui.feature.refreshCurrent();
      if (change === "rebind") ui.feature.rebindIdentity("generation-b");
      const dirtyNotifications = ui.dirtyNotifications;
      finish();
      await probing;
      assert.equal(ui.document.querySelector(".model-picker-dialog"), null);
      assert.deepEqual(ui.notifications, []);
      assert.deepEqual(ui.errors, [""]);
      assert.equal(ui.dirtyNotifications, dirtyNotifications);
      ui.feature.dispose();
    });
  }

  test(`an open model picker is invalidated by provider ${change}`, async () => {
    const ui = featureFixture(snapshot(), (command) => command === "settings_provider_model_probe"
      ? { models: ["stale-model"] } : undefined);
    await ui.feature.initialize();
    await ui.button("获取模型列表").fire("click");
    const submit = ui.button("添加", ui.document.querySelector(".model-picker-dialog"));
    if (change === "remove") await ui.button("删除模型服务").fire("click");
    if (change === "refresh") await ui.feature.refreshCurrent();
    if (change === "rebind") ui.feature.rebindIdentity("generation-b");
    assert.equal(ui.document.querySelector(".model-picker-dialog"), null);
    const dirtyNotifications = ui.dirtyNotifications;
    await submit.fire("click");
    assert.equal(ui.dirtyNotifications, dirtyNotifications);
    assert.deepEqual(ui.notifications, []);
    if (change !== "remove") {
      await ui.feature.save();
      const draft = ui.calls.find(([command]) => command === "settings_provider_model_save")[1].draft;
      assert.equal(draft.providers[0].models.includes("stale-model"), false);
    }
    ui.feature.dispose();
  });
}

test("a newer discovery supersedes an older request for the same provider", async () => {
  const finish = [];
  const ui = featureFixture(snapshot(), (command) => command === "settings_provider_model_probe"
    ? new Promise((resolve) => finish.push(resolve)) : undefined);
  await ui.feature.initialize();
  const first = ui.button("获取模型列表").fire("click");
  await settle();
  // Reselecting the provider renders a new detect button while its first request is pending.
  await ui.control("providerList").querySelector(".provider-card").fire("click");
  const second = ui.button("获取模型列表").fire("click");
  await settle();
  finish[1]({ models: ["current-model"] });
  await second;
  finish[0]({ models: ["stale-model"] });
  await first;
  assert.equal(ui.document.querySelectorAll(".model-picker-dialog").length, 1);
  await ui.button("添加", ui.document.querySelector(".model-picker-dialog")).fire("click");
  await ui.feature.save();
  const draft = ui.calls.find(([command]) => command === "settings_provider_model_save")[1].draft;
  assert.equal(draft.providers[0].models.includes("current-model"), true);
  assert.equal(draft.providers[0].models.includes("stale-model"), false);
  ui.feature.dispose();
});
