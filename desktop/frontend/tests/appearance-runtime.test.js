import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import {
  createRuntimeAppearanceController,
  toLegacyTheme,
  validateAppearanceSnapshot,
  validateAppearanceValues,
} from "../settings/appearance-runtime.js";

const limits = Object.freeze({
  portraitScalePercent: [50, 150, 100],
  controlPanelWidth: [420, 760, 640],
  bubbleMaxHeight: [96, 260, 128],
  controlPanelVerticalOffset: [-60, 160, 0],
  inputBarOffset: [0, 60, 0],
  speechFontSize: [10, 24, 19],
  nameFontSize: [10, 20, 13],
  inputFontSize: [12, 20, 15],
});
const themeTokens = Object.freeze({
  primary: "#112233",
  primaryHover: "#223344",
  accent: "#334455",
  text: "#445566",
  secondaryText: "#556677",
  mutedText: "#667788",
  pageBackground: "#778899",
  panelBackground: "#8899aa",
  inputBackground: "#99aabb",
  bubbleBackground: "#aabbcc",
  border: "#bbccdd",
});
const values = Object.freeze({
  portraitScalePercent: 125,
  controlPanelWidth: 640,
  bubbleMaxHeight: 128,
  controlPanelVerticalOffset: 0,
  inputBarOffset: 0,
  speechFontSize: 20,
  nameFontSize: 14,
  inputFontSize: 16,
  themeTokens,
});

test("appearance values validate exact theme and bounded scalar fields", () => {
  assert.deepEqual(validateAppearanceValues(values, limits), values);
  assert.throws(() => validateAppearanceValues({ ...values, portraitScalePercent: 151 }, limits));
  assert.throws(() => validateAppearanceValues({ ...values, themeTokens: { ...themeTokens, token: "secret" } }, limits));
  assert.throws(() => validateAppearanceValues({ ...values, themeTokens: { ...themeTokens, accent: "url(file)" } }, limits));
});

test("runtime theme fields map onto the unchanged legacy settings controls", () => {
  const legacy = toLegacyTheme(themeTokens);
  assert.equal(legacy.primary_color, themeTokens.primary);
  assert.equal(legacy.primary_hover_color, themeTokens.primaryHover);
  assert.equal(legacy.bubble_background_color, themeTokens.bubbleBackground);
  assert.deepEqual(Object.keys(legacy), [
    "primary_color",
    "primary_hover_color",
    "accent_color",
    "text_color",
    "secondary_text_color",
    "muted_text_color",
    "page_background_color",
    "panel_background_color",
    "input_background_color",
    "bubble_background_color",
    "border_color",
  ]);
});

test("settings snapshot binds Rust-injected window core and character identity", () => {
  const snapshot = {
    schemaVersion: 1,
    windowGeneration: 4,
    limits,
    presentation: {
      generationId: "generation-a",
      characterId: "Sakura",
      portraitKeys: ["__default__", "happy"],
      portraitResourceUrls: { __default__: "sakura-character://default", happy: "sakura-character://happy" },
    },
    appearance: {
      schemaVersion: 2,
      coreGenerationId: "generation-a",
      characterId: "Sakura",
      values,
    },
  };
  assert.equal(validateAppearanceSnapshot(snapshot).appearance.values.portraitScalePercent, 125);
  assert.throws(() => validateAppearanceSnapshot({ ...snapshot, appearance: { ...snapshot.appearance, coreGenerationId: "old" } }));
});

test("runtime settings frontend owns no data path, character selection, or forged identity", () => {
  const source = readFileSync(new URL("../settings/appearance-runtime.js", import.meta.url), "utf8");
  const entry = readFileSync(new URL("../settings/settings.js", import.meta.url), "utf8");
  const markup = readFileSync(new URL("../settings/index.html", import.meta.url), "utf8");
  const styles = readFileSync(new URL("../settings/styles.css", import.meta.url), "utf8");
  assert.doesNotMatch(source, /data[\\/]|current_character_id|generationId\s*:|characterId\s*:/);
  for (const id of [
    "characterSelect",
    "portraitScale",
    "controlPanelWidth",
    "bubbleHeight",
    "controlPanelOffset",
    "inputBarOffset",
    "speechFontSize",
    "nameFontSize",
    "inputFontSize",
    "themeColors",
    "visualEffectMode",
    "themeAiButton",
    "resetThemeButton",
  ]) assert.match(markup, new RegExp(`id="${id}"`), id);
  assert.doesNotMatch(markup, /id="buttonFontSize"/);
  assert.doesNotMatch(markup, /runtime(Character|Appearance|Portrait|Theme|Initial)/);
  assert.doesNotMatch(styles, /runtime-settings-panel|runtime-theme-grid|runtime-portrait-card/);
  assert.doesNotMatch(styles, /#page-character\s*>\s*:not|#page-appearance\s*>\s*:not/);
  assert.match(entry, /settings_character_appearance_get/);
  assert.match(entry, /RUNTIME_LAYOUT_DEFAULTS/);
  assert.match(entry, /fields\.characterSelect,[\s\S]*?fields\.themeAiButton,[\s\S]*?themeEditor\.pick/);
  assert.match(source, /settings_character_appearance_preview/);
  assert.match(source, /settings_character_appearance_save/);
  assert.match(source, /settings_character_appearance_cancel_preview/);
  assert.doesNotMatch(source, /Core 已更新；未提交预览已恢复/);
  assert.doesNotMatch(source, /#applyButton, #saveButton/);
});

test("Core generation replacement rebinds appearance in place and keeps global save actions usable", async () => {
  class Control {
    constructor() {
      this.value = "";
      this.disabled = false;
      this.listeners = {};
      this.output = { textContent: "" };
      this.parentElement = { querySelector: () => this.output };
      this.style = { setProperty() {} };
    }

    addEventListener(type, listener) { this.listeners[type] = listener; }
    fire(type) { this.listeners[type]?.(); }
  }

  const controls = Object.fromEntries([
    "portraitScale", "controlPanelWidth", "bubbleHeight", "controlPanelOffset",
    "inputBarOffset", "speechFontSize", "nameFontSize", "inputFontSize",
    "themeColors", "resetThemeButton", "applyButton", "saveButton",
  ].map((id) => [id, new Control()]));
  const themes = Object.fromEntries(Object.keys(toLegacyTheme(themeTokens)).map((id) => [id, new Control()]));
  const document = {
    getElementById: (id) => controls[id],
    querySelector: (selector) => themes[selector.match(/data-theme-field="([^"]+)"/)?.[1]],
    querySelectorAll: () => [],
  };
  const makeSnapshot = (generationId) => ({
    schemaVersion: 1,
    windowGeneration: 4,
    limits,
    presentation: {
      generationId,
      characterId: "Sakura",
      displayName: "夜乃桜",
      themeTokens,
      portraitKeys: ["__default__"],
      portraitResourceUrls: { __default__: "sakura-character://default" },
    },
    appearance: { schemaVersion: 2, coreGenerationId: generationId, characterId: "Sakura", values },
  });
  let intervalCallback = null;
  let nextFrame = null;
  const calls = [];
  const previousWindow = globalThis.window;
  globalThis.window = {
    setInterval(callback) { intervalCallback = callback; return 1; },
    clearInterval() {},
    requestAnimationFrame(callback) { nextFrame = callback; return 2; },
    cancelAnimationFrame() { nextFrame = null; },
  };
  try {
    const controller = createRuntimeAppearanceController({
      document,
      invoke: async (command, args) => {
        calls.push([command, args]);
        if (command === "runtime_lifecycle_snapshot") {
          return { supervisor: { generationId: "generation-b" } };
        }
        if (command === "settings_character_appearance_get") return makeSnapshot("generation-b");
        if (command === "settings_character_appearance_save") {
          return { coreGenerationId: "generation-b", characterId: "Sakura", values: args.values };
        }
        return {};
      },
      onDirty() {},
      onError(error) { throw new Error(error); },
      prepare() {},
      fillTheme(theme) {
        for (const [id, value] of Object.entries(theme)) themes[id].value = value;
      },
      wait: async () => {},
    });
    await controller.initialize(makeSnapshot("generation-a"));
    controls.portraitScale.value = "135";
    controls.portraitScale.fire("input");
    assert.equal(controller.isDirty(), true);
    await intervalCallback();
    assert.equal(controller.isDirty(), true);
    assert.equal(controls.portraitScale.value, "135");
    assert.equal(controls.applyButton.disabled, false);
    assert.equal(controls.saveButton.disabled, false);
    await controller.save();
    assert.equal(controller.isDirty(), false);
    assert.ok(calls.some(([command]) => command === "settings_character_appearance_get"));
    assert.ok(calls.some(([command]) => command === "settings_character_appearance_save"));
    assert.equal(nextFrame, null);
  } finally {
    globalThis.window = previousWindow;
  }
});

test("legacy controls preview, save, retain dirty state on failure, and cancel", async () => {
  class Control {
    constructor() {
      this.value = "";
      this.listeners = {};
      this.output = { textContent: "" };
      this.parentElement = { querySelector: () => this.output };
      this.style = { setProperty() {} };
    }

    addEventListener(type, listener) {
      this.listeners[type] = listener;
    }

    fire(type) {
      this.listeners[type]?.();
    }
  }

  const controls = Object.fromEntries([
    "portraitScale",
    "controlPanelWidth",
    "bubbleHeight",
    "controlPanelOffset",
    "inputBarOffset",
    "speechFontSize",
    "nameFontSize",
    "inputFontSize",
    "themeColors",
    "resetThemeButton",
  ].map((id) => [id, new Control()]));
  const themes = Object.fromEntries(Object.keys(toLegacyTheme(themeTokens)).map((id) => [id, new Control()]));
  const document = {
    getElementById: (id) => controls[id],
    querySelector: (selector) => themes[selector.match(/data-theme-field="([^"]+)"/)?.[1]],
    querySelectorAll: () => [],
  };
  const calls = [];
  let failSave = false;
  const invoke = async (command, args) => {
    calls.push([command, args]);
    if (command === "settings_character_appearance_save") {
      if (failSave) throw new Error("save failed");
      return { coreGenerationId: "generation-a", characterId: "Sakura", values: args.values };
    }
    return {};
  };
  const snapshot = {
    schemaVersion: 1,
    windowGeneration: 4,
    limits,
    presentation: {
      generationId: "generation-a",
      characterId: "Sakura",
      displayName: "夜乃桜",
      themeTokens,
      portraitKeys: ["__default__"],
      portraitResourceUrls: { __default__: "sakura-character://default" },
    },
    appearance: { schemaVersion: 2, coreGenerationId: "generation-a", characterId: "Sakura", values },
  };
  const previousWindow = globalThis.window;
  let nextFrame = null;
  globalThis.window = {
    setInterval: () => 1,
    clearInterval() {},
    requestAnimationFrame(callback) {
      nextFrame = callback;
      return 2;
    },
    cancelAnimationFrame() {
      nextFrame = null;
    },
  };
  try {
    const controller = createRuntimeAppearanceController({
      document,
      invoke,
      onDirty() {},
      onError(error) { throw new Error(error); },
      prepare() {},
      fillTheme(theme) {
        for (const [id, value] of Object.entries(theme)) themes[id].value = value;
      },
    });
    await controller.initialize(snapshot);
    controls.portraitScale.value = "130";
    controls.portraitScale.fire("input");
    controls.portraitScale.value = "135";
    controls.portraitScale.fire("input");
    nextFrame?.();
    nextFrame = null;
    await Promise.resolve();
    failSave = true;
    await assert.rejects(controller.save(), /save failed/);
    assert.equal(controller.isDirty(), true);
    failSave = false;
    await controller.save();
    assert.equal(controller.isDirty(), false);
    themes.accent_color.value = "#abcdef";
    controls.themeColors.fire("input");
    await controller.cancelPreview();
    assert.equal(controller.isDirty(), false);
    assert.equal(calls.filter(([command]) => command === "settings_character_appearance_preview").length, 1);
    assert.ok(calls.some(([command, args]) => command === "settings_character_appearance_preview" && args.values.portraitScalePercent === 135));
    assert.ok(calls.some(([command, args]) => command === "settings_character_appearance_save" && args.values.themeTokens.accent === themeTokens.accent));
    assert.ok(calls.some(([command]) => command === "settings_character_appearance_cancel_preview"));
  } finally {
    globalThis.window = previousWindow;
  }
});

test("portrait scale preview stays inside one explicit pointer gesture", async () => {
  class Control {
    constructor() {
      this.value = "";
      this.listeners = {};
      this.output = { textContent: "" };
      this.parentElement = { querySelector: () => this.output };
      this.style = { setProperty() {} };
    }

    addEventListener(type, listener) { this.listeners[type] = listener; }
    fire(type, event = {}) { return this.listeners[type]?.(event); }
  }

  const controls = Object.fromEntries([
    "portraitScale", "controlPanelWidth", "bubbleHeight", "controlPanelOffset",
    "inputBarOffset", "speechFontSize", "nameFontSize", "inputFontSize",
    "themeColors", "resetThemeButton",
  ].map((id) => [id, new Control()]));
  const themes = Object.fromEntries(Object.keys(toLegacyTheme(themeTokens)).map((id) => [id, new Control()]));
  const document = {
    getElementById: (id) => controls[id],
    querySelector: (selector) => themes[selector.match(/data-theme-field="([^"]+)"/)?.[1]],
    querySelectorAll: () => [],
  };
  const snapshot = {
    schemaVersion: 1,
    windowGeneration: 4,
    limits,
    presentation: {
      generationId: "generation-a",
      characterId: "Sakura",
      displayName: "夜乃桜",
      themeTokens,
      portraitKeys: ["__default__"],
      portraitResourceUrls: { __default__: "sakura-character://default" },
    },
    appearance: { schemaVersion: 2, coreGenerationId: "generation-a", characterId: "Sakura", values },
  };
  const calls = [];
  let nextFrame = null;
  const previousWindow = globalThis.window;
  globalThis.window = {
    setInterval: () => 1,
    clearInterval() {},
    requestAnimationFrame(callback) { nextFrame = callback; return 2; },
    cancelAnimationFrame() { nextFrame = null; },
  };
  try {
    const controller = createRuntimeAppearanceController({
      document,
      invoke: async (command, args) => { calls.push([command, args]); return {}; },
      onDirty() {},
      onError(error) { throw new Error(error); },
      prepare() {},
      fillTheme(theme) {
        for (const [id, value] of Object.entries(theme)) themes[id].value = value;
      },
    });
    await controller.initialize(snapshot);
    controls.portraitScale.fire("pointerdown");
    controls.portraitScale.value = "51";
    controls.portraitScale.fire("input");
    nextFrame?.();
    nextFrame = null;
    await controls.portraitScale.fire("pointerup");

    assert.deepEqual(
      calls.filter(([command]) => command.startsWith("settings_character_appearance_"))
        .map(([command, args]) => [command, args?.active]),
      [
        ["settings_character_appearance_scale_gesture", true],
        ["settings_character_appearance_preview", undefined],
        ["settings_character_appearance_scale_gesture", false],
      ],
    );
    controller.dispose();
  } finally {
    globalThis.window = previousWindow;
  }
});
