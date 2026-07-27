import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import { validateAppearanceSnapshot, validateAppearanceValues } from "../settings/appearance-runtime.js";

const limits = Object.freeze({
  portraitScalePercent: [50, 150, 100],
  speechFontSize: [10, 24, 19],
  nameFontSize: [10, 20, 13],
  inputFontSize: [12, 20, 15],
  buttonFontSize: [12, 20, 15],
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
  speechFontSize: 20,
  nameFontSize: 14,
  inputFontSize: 16,
  buttonFontSize: 16,
  themeTokens,
});

test("appearance values validate exact theme and bounded scalar fields", () => {
  assert.deepEqual(validateAppearanceValues(values, limits), values);
  assert.throws(() => validateAppearanceValues({ ...values, portraitScalePercent: 151 }, limits));
  assert.throws(() => validateAppearanceValues({ ...values, themeTokens: { ...themeTokens, token: "secret" } }, limits));
  assert.throws(() => validateAppearanceValues({ ...values, themeTokens: { ...themeTokens, accent: "url(file)" } }, limits));
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

test("runtime settings frontend owns no data path, character selection, or forged identity", () => {
  const source = readFileSync(new URL("../settings/appearance-runtime.js", import.meta.url), "utf8");
  const entry = readFileSync(new URL("../settings/settings.js", import.meta.url), "utf8");
  assert.doesNotMatch(source, /data[\\/]|current_character_id|generationId\s*:|characterId\s*:/);
  assert.match(entry, /settings_character_appearance_get/);
  assert.match(source, /settings_character_appearance_preview/);
  assert.match(source, /settings_character_appearance_save/);
  assert.match(source, /settings_character_appearance_cancel_preview/);
});
