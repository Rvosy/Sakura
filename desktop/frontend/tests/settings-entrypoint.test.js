import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const settingsHtml = readFileSync(
  new URL("../settings/index.html", import.meta.url),
  "utf8",
);

test("settings entrypoint loads the ES module as a module script", () => {
  const scriptTags = settingsHtml.match(/<script\b[^>]*>/gi) || [];
  const entrypoint = scriptTags.find((tag) => /\bsrc=["']\.\/settings\.js["']/.test(tag));

  assert.ok(entrypoint, "settings.js entrypoint is missing");
  assert.match(entrypoint, /\btype=["']module["']/i);
});
