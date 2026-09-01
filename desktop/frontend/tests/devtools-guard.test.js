import assert from "node:assert/strict";
import test from "node:test";

import {
  installDevtoolsShortcutGuard,
  isDevtoolsShortcut,
} from "../core/devtools-guard.js";

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
