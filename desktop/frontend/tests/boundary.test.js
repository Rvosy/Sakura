import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const index = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const app = readFileSync(new URL("../app.js", import.meta.url), "utf8");
const fakeCore = readFileSync(new URL("../chat/fake-chat-core.js", import.meta.url), "utf8");
const styles = readFileSync(new URL("../styles.css", import.meta.url), "utf8");
const nativeInteraction = readFileSync(new URL("../../src-tauri/src/window_interaction.rs", import.meta.url), "utf8");
const tauriConfig = JSON.parse(readFileSync(new URL("../../src-tauri/tauri.conf.json", import.meta.url), "utf8"));

function declarationBlock(selector, requiredDeclaration = null) {
  const blocks = [...styles.matchAll(new RegExp(`\\.${selector}\\s*\\{([^}]*)\\}`, "g"))].map((match) => match[1]);
  return requiredDeclaration ? blocks.find((block) => block.includes(requiredDeclaration)) || "" : blocks.at(-1) || "";
}

test("markup exposes fixed product chat, portrait, status, and accessible controls", () => {
  for (const id of ["chat-bubble", "bubble-copy", "composer-input", "composer-send", "typewriter-skip", "portrait-current", "close-window"])
    assert.match(index, new RegExp(`id="${id}"`), id);
  assert.match(index, /aria-live="polite"/);
  assert.match(index, /maxlength="4096"/);
  assert.match(index, /id="bubble-copy"[^>]*data-interactive="true"/);
  assert.match(index, /id="acceptance-entry"[^>]*hidden[^>]*aria-hidden="true"/);
  for (const forbidden of ["state-rail", "FAKE CORE", "geometry-readout", "theme-button", "composer-toggle", "visibility-probe"])
    assert.equal(index.includes(forbidden), false, forbidden);
});

test("rounded WebView surfaces preserve the native clip contract without external effects", () => {
  assert.doesNotMatch(styles, /filter\s*:\s*drop-shadow/i);
  assert.doesNotMatch(styles, /\.portrait-frame::after/);
  for (const [selector, radius] of [["bubble", 26], ["composer", 18]]) {
    const block = declarationBlock(selector, "border-radius");
    assert.match(block, new RegExp(`border-radius:\\s*${radius}px`), selector);
  }
  for (const selector of ["pet-stage", "portrait", "portrait-frame", "portrait-image"]) {
    const block = declarationBlock(selector, "background");
    assert.match(block, /background:\s*transparent/, selector);
  }
  assert.match(nativeInteraction, /const BUBBLE_CORNER_RADIUS: u32 = 26;/);
  assert.match(nativeInteraction, /const INPUT_CORNER_RADIUS: u32 = 18;/);
  assert.match(nativeInteraction, /const CONTROLS_CORNER_RADIUS: u32 = 15;/);
  assert.match(nativeInteraction, /const NATIVE_ANTIALIAS_BLEED_LOGICAL_PX: f64 = 2\.0;/);
  assert.match(nativeInteraction, /portrait_rect,[\s\S]*?0,[\s\S]*?\)\?/);
});

test("empty portrait layers stay hidden instead of painting WebView2 broken-image frames", () => {
  assert.match(styles, /\.portrait-image:not\(\[src\]\)\s*\{[^}]*visibility:\s*hidden/);
  const imageBlock = declarationBlock("portrait-image");
  assert.match(imageBlock, /display:\s*block/);
  assert.match(imageBlock, /border:\s*0/);
});

test("bubble typography uses language-aware sans-serif stacks instead of mixed CJK serif fallback", () => {
  const bubbleCopyBlock = styles.match(/\.bubble-copy\s*\{([^}]*)\}/)?.[1] || "";
  assert.doesNotMatch(bubbleCopyBlock, /Yu Mincho|SimSun|(^|[^-])\bserif\b/);
  assert.match(styles, /\.bubble-copy \[lang\|="zh"\]/);
  assert.match(styles, /\.bubble-copy \[lang\|="ja"\]/);
  assert.match(styles, /Microsoft YaHei UI/);
  assert.match(styles, /Yu Gothic UI/);
  assert.match(app, /renderMultilingualText/);
});

test("WP-3-03 presentation never invokes the real chat Gateway or reads network and product data", () => {
  for (const forbidden of ["chat_send", "chat_cancel", "fetch(", "localStorage", "sessionStorage", "characters/", "data/"])
    assert.equal(fakeCore.includes(forbidden), false, forbidden);
  assert.equal(app.includes('invoke("chat_'), false);
  assert.equal(app.includes("runtime_lifecycle_snapshot"), false);
  assert.equal(app.includes("window.setInterval"), false);
});

test("CSP admits only controlled character URLs and keeps network/media sources closed", () => {
  const csp = tauriConfig.app.security.csp;
  assert.match(csp, /img-src 'self'/);
  assert.match(csp, /http:\/\/sakura-character\.localhost/);
  assert.match(csp, /sakura-character:/);
  assert.match(csp, /connect-src 'self' ipc: http:\/\/ipc\.localhost/);
  assert.match(csp, /media-src 'none'/);
  assert.equal(csp.includes("data:"), false);
  assert.equal(csp.includes("https:"), false);
});
