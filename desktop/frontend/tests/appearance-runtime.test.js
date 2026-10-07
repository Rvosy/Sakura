import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import vm from "node:vm";

import {
  createRuntimeAppearanceController,
  toLegacyTheme,
  validateAppearanceSnapshot,
  validateAppearanceValues,
} from "../settings/appearance-runtime.js";
import { validateAppearancePublication as validatePetAppearancePublication } from "../pet/appearance.js";
import { FALLBACK_THEME_TOKENS } from "../core/theme.js";
import { applyThemeTokens, toLegacyThemeTokens } from "../core/theme-runtime.js";

const limits = Object.freeze({
  portraitScalePercent: [50, 150, 100],
  controlPanelWidth: [420, 860, 640],
  bubbleMaxHeight: [96, 400, 128],
  controlPanelVerticalOffset: [-400, 400, 0],
  inputBarOffset: [0, 400, 0],
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
  bubbleAutoExpand: false,
  controlPanelVerticalOffset: 0,
  inputBarOffset: 0,
  speechFontSize: 20,
  nameFontSize: 14,
  inputFontSize: 16,
  visualEffectMode: "gaussian_blur",
  themeTokens,
});

test("removing the last character clears real appearance state and continues global settings refresh", async () => {
  class Control {
    value = "";
    disabled = false;
    checked = false;
    dataset = {};
    listeners = {};
    output = { textContent: "" };
    parentElement = { querySelector: () => this.output };
    style = { setProperty() {} };
    addEventListener(type, listener) { this.listeners[type] = listener; }
    fire(type) { return this.listeners[type]?.({ type, currentTarget: this }); }
    setAttribute() {}
    removeAttribute() {}
    closest() { return null; }
    replaceChildren() { this.textContent = ""; }
  }
  const controls = Object.fromEntries([
    "portraitScale", "controlPanelWidth", "bubbleHeight", "bubbleAutoExpand", "controlPanelOffset",
    "inputBarOffset", "speechFontSize", "nameFontSize", "inputFontSize", "themeAiButton",
    "themeColors", "visualEffectMode", "resetThemeButton",
  ].map((id) => [id, new Control()]));
  const colors = Object.fromEntries(Object.keys(toLegacyTheme(themeTokens)).map((id) => [id, new Control()]));
  const css = new Map();
  const document = {
    getElementById: (id) => controls[id],
    querySelector: (selector) => colors[selector.match(/data-theme-field="([^"]+)"/)?.[1]],
    documentElement: { style: { setProperty: (key, value) => css.set(key, value) } },
  };
  const source = await readFile(new URL("../settings/settings.js", import.meta.url), "utf8");
  const functions = [
    ["function disableRuntimeControl(", "\nconst errorDialog ="],
    ["function prepareRuntimeCharacterOnly(", "\nfunction applyStorageSnapshot("],
    ["async function rebindSettingsAfterCharacterSwitch(", "\nfunction renderThemeControls("],
  ].map(([start, end]) => source.slice(source.indexOf(start), source.indexOf(end))).join("\n");
  const refreshed = [];
  const fillTheme = (theme) => {
    for (const [key, value] of Object.entries(theme)) colors[key].value = value;
    applyThemeTokens(theme, document.documentElement);
  };
  const context = vm.createContext({
    document, fields: controls, request: null, themeEditor: {}, activeThemeField: "", themeChanged: false,
    RUNTIME_UNAVAILABLE_REASON: "此设置暂不可用", RUNTIME_LAYOUT_DEFAULTS: {},
    runtimeVisualEffectModes: [], runtimeCharacterFeature: { applyAppearancePresentation() {}, prepareControls() {} },
    renderThemeControls() { controls.themeColors.textContent = "character colors"; },
    setThemeValues: fillTheme, enhanceSelect() {}, refreshSelect() {}, upgradeSliderControls() {},
    applyThemeTokens, toLegacyThemeTokens, FALLBACK_THEME_TOKENS,
    runtimeProviderFeature: { rebindIdentity: (id) => refreshed.push(["provider", id]) },
    runtimeToolsController: { refreshCurrent: async () => refreshed.push("tools") },
    runtimePluginController: { refreshCurrent: async () => refreshed.push("plugins") },
    runtimeVoiceController: { refreshCurrent: async () => refreshed.push("voice") },
  });
  vm.runInContext(functions, context);
  const makeSnapshot = (generationId, characterId, scale = 125) => ({
    schemaVersion: 1, windowGeneration: 4, limits,
    presentation: { generationId, characterId, themeTokens },
    appearance: { schemaVersion: 1, coreGenerationId: generationId, characterId,
      values: { ...values, portraitScalePercent: scale } },
  });
  let current = makeSnapshot("generation-a", "alpha");
  let emptyReadiness = "setup_required";
  let pollLifecycle;
  let elapsed = 0;
  const calls = [], errors = [];
  const previousWindow = globalThis.window;
  const previousNow = Date.now;
  globalThis.window = {
    setInterval(callback) { pollLifecycle = callback; return 1; }, clearInterval() {},
    requestAnimationFrame: () => 2, cancelAnimationFrame() {},
  };
  Date.now = () => elapsed;
  const controller = createRuntimeAppearanceController({
    document,
    invoke: async (command) => {
      calls.push(command);
      if (command === "runtime_lifecycle_snapshot") return {
        supervisor: { generationId: current?.presentation.generationId || "generation-empty" },
        snapshot: { generationId: current?.presentation.generationId || "generation-empty", readiness: current ? "ready" : emptyReadiness },
        characterPresentation: current?.presentation || null,
      };
      if (command === "settings_character_appearance_get") {
        if (!current) throw new Error("CHARACTER_PRESENTATION_NOT_READY");
        return current;
      }
      return {};
    },
    prepare: context.prepareRuntimeAppearance, fillTheme,
    onDirty() {}, onError: (error) => errors.push(error),
    wait: async (milliseconds) => { elapsed += milliseconds; },
  });
  context.runtimeAppearanceController = controller;
  try {
    await controller.initialize(current);
    controls.portraitScale.value = "135";
    controls.portraitScale.fire("input");
    assert.equal(controller.isDirty(), true);
    current = null;
    await context.rebindSettingsAfterCharacterSwitch("generation-empty", undefined);
    assert.deepEqual(refreshed, [["provider", "generation-empty"], "tools", "plugins", "voice"]);
    assert.equal(calls.includes("settings_character_appearance_get"), false);
    assert.equal(elapsed, 0);
    assert.equal(controller.isDirty(), false);
    assert.equal(controls.portraitScale.value, String(limits.portraitScalePercent[2]));
    assert.equal(controls.portraitScale.disabled, true);
    assert.equal(controls.themeColors.textContent, "");
    assert.equal(css.get("--sakura-primary"), FALLBACK_THEME_TOKENS.primary);

    current = makeSnapshot("generation-b", "beta", 75);
    await pollLifecycle();
    assert.equal(controls.portraitScale.value, "75");
    assert.equal(controls.portraitScale.disabled, false);
    assert.equal(controller.isDirty(), false);
    current = null;
    calls.length = 0;
    emptyReadiness = "initializing";
    await pollLifecycle();
    assert.equal(controls.portraitScale.value, "75");
    assert.equal(controls.portraitScale.disabled, false, "a transient missing presentation keeps the current appearance");
    emptyReadiness = "setup_required";
    await pollLifecycle();
    await pollLifecycle();
    assert.equal(controls.portraitScale.disabled, true);
    assert.equal(calls.includes("settings_character_appearance_get"), false);
    assert.deepEqual(errors, []);
  } finally {
    controller.dispose();
    globalThis.window = previousWindow;
    Date.now = previousNow;
  }
});

test("appearance values validate exact theme and bounded scalar fields", () => {
  assert.deepEqual(validateAppearanceValues(values, limits), values);
  assert.equal(
    validateAppearanceValues({ ...values, visualEffectMode: "liquid_glass" }, limits).visualEffectMode,
    "liquid_glass",
  );
  assert.throws(() => validateAppearanceValues({ ...values, portraitScalePercent: 151 }, limits));
  assert.throws(() => validateAppearanceValues({ ...values, visualEffectMode: "acrylic" }, limits));
  assert.throws(() => validateAppearanceValues({ ...values, bubbleAutoExpand: "yes" }, limits));
  assert.throws(() => validateAppearanceValues({ ...values, themeTokens: { ...themeTokens, token: "secret" } }, limits));
  assert.throws(() => validateAppearanceValues({ ...values, themeTokens: { ...themeTokens, accent: "url(file)" } }, limits));
});

test("expanded bubble bounds survive settings and pet publication validation", () => {
  const presentation = { generationId: "generation-a", characterId: "Sakura" };
  const validate = (draft) => validatePetAppearancePublication({
    schemaVersion: 1,
    coreGenerationId: presentation.generationId,
    characterId: presentation.characterId,
    values: validateAppearanceValues(draft, limits),
  }, presentation);
  for (const offset of [-400, 400]) {
    const draft = { ...values, bubbleMaxHeight: 400, inputBarOffset: 400, controlPanelVerticalOffset: offset };
    assert.deepEqual(validate(draft), draft);
  }
  for (const [field, value] of [["bubbleMaxHeight", 401], ["inputBarOffset", 401], ["controlPanelVerticalOffset", -401], ["controlPanelVerticalOffset", 401]]) {
    assert.throws(() => validate({ ...values, [field]: value }));
  }
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
      schemaVersion: 1,
      coreGenerationId: "generation-a",
      characterId: "Sakura",
      values,
    },
  };
  assert.equal(validateAppearanceSnapshot(snapshot).appearance.values.portraitScalePercent, 125);
  assert.throws(() => validateAppearanceSnapshot({ ...snapshot, appearance: { ...snapshot.appearance, coreGenerationId: "old" } }));
});

test("Studio publication merges only edited appearance fields and saves against the new generation", async () => {
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
    "portraitScale", "controlPanelWidth", "bubbleHeight", "bubbleAutoExpand", "controlPanelOffset",
    "inputBarOffset", "speechFontSize", "nameFontSize", "inputFontSize",
    "themeColors", "visualEffectMode", "resetThemeButton", "applyButton", "saveButton",
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
    appearance: { schemaVersion: 1, coreGenerationId: generationId, characterId: "Sakura", values },
  });
  let nextSnapshot = makeSnapshot("generation-b");
  nextSnapshot.appearance.values = {
    ...values, controlPanelWidth: 700,
    themeTokens: { ...themeTokens, primary: "#abcdef", accent: "#fedcba" },
  };
  let intervalCallback = null;
  let startupPresentationReady = false;
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
          return { supervisor: { generationId: nextSnapshot.presentation.generationId },
            characterPresentation: startupPresentationReady ? nextSnapshot.presentation : null };
        }
        if (command === "settings_character_appearance_get") return nextSnapshot;
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
    await controller.initialize();
    await intervalCallback();
    assert.equal(calls.some(([command]) => command === "settings_character_appearance_get"), false);
    const afterRestart = nextSnapshot;
    nextSnapshot = makeSnapshot("generation-a");
    startupPresentationReady = true;
    await intervalCallback();
    assert.equal(controls.portraitScale.value, "125", "startup completion initializes sliders without reopening settings");
    nextSnapshot = afterRestart;
    controls.portraitScale.value = "135";
    controls.portraitScale.fire("input");
    themes.accent_color.value = "#123456";
    controls.themeColors.fire("input");
    assert.equal(controller.isDirty(), true);
    await intervalCallback();
    assert.equal(controller.isDirty(), true);
    assert.equal(controls.portraitScale.value, "135");
    assert.equal(controls.controlPanelWidth.value, "700");
    assert.equal(themes.primary_color.value, "#abcdef");
    assert.equal(themes.accent_color.value, "#123456");
    assert.equal(controls.applyButton.disabled, false);
    assert.equal(controls.saveButton.disabled, false);
    await controller.save();
    assert.equal(controller.isDirty(), false);
    assert.ok(calls.some(([command]) => command === "settings_character_appearance_get"));
    assert.ok(calls.some(([command]) => command === "settings_character_appearance_save"));
    assert.equal(nextFrame, null);

    nextSnapshot = makeSnapshot("generation-c");
    nextSnapshot.appearance.values = { ...values, themeTokens: { ...themeTokens, primary: "#998877" } };
    await intervalCallback();
    assert.equal(themes.primary_color.value, "#998877");
    assert.equal(controller.isDirty(), false, "a clean Settings page accepts the published appearance without creating a draft");

    controls.portraitScale.value = "140";
    controls.portraitScale.fire("input");
    nextSnapshot = makeSnapshot("generation-d");
    nextSnapshot.presentation.characterId = "Other";
    nextSnapshot.appearance.characterId = "Other";
    await intervalCallback();
    assert.equal(controls.portraitScale.value, "125");
    assert.equal(controller.isDirty(), false, "edits cannot cross to another character");
    controller.dispose();
  } finally {
    globalThis.window = previousWindow;
  }
});

for (const trigger of ["switch-completion", "lifecycle-poll"]) {
  test(`same-Core character switch refreshes colors and keeps released scale (${trigger})`, async () => {
    class Control {
      value = "";
      listeners = {};
      parentElement = { querySelector: () => ({ textContent: "" }) };
      style = { setProperty() {} };
      addEventListener(type, listener) { this.listeners[type] = listener; }
      fire(type) { return this.listeners[type]?.({ type, currentTarget: this }); }
    }
    const controls = Object.fromEntries([
      "portraitScale", "controlPanelWidth", "bubbleHeight", "bubbleAutoExpand", "controlPanelOffset",
      "inputBarOffset", "speechFontSize", "nameFontSize", "inputFontSize",
      "themeColors", "visualEffectMode", "resetThemeButton",
    ].map((id) => [id, new Control()]));
    const themes = Object.fromEntries(Object.keys(toLegacyTheme(themeTokens)).map((id) => [id, new Control()]));
    const document = {
      getElementById: (id) => controls[id],
      querySelector: (selector) => themes[selector.match(/data-theme-field="([^"]+)"/)?.[1]],
    };
    const snapshots = Object.fromEntries(["Sakura", "Other"].map((characterId) => [characterId, {
      schemaVersion: 1, windowGeneration: 4, limits,
      presentation: { generationId: "generation-a", characterId, themeTokens },
      appearance: { schemaVersion: 1, coreGenerationId: "generation-a", characterId,
        values: { ...values, portraitScalePercent: 150,
          themeTokens: { ...themeTokens, primary: characterId === "Other" ? "#abcdef" : themeTokens.primary } } },
    }]));
    let currentCharacter = "Sakura", sessionCharacter = "Sakura";
    let intervalCallback, nextFrame, preview, saved;
    let visibleScale = 150;
    const errors = [];
    const previousWindow = globalThis.window;
    globalThis.window = {
      setInterval(callback) { intervalCallback = callback; return 1; },
      clearInterval() {},
      requestAnimationFrame(callback) { nextFrame = callback; return 2; },
      cancelAnimationFrame() { nextFrame = null; },
    };
    const controller = createRuntimeAppearanceController({
      document,
      invoke: async (command, args) => {
        const snapshot = snapshots[currentCharacter];
        if (command === "runtime_lifecycle_snapshot") return {
          supervisor: { generationId: "generation-a" }, characterPresentation: snapshot.presentation,
        };
        if (command === "settings_character_appearance_get") {
          sessionCharacter = currentCharacter;
          return snapshot;
        }
        if (command === "settings_character_appearance_preview") {
          preview = { schemaVersion: 1, coreGenerationId: "generation-a", characterId: sessionCharacter, values: args.values };
        }
        if (command === "settings_character_appearance_scale_frame") visibleScale = args.portraitScalePercent;
        if (command === "settings_character_appearance_scale_gesture" && !args.active) {
          visibleScale = preview?.characterId === currentCharacter
            ? validatePetAppearancePublication(preview, snapshot.presentation).portraitScalePercent
            : snapshot.appearance.values.portraitScalePercent;
        }
        if (command === "settings_character_appearance_save") {
          if (sessionCharacter !== currentCharacter) throw new Error("APPEARANCE_SESSION_STALE");
          saved = { ...snapshot.appearance, values: args.values };
          return saved;
        }
        return {};
      },
      onDirty() {}, onError: (error) => errors.push(error), prepare() {},
      fillTheme(theme) { for (const [id, value] of Object.entries(theme)) themes[id].value = value; },
    });
    try {
      await controller.initialize(snapshots.Sakura);
      currentCharacter = "Other";
      if (trigger === "switch-completion") await controller.rebindIdentity("generation-a", "Other");
      else await intervalCallback();
      assert.equal(themes.primary_color.value, "#abcdef");
      assert.equal(controller.isDirty(), false);
      controls.portraitScale.fire("pointerdown");
      controls.portraitScale.value = "80";
      controls.portraitScale.fire("input");
      nextFrame?.();
      await controls.portraitScale.fire("pointerup");
      assert.equal(visibleScale, 80, "the released scale belongs to the active character");
      await controller.save();
      assert.equal(saved.characterId, "Other");
      assert.equal(saved.values.portraitScalePercent, 80);
      assert.equal(saved.values.themeTokens.primary, "#abcdef");
      assert.deepEqual(errors, []);
    } finally {
      controller.dispose();
      globalThis.window = previousWindow;
    }
  });
}

test("legacy controls preview, save, retain dirty state on failure, cancel, and reset theme", async () => {
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
    "bubbleAutoExpand",
    "controlPanelOffset",
    "inputBarOffset",
    "speechFontSize",
    "nameFontSize",
    "inputFontSize",
    "themeColors",
    "visualEffectMode",
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
    appearance: { schemaVersion: 1, coreGenerationId: "generation-a", characterId: "Sakura", values },
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
    controls.visualEffectMode.value = "solid";
    controls.visualEffectMode.fire("change");
    nextFrame?.();
    nextFrame = null;
    await Promise.resolve();
    assert.equal(controller.isDirty(), true);
    assert.ok(calls.some(([command, args]) => command === "settings_character_appearance_preview"
      && args.values.visualEffectMode === "solid"));
    await controller.cancelPreview();
    assert.equal(controls.visualEffectMode.value, "gaussian_blur");
    themes.accent_color.value = "#abcdef";
    controls.themeColors.fire("input");
    await controller.cancelPreview();
    assert.equal(controller.isDirty(), false);
    assert.equal(calls.filter(([command]) => command === "settings_character_appearance_preview").length, 2);
    assert.ok(calls.some(([command, args]) => command === "settings_character_appearance_preview" && args.values.portraitScalePercent === 135));
    assert.ok(calls.some(([command, args]) => command === "settings_character_appearance_save" && args.values.themeTokens.accent === themeTokens.accent));
    assert.ok(calls.some(([command]) => command === "settings_character_appearance_cancel_preview"));

    controls.portraitScale.value = "140";
    controls.portraitScale.fire("input");
    themes.accent_color.value = "#abcdef";
    controls.themeColors.fire("input");
    assert.doesNotThrow(() => controls.resetThemeButton.fire("click"));
    assert.doesNotThrow(() => controls.resetThemeButton.fire("click"));
    assert.equal(controls.portraitScale.value, "140", "theme reset retains other appearance edits");
    assert.equal(themes.accent_color.value, themeTokens.accent);
    assert.equal(controller.isDirty(), true);
    nextFrame?.();
    nextFrame = null;
    await Promise.resolve();
    const resetPreview = calls.findLast(([command]) => command === "settings_character_appearance_preview")[1];
    assert.deepEqual(resetPreview.values.themeTokens, themeTokens);
    assert.equal(resetPreview.values.portraitScalePercent, 140);
    await controller.save();
    const resetSave = calls.findLast(([command]) => command === "settings_character_appearance_save")[1];
    assert.deepEqual(resetSave.values, resetPreview.values);
    assert.equal(controller.isDirty(), false);
    assert.equal(snapshot.appearance.values.portraitScalePercent, 125);

    themes.accent_color.value = "#abcdef";
    controls.themeColors.fire("input");
    controls.resetThemeButton.fire("click");
    assert.equal(controller.isDirty(), false, "resetting a theme-only edit restores the committed values");
    controller.dispose();
  } finally {
    globalThis.window = previousWindow;
  }
});

test("overlapping rapid portrait drags share one backend gesture and window blur closes it", async () => {
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
    "portraitScale", "controlPanelWidth", "bubbleHeight", "bubbleAutoExpand", "controlPanelOffset",
    "inputBarOffset", "speechFontSize", "nameFontSize", "inputFontSize",
    "themeColors", "visualEffectMode", "resetThemeButton",
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
    appearance: { schemaVersion: 1, coreGenerationId: "generation-a", characterId: "Sakura", values },
  };
  const calls = [];
  const errors = [];
  const successfulPreviewScales = [];
  let previewAttempts = 0;
  let scaleFrameAttempts = 0;
  let nextFrame = null;
  const windowListeners = {};
  const previousWindow = globalThis.window;
  globalThis.window = {
    addEventListener(type, listener) { windowListeners[type] = listener; },
    setInterval: () => 1,
    clearInterval() {},
    requestAnimationFrame(callback) { nextFrame = callback; return 2; },
    cancelAnimationFrame() { nextFrame = null; },
  };
  try {
    const controller = createRuntimeAppearanceController({
      document,
      invoke: async (command, args) => {
        calls.push([command, args]);
        if (command === "settings_character_appearance_scale_frame" && scaleFrameAttempts++ === 0) {
          throw new Error("TRANSIENT_SCALE_FRAME_DROP");
        }
        if (command === "settings_character_appearance_preview" && previewAttempts++ === 0) {
          throw new Error("CHARACTER_PRESENTATION_NOT_READY");
        }
        if (command === "settings_character_appearance_preview") {
          successfulPreviewScales.push(args.values.portraitScalePercent);
        }
        return {};
      },
      onDirty() {},
      onError(error) { errors.push(error); },
      prepare() {},
      fillTheme(theme) {
        for (const [id, value] of Object.entries(theme)) themes[id].value = value;
      },
      wait: async () => {},
    });
    await controller.initialize(snapshot);
    controls.portraitScale.fire("pointerdown");
    controls.portraitScale.value = "51";
    controls.portraitScale.fire("input");
    nextFrame?.();
    nextFrame = null;
    const firstEnd = controls.portraitScale.fire("pointerup");
    controls.portraitScale.fire("pointerdown");
    controls.portraitScale.value = "52";
    controls.portraitScale.fire("input");
    nextFrame?.();
    nextFrame = null;
    const secondEnd = windowListeners.blur?.({ type: "blur" });
    await Promise.all([firstEnd, secondEnd]);

    assert.deepEqual(
      calls.filter(([command]) => command.startsWith("settings_character_appearance_"))
        .filter(([command]) => command === "settings_character_appearance_scale_gesture")
        .map(([command, args]) => [command, args.active]),
      [
        ["settings_character_appearance_scale_gesture", true],
        ["settings_character_appearance_scale_gesture", false],
      ],
    );
    const previewScales = calls
      .filter(([command]) => command === "settings_character_appearance_preview")
      .map(([, args]) => args.values.portraitScalePercent);
    assert.ok(previewScales.length >= 1);
    assert.ok(previewScales.every((scale) => scale === 52));
    assert.deepEqual(successfulPreviewScales, [52]);
    const scaleFrames = calls
      .filter(([command]) => command === "settings_character_appearance_scale_frame")
      .map(([, args]) => args.portraitScalePercent);
    assert.ok(scaleFrames.length >= 1);
    assert.equal(scaleFrames.at(-1), 52);
    assert.deepEqual(errors, []);
    controller.dispose();
  } finally {
    globalThis.window = previousWindow;
  }
});

test("overlapping layout drags ignore the old slider blur and publish the newest height", async () => {
  class Control {
    constructor() {
      this.value = "";
      this.listeners = {};
      this.output = { textContent: "" };
      this.parentElement = { querySelector: () => this.output };
      this.style = { setProperty() {} };
    }

    addEventListener(type, listener) { this.listeners[type] = listener; }
    fire(type, event = {}) {
      return this.listeners[type]?.({ currentTarget: this, ...event });
    }
  }

  const controls = Object.fromEntries([
    "portraitScale", "controlPanelWidth", "bubbleHeight", "bubbleAutoExpand", "controlPanelOffset",
    "inputBarOffset", "speechFontSize", "nameFontSize", "inputFontSize",
    "themeColors", "visualEffectMode", "resetThemeButton",
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
    appearance: { schemaVersion: 1, coreGenerationId: "generation-a", characterId: "Sakura", values },
  };
  const calls = [];
  const errors = [];
  const successfulPreviewHeights = [];
  let previewAttempts = 0;
  let layoutFrameAttempts = 0;
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
      invoke: async (command, args) => {
        calls.push([command, args]);
        if (command === "settings_character_appearance_layout_frame" && layoutFrameAttempts++ === 0) {
          throw new Error("TRANSIENT_LAYOUT_FRAME_DROP");
        }
        if (command === "settings_character_appearance_preview" && previewAttempts++ === 0) {
          throw new Error("CHARACTER_PRESENTATION_NOT_READY");
        }
        if (command === "settings_character_appearance_preview") {
          successfulPreviewHeights.push(args.values.bubbleMaxHeight);
        }
        return {};
      },
      onDirty() {},
      onError(error) { errors.push(error); },
      prepare() {},
      fillTheme(theme) {
        for (const [id, value] of Object.entries(theme)) themes[id].value = value;
      },
      wait: async () => {},
    });
    await controller.initialize(snapshot);
    controls.controlPanelWidth.fire("pointerdown");
    controls.controlPanelWidth.value = "650";
    controls.controlPanelWidth.fire("input");
    controls.controlPanelWidth.value = "660";
    controls.controlPanelWidth.fire("input");
    nextFrame?.();
    nextFrame = null;
    const firstEnd = controls.controlPanelWidth.fire("pointerup");
    controls.bubbleHeight.fire("pointerdown");
    // A browser focuses the new slider after pointerdown, so the old slider's blur arrives late.
    // It must not close the newly started layout gesture and route its frames through full preview.
    controls.controlPanelWidth.fire("blur");
    controls.bubbleHeight.value = "150";
    controls.bubbleHeight.fire("input");
    controls.bubbleHeight.value = "160";
    controls.bubbleHeight.fire("input");
    nextFrame?.();
    nextFrame = null;
    const secondEnd = controls.bubbleHeight.fire("pointerup");
    await Promise.all([firstEnd, secondEnd]);

    assert.deepEqual(
      calls.filter(([command]) => command === "settings_character_appearance_layout_gesture")
        .map(([command, args]) => [command, args.active]),
      [
        ["settings_character_appearance_layout_gesture", true],
        ["settings_character_appearance_layout_gesture", false],
      ],
    );
    const previewHeights = calls
      .filter(([command]) => command === "settings_character_appearance_preview")
      .map(([, args]) => args.values.bubbleMaxHeight);
    assert.ok(previewHeights.length >= 1);
    assert.ok(previewHeights.every((height) => height === 160));
    assert.deepEqual(successfulPreviewHeights, [160]);
    const layoutFrames = calls
      .filter(([command]) => command === "settings_character_appearance_layout_frame")
      .map(([, args]) => args.values);
    assert.ok(layoutFrames.length >= 1);
    assert.equal(layoutFrames.at(-1).controlPanelWidth, 660);
    assert.equal(layoutFrames.at(-1).bubbleMaxHeight, 160);
    assert.deepEqual(errors, []);
    controller.dispose();
  } finally {
    globalThis.window = previousWindow;
  }
});
