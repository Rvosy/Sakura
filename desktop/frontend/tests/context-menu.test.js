import assert from "node:assert/strict";
import test from "node:test";

import {
  PRODUCT_MENU_ACTIONS,
  clampMenuPosition,
  moveMenuFocusIndex,
  validateProductMenuManifest,
} from "../pet/context_menu.js";

test("the custom product menu uses the existing Rust action IDs", () => {
  assert.deepEqual(PRODUCT_MENU_ACTIONS, {
    visibility: "sakura.pet.visibility.toggle",
    settings: "sakura.settings.open",
    exit: "sakura.app.exit",
  });
});

test("menu positioning remains inside every viewport edge", () => {
  assert.deepEqual(
    clampMenuPosition(899, 995, 226, 330, { width: 900, height: 996 }),
    { x: 666, y: 658 },
  );
  assert.deepEqual(
    clampMenuPosition(-20, -10, 226, 330, { width: 900, height: 996 }),
    { x: 8, y: 8 },
  );
});

test("the capability manifest fails closed and ignores unknown actions", () => {
  const manifest = validateProductMenuManifest({
    schemaVersion: 1,
    availableActions: [
      PRODUCT_MENU_ACTIONS.visibility,
      "sakura.history.open",
      PRODUCT_MENU_ACTIONS.settings,
      PRODUCT_MENU_ACTIONS.settings,
      PRODUCT_MENU_ACTIONS.exit,
    ],
    unavailableReason: "尚未迁移",
  });
  assert.deepEqual(manifest.availableActions, [
    PRODUCT_MENU_ACTIONS.visibility,
    PRODUCT_MENU_ACTIONS.settings,
    PRODUCT_MENU_ACTIONS.exit,
  ]);
  assert.equal(manifest.unavailableReason, "尚未迁移");
  assert.throws(() => validateProductMenuManifest({ schemaVersion: 2 }), /MANIFEST_INVALID/);
});

test("keyboard focus wraps and supports Home and End", () => {
  assert.equal(moveMenuFocusIndex(0, 3, "ArrowUp"), 2);
  assert.equal(moveMenuFocusIndex(2, 3, "ArrowDown"), 0);
  assert.equal(moveMenuFocusIndex(1, 3, "Home"), 0);
  assert.equal(moveMenuFocusIndex(1, 3, "End"), 2);
  assert.equal(moveMenuFocusIndex(1, 0, "ArrowDown"), -1);
});
