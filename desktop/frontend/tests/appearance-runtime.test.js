import assert from "node:assert/strict";
import test from "node:test";

import {
  createRuntimeAppearanceController,
  toLegacyTheme,
  validateAppearanceSnapshot,
  validateAppearanceValues,
} from "../settings/appearance-runtime.js";
import { validateAppearancePublication as validatePetAppearancePublication } from "../pet/appearance.js";

const limits = Object.freeze({
  portraitScalePercent: [50, 150, 100],
  controlPanelWidth: [420, 860, 640],
  bubbleMaxHeight: [96, 260, 128],
  controlPanelVerticalOffset: [-200, 200, 0],
  inputBarOffset: [0, 200, 0],
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

test("pet appearance accepts the complete 0.9.10 layout adjustment range", () => {
  const presentation = { generationId: "generation-a", characterId: "Sakura" };
  const publication = {
    schemaVersion: 1,
    coreGenerationId: presentation.generationId,
    characterId: presentation.characterId,
    values: {
      ...values,
      controlPanelWidth: 860,
      controlPanelVerticalOffset: -200,
      inputBarOffset: 200,
    },
  };

  assert.equal(validatePetAppearancePublication(publication, presentation).controlPanelWidth, 860);
  assert.throws(() => validatePetAppearancePublication({
    ...publication,
    values: { ...publication.values, controlPanelWidth: 861 },
  }, presentation));
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

test("overlapping rapid layout drags publish only the newest fixed bubble height without connection errors", async () => {
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
