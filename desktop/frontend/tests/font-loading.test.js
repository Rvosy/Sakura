import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  DEFAULT_FONT_LOAD_TIMEOUT_MS,
  waitForRuntimeFonts,
} from "../core/font-loader.js";

const fontCss = readFileSync(new URL("../assets/fonts/fonts.css", import.meta.url), "utf8");
const fontReadme = readFileSync(new URL("../assets/fonts/README.md", import.meta.url), "utf8");
const fontLicense = readFileSync(new URL("../assets/fonts/OFL.txt", import.meta.url), "utf8");
const petStyles = readFileSync(new URL("../styles.css", import.meta.url), "utf8");
const settingsStyles = readFileSync(new URL("../settings/styles.css", import.meta.url), "utf8");
const petApp = readFileSync(new URL("../app.js", import.meta.url), "utf8");
const settingsApp = readFileSync(new URL("../settings/settings.js", import.meta.url), "utf8");
const tauriConfig = JSON.parse(readFileSync(
  new URL("../../src-tauri/tauri.conf.json", import.meta.url),
  "utf8",
));

const assets = [
  "NotoSansSC-VariableFont_wght-400-700.woff2",
  "NotoSansJP-VariableFont_wght-400-700.woff2",
];

function documentWith(fonts) {
  return { documentElement: { dataset: {} }, fonts };
}

test("bundled fonts are WOFF2 variable faces with provenance and an OFL license", () => {
  for (const asset of assets) {
    const bytes = readFileSync(new URL(`../assets/fonts/${asset}`, import.meta.url));
    const hash = createHash("sha256").update(bytes).digest("hex").toUpperCase();
    assert.equal(bytes.subarray(0, 4).toString("ascii"), "wOF2");
    assert.match(fontReadme, new RegExp(hash));
    assert.match(fontCss, new RegExp(asset.replaceAll(".", "\\.")));
  }
  assert.equal((fontCss.match(/font-weight:\s*400 700/g) || []).length, 2);
  assert.match(fontCss, /font-family:\s*"Sakura Noto Sans SC"/);
  assert.match(fontCss, /font-family:\s*"Sakura Noto Sans JP"/);
  assert.match(fontCss, /--font-weight-regular:\s*400/);
  assert.match(fontCss, /--font-weight-medium:\s*500/);
  assert.match(fontCss, /--font-weight-semibold:\s*600/);
  assert.match(fontCss, /--font-weight-bold:\s*700/);
  assert.match(fontLicense, /SIL OPEN FONT LICENSE Version 1\.1/);
  assert.match(fontReadme, /no Unicode or glyph subsetting was performed/);
});

test("runtime typography maps SC and JP by language and keeps system fallbacks", () => {
  assert.match(petStyles, /@import url\("\.\/assets\/fonts\/fonts\.css"\)/);
  assert.match(petStyles, /--font-ui:\s*"Sakura Noto Sans SC",\s*var\(--font-ui-system\)/);
  assert.match(petStyles, /--font-zh:\s*"Sakura Noto Sans SC",\s*var\(--font-zh-system\)/);
  assert.match(petStyles, /--font-ja:\s*"Sakura Noto Sans JP",\s*var\(--font-ja-system\)/);
  assert.match(petStyles, /--font-ko:\s*var\(--font-ko-system\)/);
  assert.match(petStyles, /--font-latin:\s*"Sakura Noto Sans SC",\s*var\(--font-latin-system\)/);
  assert.match(petStyles, /:root\[data-runtime-fonts="fallback"\]/);
  assert.doesNotMatch(petStyles, /font-weight:\s*(?:800|900)\b/);

  assert.match(settingsStyles, /@import url\("\.\.\/assets\/fonts\/fonts\.css"\)/);
  assert.match(settingsStyles, /font-family:\s*"Sakura Noto Sans SC",\s*var\(--settings-font-system\)/);
  assert.match(settingsStyles, /:root\[data-runtime-fonts="fallback"\]/);
  assert.doesNotMatch(settingsStyles, /font-weight:\s*(?:800|900)\b/);
});

test("CSP permits only self-hosted fonts", () => {
  const fontDirective = tauriConfig.app.security.csp
    .split(";")
    .map((directive) => directive.trim())
    .find((directive) => directive.startsWith("font-src"));
  assert.equal(fontDirective, "font-src 'self'");
});

test("pet and settings wait for fonts before revealing their windows", () => {
  const petWait = petApp.lastIndexOf("await waitForRuntimeFonts()");
  const petReady = petApp.lastIndexOf("document.body.dataset.shellState =");
  const petReveal = petApp.lastIndexOf('await invoke("reveal_pet_window")');
  assert.ok(petWait >= 0 && petWait < petReady && petReady < petReveal);

  const settingsWait = settingsApp.lastIndexOf("await runtimeFontsReadyPromise");
  const settingsReveal = settingsApp.lastIndexOf('await invoke("reveal_settings_window")');
  assert.ok(settingsWait >= 0 && settingsWait < settingsReveal);
  assert.match(settingsApp, /waitForRuntimeFonts\(\{ families: \["sc"\] \}\)/);
  assert.equal(DEFAULT_FONT_LOAD_TIMEOUT_MS, 2_000);
});

test("font loader reports loaded after all requested faces are ready", async () => {
  const calls = [];
  const documentRef = documentWith({
    load: async (descriptor, sample) => {
      calls.push([descriptor, sample]);
      return [{}];
    },
    ready: Promise.resolve(),
  });

  assert.equal(await waitForRuntimeFonts({ documentRef }), "loaded");
  assert.equal(calls.length, 2);
  assert.match(calls[0][0], /Sakura Noto Sans SC/);
  assert.match(calls[1][0], /Sakura Noto Sans JP/);
  assert.equal(documentRef.documentElement.dataset.runtimeFonts, "loaded");
});

test("font loader marks unsupported and failed FontFaceSet implementations as fallback", async () => {
  const unsupported = documentWith(undefined);
  assert.equal(await waitForRuntimeFonts({ documentRef: unsupported }), "unsupported");
  assert.equal(unsupported.documentElement.dataset.runtimeFonts, "fallback");

  const failed = documentWith({
    load: async () => { throw new Error("font unavailable"); },
    ready: Promise.resolve(),
  });
  assert.equal(await waitForRuntimeFonts({ documentRef: failed }), "fallback");
  assert.equal(failed.documentElement.dataset.runtimeFonts, "fallback");
});

test("font timeout locks the page to system fallbacks for the current window", async () => {
  let finishLoad;
  const documentRef = documentWith({
    load: () => new Promise((resolve) => { finishLoad = resolve; }),
    ready: Promise.resolve(),
  });

  const status = await waitForRuntimeFonts({
    documentRef,
    setTimer: (callback) => {
      queueMicrotask(callback);
      return 1;
    },
    clearTimer: () => {},
  });
  assert.equal(status, "fallback");
  assert.equal(documentRef.documentElement.dataset.runtimeFonts, "fallback");

  finishLoad([{}]);
  await Promise.resolve();
  assert.equal(documentRef.documentElement.dataset.runtimeFonts, "fallback");
});
