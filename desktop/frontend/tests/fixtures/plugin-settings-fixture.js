import assert from "node:assert/strict";

import { createPluginSettingsFeature } from "../../settings/plugin-settings.js";

globalThis.CSS = { escape: (value) => String(value) };

export const settle = () => new Promise((resolve) => setImmediate(resolve));

// Only the browser boundary is replaced. The feature renders its real controls,
// handles their events, and calls the real plugin protocol controller.
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
      this.value = "";
      this.disabled = false;
      this.hidden = false;
      this.style = { setProperty() {}, removeProperty() {} };
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
    get childElementCount() { return this.children.length; }
    get firstChild() { return this.children[0] || null; }
    get lastElementChild() { return this.children.at(-1) || null; }
    getAnimations() { return []; }
    showModal() { this.open = true; }
    close() { this.open = false; }
    scrollIntoView(options) { this.revealed = options.block; }
    click() { return this.fire("click"); }
    closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest(selector) || null; }
    prepend(...children) { for (const child of children.reverse()) this.insertBefore(child, this.firstChild); }
    replaceWith(child) { const parent = this.parentElement; if (parent) { parent.insertBefore(child, this); this.remove(); } }
    get childNodes() { return this.children; }
    get textContent() { return (this.text || "") + this.children.map((child) => child.textContent).join(""); }
    set textContent(value) { this.children.slice().forEach((child) => child.remove()); this.text = String(value); }
    append(...children) {
      for (let child of children) {
        if (typeof child !== "object") { const text = new Element("#text"); text.textContent = child; child = text; }
        child.remove(); child.parentElement = this; this.children.push(child);
      }
    }
    remove() {
      if (this.parentElement) this.parentElement.children.splice(this.parentElement.children.indexOf(this), 1);
      this.parentElement = null;
    }
    insertBefore(child, reference) {
      child.remove();
      child.parentElement = this;
      const index = this.children.indexOf(reference);
      this.children.splice(index < 0 ? this.children.length : index, 0, child);
    }
    setAttribute(name, value) {
      if (name.startsWith("data-")) this.dataset[dataKey(name)] = String(value);
      else this.attributes.set(name, String(value));
    }
    getAttribute(name) {
      return name.startsWith("data-") ? this.dataset[dataKey(name)] ?? null : this.attributes.get(name) ?? null;
    }
    hasAttribute(name) { return this.getAttribute(name) !== null; }
    removeAttribute(name) {
      if (name.startsWith("data-")) delete this.dataset[dataKey(name)];
      else this.attributes.delete(name);
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
    contains(element) { return element === this || this.children.some((child) => child.contains(element)); }
    focus() { document.activeElement = this; }
    setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }
    matches(selector) {
      if (selector.includes(",")) return selector.split(",").some((part) => this.matches(part.trim()));
      if (selector.includes(":not(:disabled)")) return !this.disabled && this.matches(selector.replace(":not(:disabled)", ""));
      if (selector.includes(":invalid")) return false;
      const tokens = selector.match(/\[[^\]]+\]|[.#]?[\w-]+/g) || [];
      return tokens.every((token) => {
        if (token[0] === ".") return this.classList.contains(token.slice(1));
        if (token[0] === "#") return this.id === token.slice(1);
        if (token[0] === "[") {
          const [, name, value] = token.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/);
          return value === undefined ? this.hasAttribute(name) : this.getAttribute(name) === value;
        }
        return this.tagName === token;
      });
    }
    querySelectorAll(selector) {
      const selectors = selector.split(",").map((part) => part.trim().split(/\s+/));
      const matches = (element, parts) => {
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
      return selector === "*" ? descendants : descendants.filter((element) => selectors.some((parts) => matches(element, parts)));
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  }
  const document = new Element("document");
  document.createElement = (tagName) => new Element(tagName);
  document.getElementById = (id) => document.querySelector(`#${id}`);
  document.body = new Element("body");
  document.append(document.body);
  const shell = new Element("main");
  shell.className = "settings-shell";
  document.body.append(shell);
  for (const id of [
    "pluginTotal", "pluginRoleTabs", "pluginSearch", "pluginInstallMenuRoot", "pluginInstallMenuButton", "pluginInstallMenu",
    "pluginInstallZipButton", "pluginInstallFolderButton", "pluginList", "pluginDetail",
    "aboutComponentsSummary", "aboutComponentsRefresh", "aboutComponentsState", "aboutComponentsList",
    "memorySurface", "page-memory", "page-plugins", "page-about",
  ]) {
    const element = new Element("div");
    element.id = id;
    shell.append(element);
  }
  document.getElementById("pluginInstallMenu").hidden = true;
  const timers = new Map();
  let nextTimer = 0;
  const window = {
    matchMedia: () => ({ matches: true }),
    setTimeout(callback, milliseconds) { const id = ++nextTimer; timers.set(id, { callback, milliseconds }); return id; },
    clearTimeout(id) { timers.delete(id); },
  };
  return {
    document, window, timers,
    async runTimers(milliseconds) {
      for (const [id, timer] of [...timers]) {
        if (timer.milliseconds !== milliseconds || !timers.has(id)) continue;
        timers.delete(id);
        timer.callback();
        await settle();
      }
    },
  };
}

export function field(key, overrides = {}) {
  return {
    key, label: key, type: "string", default: "", description: "", options: [],
    minimum: null, maximum: null, step: null, maxLength: 1000, placement: "row",
    actionIds: [], enabledWhen: null, required: false, readonly: false, copyable: false,
    restartRequired: false, ...overrides,
  };
}

export function snapshot(coreGenerationId = "generation-a", label = "fixture") {
  return {
    schemaVersion: 1, revision: "0123456789abcdef", state: "ready", reasonCode: "READY",
    windowGeneration: 7, coreGenerationId,
    plugins: [{
      installId: "pi_bundled_666978747572655f706c7567696e", pluginId: "fixture_plugin", name: "Fixture Plugin",
      version: "1.0.0", author: "Sakura Tests", description: "Fixture", enabled: true, required: false,
      supported: true, source: "bundled", canUninstall: false, provides: ["fixture.service"],
      requires: ["sakura.host.settings"], missingServices: [], state: "active", reasonCode: "ACTIVE",
      sections: [{
        sectionId: "general", title: "General", surface: null, reasonCode: "READY",
        fields: [field("label", { value: label })], values: { label }, actions: [], collections: [],
      }, {
        sectionId: "archive", title: "Memory", surface: "memory", reasonCode: "READY",
        fields: [], values: {}, actions: [], collections: [{
          collectionId: "entries", title: "Memory", description: "",
          columns: [{ key: "content", label: "内容", type: "string", maxLength: 1000 }],
          fields: [field("content", { required: true })], filters: [], searchable: true,
          pageSize: 20, canCreate: true, canUpdate: true, canDelete: true, deleteConfirmation: "删除？",
        }],
      }],
    }],
  };
}

export function featureFixture(invoke, options = {}) {
  const browser = browserFixture();
  let dirtyNotifications = 0;
  const errors = [];
  const feature = createPluginSettingsFeature({
    ...browser, invoke,
    onDirty: () => { dirtyNotifications += 1; }, onError: (error) => { if (error) errors.push(error); },
    notify() {}, confirmAction: async () => true, enhanceSelect() {},
    removeOverlayAfterExit: async (overlay) => overlay.remove(), showPage() {},
    isMemoryTransitioning: () => false, hasPendingCharacterSelection: () => false,
    refreshSelect() {}, closeSelects() {}, focusSelect: (control) => control?.focus(),
    replayMotion() {}, getVoiceController: () => null,
    ...options,
  });
  return {
    ...browser, feature, errors, dirtyNotifications: () => dirtyNotifications,
    openSettings: () => browser.document.querySelector(".plugin-configure").fire("click"),
  };
}

export function queryResult(itemId, content = itemId) {
  return { items: [{ itemId, values: { content } }], total: 1, nextCursor: null };
}
