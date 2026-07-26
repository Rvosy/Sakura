import assert from "node:assert/strict";
import test from "node:test";

import { FALLBACK_THEME_TOKENS, normalizeThemeTokens, themeToCssVariables } from "../core/theme.js";

test("character theme tokens project only the approved CSS variables", () => {
  const projected = themeToCssVariables({ ...FALLBACK_THEME_TOKENS, primary: "#12ABef" });
  assert.deepEqual(Object.keys(projected).sort(), ["--accent", "--border", "--bubble-background", "--input-background", "--muted-text", "--page-background", "--panel-background", "--primary", "--primary-hover", "--secondary-text", "--text"]);
  assert.equal(projected["--primary"], "#12abef");
});

test("invalid theme values fall back and cannot inject CSS", () => {
  const projected = themeToCssVariables({ primary: "red; background:url(secret)", text: "#12345" });
  assert.equal(projected["--primary"], FALLBACK_THEME_TOKENS.primary);
  assert.equal(projected["--text"], FALLBACK_THEME_TOKENS.text);
  assert.equal(JSON.stringify(projected).includes("secret"), false);
});

test("normalization fills every missing character token", () => {
  assert.deepEqual(normalizeThemeTokens({}), FALLBACK_THEME_TOKENS);
});
