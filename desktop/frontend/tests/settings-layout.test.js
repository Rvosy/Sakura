import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const styles = readFileSync(new URL("../settings/styles.css", import.meta.url), "utf8");
const nativeProductShell = readFileSync(
  new URL("../../src-tauri/src/product_shell.rs", import.meta.url),
  "utf8",
);
const nativeMain = readFileSync(new URL("../../src-tauri/src/main.rs", import.meta.url), "utf8");

function declaration(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return styles.match(new RegExp(`${escaped}\\s*\\{([\\s\\S]*?)\\n\\}`, "m"))?.[1] || "";
}

test("settings footer keeps its full action height in short windows", () => {
  const shell = declaration(".settings-shell");
  const cards = declaration(".nav-card,\n.detail-card");

  assert.match(shell, /grid-template-rows:\s*minmax\(0, 1fr\);/);
  assert.match(shell, /min-height:\s*0;/);
  assert.match(cards, /min-height:\s*0;/);
  assert.match(cards, /box-shadow:\s*none;/);
  assert.match(declaration("footer"), /flex:\s*0 0 auto;/);
  assert.match(declaration(".page-scroll"), /min-height:\s*0;/);
});

test("short-window navigation scrollbar keeps the themed compact style", () => {
  const navigation = declaration(".nav-list");
  const scrollbar = declaration(".nav-list::-webkit-scrollbar");
  const buttons = declaration(".nav-list::-webkit-scrollbar-button");
  const thumb = declaration(".nav-list::-webkit-scrollbar-thumb");

  assert.match(navigation, /scrollbar-color:\s*var\(--sakura-border\) transparent;/);
  assert.match(scrollbar, /width:\s*10px;/);
  assert.match(buttons, /display:\s*none;/);
  assert.match(thumb, /background-color:\s*color-mix\(in srgb, var\(--sakura-border\) 80%, transparent\);/);
});

test("native settings resize reveals the page color instead of a black frame", () => {
  const settingsWindow = nativeProductShell.match(
    /WebviewWindowBuilder::new\([\s\S]*?SETTINGS_WINDOW_CREATE_FAILED/,
  )?.[0] || "";

  assert.match(settingsWindow, /\.background_color\(Color\(255, 246, 250, 255\)\)/);
  assert.match(nativeProductShell, /bind_settings_webview_resize\(&window\)/);

  const resizeBinding = nativeProductShell.match(
    /fn bind_settings_webview_resize\([\s\S]*?\n\}/,
  )?.[0] || "";
  assert.match(resizeBinding, /\.set_size\(initial_size\)/);
  assert.match(resizeBinding, /WindowEvent::Resized\(size\)/);
  assert.match(resizeBinding, /webview\.set_size\(\*size\)/);
  assert.doesNotMatch(settingsWindow, /\.auto_resize\(\)/);
});

test("native resize fallback follows the validated page background theme", () => {
  assert.match(nativeProductShell, /fn parse_theme_color\([\s\S]*?Color\([\s\S]*?255\)/);
  assert.match(nativeProductShell, /set_background_color\(Some\(parse_theme_color\(value\)\?\)\)/);

  const sync = nativeMain.match(
    /fn sync_settings_window_appearance_background\([\s\S]*?\n\}/,
  )?.[0] || "";
  assert.match(sync, /theme_tokens[\s\S]*?\.get\("pageBackground"\)/);
  assert.match(sync, /set_settings_window_theme_background/);
  assert.equal((nativeMain.match(/sync_settings_window_appearance_background\(&window, &publication\)\?/g) || []).length, 4);
});

test("settings width tokens interpolate instead of shrinking content at a breakpoint", () => {
  const root = declaration(":root");
  const shell = declaration(".settings-shell");

  assert.match(root, /--settings-nav-width:\s*clamp\(/);
  assert.match(root, /--settings-content-gutter:\s*clamp\(/);
  assert.match(shell, /grid-template-columns:\s*var\(--settings-nav-width\) minmax\(0, 1fr\);/);

  const narrowBreakpoint = styles.match(/@media \(max-width: 940px\) \{([\s\S]*?)\n\}/)?.[1] || "";
  assert.doesNotMatch(
    narrowBreakpoint,
    /(?:^|\n)  (?:\.settings-shell|\.page-scroll|footer|\.form-row)\s*\{/,
  );
});

test("regular settings pages use wide space without stretching cards below a readable width", () => {
  const page = declaration(".settings-page");
  const regularPage = declaration(".settings-page:not(.admin-page)");
  const trailingGroup = declaration(".settings-page:not(.admin-page) > :last-child:nth-child(odd)");

  assert.match(page, /width:\s*100%;/);
  assert.match(page, /max-width:\s*none;/);
  assert.match(
    regularPage,
    /grid-template-columns:\s*repeat\(auto-fit, minmax\(min\(100%, 600px\), 1fr\)\);/,
  );
  assert.match(regularPage, /align-items:\s*start;/);
  assert.match(trailingGroup, /grid-column:\s*1 \/ -1;/);
});

test("provider actions remain a single equal-width row", () => {
  const actions = declaration("#providerDetail > .detail-actions");
  const buttons = declaration("#providerDetail > .detail-actions > button");

  assert.match(actions, /flex-wrap:\s*nowrap;/);
  assert.match(buttons, /flex:\s*1 1 0;/);
  assert.match(buttons, /min-width:\s*0;/);
});
