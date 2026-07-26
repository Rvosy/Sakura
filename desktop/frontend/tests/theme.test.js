import assert from "node:assert/strict";
import test from "node:test";

import { THEMES, themeToCssVariables } from "../core/theme.js";

test("built-in themes project only the approved CSS variables", () => {
  const projected = themeToCssVariables(THEMES.moon);
  assert.deepEqual(Object.keys(projected).sort(), ["--ink", "--line", "--muted", "--paper", "--sakura", "--sakura-deep"]);
  assert.equal(projected["--paper"], "#2b202b");
});

test("invalid theme values fall back and cannot inject CSS", () => {
  const projected = themeToCssVariables({ ink: "red; background:url(secret)", paper: "#12345" });
  assert.equal(projected["--ink"], THEMES.blossom.ink);
  assert.equal(projected["--paper"], THEMES.blossom.paper);
  assert.equal(JSON.stringify(projected).includes("secret"), false);
});
