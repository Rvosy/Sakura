import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const onboardingHtml = await readFile(new URL("../onboarding/index.html", import.meta.url), "utf8");
const onboardingSource = await readFile(new URL("../onboarding/onboarding.js", import.meta.url), "utf8");
const settingsHtml = await readFile(new URL("../settings/index.html", import.meta.url), "utf8");
const settingsSource = await readFile(new URL("../settings/settings.js", import.meta.url), "utf8");
const capabilityShell = await readFile(new URL("../settings/capability-shell.js", import.meta.url), "utf8");
const rustMain = await readFile(new URL("../../src-tauri/src/main.rs", import.meta.url), "utf8");
const externalHelp = await readFile(
  new URL("../../../packaging/macos-open-help.html", import.meta.url),
  "utf8",
);

test("macOS launch help is secondary to the two first-run configuration routes", () => {
  assert.equal((onboardingHtml.match(/class="route-card(?:\s|\")/g) || []).length, 2);
  assert.match(onboardingHtml, /id="macosOpenHelpButton"[^>]*hidden[^>]*>[\s\S]*?应用打开遇到问题？/);
  assert.match(onboardingSource, /sections\?\.\["open-help"\]\?\.status !== "available"/);
});

test("onboarding and settings present the bounded Apple exception workflow", () => {
  for (const document of [onboardingHtml, settingsHtml]) {
    assert.match(document, /隐私与安全性/);
    assert.match(document, /仍要打开/);
    assert.match(document, /约一小时/);
    assert.match(document, /不会关闭系统的安全检查/);
  }
  assert.match(settingsHtml, /data-page="open-help"[^>]*data-hide-when-unavailable[^>]*hidden/);
  assert.match(settingsHtml, /id="page-open-help"/);
  assert.match(capabilityShell, /data-hide-when-unavailable/);
  assert.match(capabilityShell, /item\.hidden = unavailable/);
});

test("launch-help actions use fixed first-party Tauri commands", () => {
  assert.match(onboardingSource, /settings_macos_open_system_settings/);
  assert.match(onboardingSource, /settings_macos_open_apple_support/);
  assert.match(settingsSource, /rootSettingsClient\.macosOpenSystemSettings\(\)/);
  assert.match(settingsSource, /rootSettingsClient\.macosOpenAppleSupport\(\)/);
  assert.match(rustMain, /product_shell::validate_settings_window\(&window\)\?[\s\S]*macos_open_help::open_system_settings\(\)/);
  assert.match(rustMain, /product_shell::validate_settings_window\(&window\)\?[\s\S]*macos_open_help::open_apple_support\(\)/);
});

test("external launch help is available before the application opens and avoids unsafe bypasses", () => {
  assert.match(externalHelp, /Sakura 无法在 macOS 上打开？/);
  assert.match(externalHelp, /https:\/\/github\.com\/Rvosy\/Sakura\/releases/);
  assert.match(
    externalHelp,
    /https:\/\/support\.apple\.com\/guide\/mac-help\/open-a-mac-app-from-an-unknown-developer-mh40616\/mac/,
  );
  assert.match(externalHelp, /只为这份应用\s*保存本机例外/);
  assert.doesNotMatch(externalHelp, /xattr|spctl\s+--master-disable|csrutil|sudo\s+/i);
});
