import assert from "node:assert/strict";
import test from "node:test";

import {
  applyRuntimeThemeTokens,
  applyThemeTokens,
  normalizeColorText,
  toLegacyThemeTokens,
} from "../core/theme-runtime.js";

test("settings and character studio share the same theme token mapping", () => {
  const applied = new Map();
  const root = {
    style: {
      setProperty(name, value) { applied.set(name, value); },
    },
  };

  applyThemeTokens({
    primary_color: "#123456",
    page_background_color: "#abcdef",
    border_color: "invalid",
  }, root);

  assert.equal(applied.get("--sakura-primary"), "#123456");
  assert.equal(applied.get("--sakura-page-bg"), "#abcdef");
  assert.equal(applied.has("--sakura-border"), false);
});

test("shared theme color normalization accepts six-digit hex colors", () => {
  assert.equal(normalizeColorText("A1B2C3", "#000000"), "#a1b2c3");
  assert.equal(normalizeColorText("invalid", "#123456"), "#123456");
});

test("character studio shell applies the active Runtime theme token names", () => {
  const applied = new Map();
  const root = {
    style: {
      setProperty(name, value) { applied.set(name, value); },
    },
  };
  const runtimeTheme = {
    primary: "#28483a",
    pageBackground: "#101713",
    panelBackground: "#18221d",
  };

  assert.deepEqual(toLegacyThemeTokens(runtimeTheme), {
    primary_color: "#28483a",
    primary_hover_color: undefined,
    accent_color: undefined,
    text_color: undefined,
    secondary_text_color: undefined,
    muted_text_color: undefined,
    page_background_color: "#101713",
    panel_background_color: "#18221d",
    input_background_color: undefined,
    bubble_background_color: undefined,
    border_color: undefined,
  });

  applyRuntimeThemeTokens(runtimeTheme, root);

  assert.equal(applied.get("--sakura-primary"), "#28483a");
  assert.equal(applied.get("--sakura-page-bg"), "#101713");
  assert.equal(applied.get("--sakura-panel-bg"), "#18221d");
});
