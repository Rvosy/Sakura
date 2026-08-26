import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { extname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const frontendRoot = fileURLToPath(new URL("..", import.meta.url));
const checkedExtensions = new Set([".css", ".html", ".svg"]);
const gradientPattern = /(?:repeating-)?(?:linear|radial|conic)-gradient\s*\(|<(?:linear|radial)Gradient\b/i;

function collectStyleSources(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return collectStyleSources(path);
    return checkedExtensions.has(extname(entry.name)) ? [path] : [];
  });
}

test("first-party frontend does not define CSS or SVG gradients", () => {
  const violations = collectStyleSources(frontendRoot).filter((path) => (
    gradientPattern.test(readFileSync(path, "utf8"))
  ));

  assert.deepEqual(violations, []);
});

test("first-party frontend does not use theme colors for blurred glows", () => {
  const cssFiles = collectStyleSources(frontendRoot).filter((path) => extname(path) === ".css");
  const shadowDeclarationPattern = /(box-shadow|filter)\s*:\s*([^;]+)/gi;
  const themeColorPattern = /--primary|--sakura-primary|--sakura-accent|255\s+127\s+181/i;
  const solidRingPattern = /^0(?:px)?\s+0(?:px)?\s+0(?:px)?(?:\s|$)/i;
  const shadowTokenPattern = /--shadow-(?:card|pop)\s*:\s*([^;]+)/gi;
  const violations = cssFiles.filter((path) => {
    const source = readFileSync(path, "utf8");
    const hasThemeGlow = Array.from(source.matchAll(shadowDeclarationPattern)).some((match) => {
      const [, property, value] = match;
      if (!themeColorPattern.test(value)) return false;
      return property === "filter" || !solidRingPattern.test(value.trim());
    });
    const hasColoredShadowToken = Array.from(source.matchAll(shadowTokenPattern)).some((match) => (
      match[1].trim() !== "none"
    ));
    return hasThemeGlow || hasColoredShadowToken;
  });

  assert.deepEqual(violations, []);
});
