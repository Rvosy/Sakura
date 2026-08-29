import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  installDevtoolsShortcutGuard,
  isDevtoolsShortcut,
} from "../core/devtools-guard.js";

const frontendEntrypoints = [
  "../app.js",
  "../settings/settings.js",
  "../history/history.js",
  "../capture/capture-controller.js",
  "../runtime-log/runtime-log.js",
];

test("devtools shortcut classifier covers first-party desktop combinations", () => {
  assert.equal(isDevtoolsShortcut({ key: "F12" }), true);
  for (const key of ["c", "I", "j"]) {
    assert.equal(isDevtoolsShortcut({ key, ctrlKey: true, shiftKey: true }), true);
  }
  assert.equal(isDevtoolsShortcut({ key: "i", metaKey: true, altKey: true }), true);
  assert.equal(isDevtoolsShortcut({ key: "i", ctrlKey: true }), false);
  assert.equal(isDevtoolsShortcut({ key: "Escape" }), false);
});

test("devtools shortcut guard captures and consumes blocked keys", () => {
  let registration = null;
  const target = {
    addEventListener(type, handler, options) {
      registration = { type, handler, options };
    },
  };
  installDevtoolsShortcutGuard(target);
  assert.equal(registration.type, "keydown");
  assert.deepEqual(registration.options, { capture: true });

  let prevented = false;
  let stopped = false;
  registration.handler({
    key: "F12",
    preventDefault() { prevented = true; },
    stopImmediatePropagation() { stopped = true; },
  });
  assert.equal(prevented, true);
  assert.equal(stopped, true);
});

test("every first-party frontend entrypoint installs the shared devtools guard", () => {
  for (const relativePath of frontendEntrypoints) {
    const source = readFileSync(new URL(relativePath, import.meta.url), "utf8");
    assert.match(source, /import \{ installDevtoolsShortcutGuard \}/);
    assert.match(source, /installDevtoolsShortcutGuard\(\);/);
  }
});

test("every first-party native WebView disables devtools", () => {
  const config = JSON.parse(readFileSync(
    new URL("../../src-tauri/tauri.conf.json", import.meta.url),
    "utf8",
  ));
  assert.equal(config.app.windows.find(({ label }) => label === "main")?.devtools, false);

  for (const relativePath of [
    "../../src-tauri/src/product_shell.rs",
    "../../src-tauri/src/history_window.rs",
    "../../src-tauri/src/runtime_log_window.rs",
    "../../src-tauri/src/capture.rs",
  ]) {
    const source = readFileSync(new URL(relativePath, import.meta.url), "utf8");
    assert.match(source, /\.devtools\(false\)/);
  }
});
