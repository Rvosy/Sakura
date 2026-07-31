import assert from "node:assert/strict";
import test from "node:test";

import {
  PetContextMenu,
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
    { x: 654, y: 646 },
  );
  assert.deepEqual(
    clampMenuPosition(-20, -10, 226, 330, { width: 900, height: 996 }),
    { x: 20, y: 20 },
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

test("repositioning an open menu restarts its entrance animation", () => {
  const classNames = new Set(["is-open"]);
  const classMutations = [];
  const item = {
    dataset: { menuAction: PRODUCT_MENU_ACTIONS.settings },
    disabled: false,
    setAttribute() {},
    focus() {},
  };
  const menu = {
    hidden: false,
    style: {},
    offsetWidth: 226,
    offsetHeight: 330,
    classList: {
      add(name) {
        classNames.add(name);
        classMutations.push(`add:${name}`);
      },
      remove(name) {
        classNames.delete(name);
        classMutations.push(`remove:${name}`);
      },
    },
    addEventListener() {},
    removeEventListener() {},
    contains() {
      return false;
    },
    getBoundingClientRect() {
      return { width: 226, height: 330 };
    },
    querySelector() {
      return null;
    },
    querySelectorAll(selector) {
      if (selector === "[data-menu-action]" || selector === "[data-menu-action]:not(:disabled)") {
        return [item];
      }
      return [];
    },
  };
  const documentRef = {
    activeElement: null,
    addEventListener() {},
    removeEventListener() {},
  };
  const windowRef = {
    innerWidth: 900,
    innerHeight: 996,
    addEventListener() {},
    removeEventListener() {},
  };
  const contextMenu = new PetContextMenu({
    menu,
    invoke: async () => {},
    documentRef,
    windowRef,
  });

  contextMenu.openAt(400, 500, {
    schemaVersion: 1,
    availableActions: [PRODUCT_MENU_ACTIONS.settings],
  });

  assert.deepEqual(classMutations, ["remove:is-open", "add:is-open"]);
  assert.equal(classNames.has("is-open"), true);
  assert.equal(menu.style.left, "400px");
  assert.equal(menu.style.top, "500px");
});
