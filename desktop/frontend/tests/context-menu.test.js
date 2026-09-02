import assert from "node:assert/strict";
import test from "node:test";

import {
  PetContextMenu,
  PRODUCT_MENU_ACTIONS,
  clampMenuPosition,
  moveMenuFocusIndex,
  validateProductMenuManifest,
} from "../pet/context_menu.js";

function deferred() {
  let resolve;
  const promise = new Promise((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

test("the custom product menu uses the existing Rust action IDs", () => {
  assert.deepEqual(PRODUCT_MENU_ACTIONS, {
    visibility: "sakura.pet.visibility.toggle",
    subtitle: "sakura.chat.subtitle.toggle",
    topmost: "sakura.pet.topmost.toggle",
    history: "sakura.history.open",
    runtimeLog: "sakura.runtime-log.open",
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
      PRODUCT_MENU_ACTIONS.subtitle,
      PRODUCT_MENU_ACTIONS.topmost,
      PRODUCT_MENU_ACTIONS.history,
      PRODUCT_MENU_ACTIONS.runtimeLog,
      PRODUCT_MENU_ACTIONS.settings,
      PRODUCT_MENU_ACTIONS.settings,
      PRODUCT_MENU_ACTIONS.exit,
    ],
    checkedActions: [PRODUCT_MENU_ACTIONS.subtitle, PRODUCT_MENU_ACTIONS.topmost],
    unavailableReason: "尚未迁移",
  });
  assert.deepEqual(manifest.availableActions, [
    PRODUCT_MENU_ACTIONS.visibility,
    PRODUCT_MENU_ACTIONS.subtitle,
    PRODUCT_MENU_ACTIONS.topmost,
    PRODUCT_MENU_ACTIONS.history,
    PRODUCT_MENU_ACTIONS.runtimeLog,
    PRODUCT_MENU_ACTIONS.settings,
    PRODUCT_MENU_ACTIONS.exit,
  ]);
  assert.deepEqual(manifest.checkedActions, [PRODUCT_MENU_ACTIONS.subtitle, PRODUCT_MENU_ACTIONS.topmost]);
  assert.equal(manifest.unavailableReason, "尚未迁移");
  assert.throws(() => validateProductMenuManifest({ schemaVersion: 1 }), /MANIFEST_INVALID/);
  assert.throws(() => validateProductMenuManifest({
    schemaVersion: 2,
    availableActions: [],
    checkedActions: [],
  }), /MANIFEST_INVALID/);
});

test("keyboard focus wraps and supports Home and End", () => {
  assert.equal(moveMenuFocusIndex(0, 3, "ArrowUp"), 2);
  assert.equal(moveMenuFocusIndex(2, 3, "ArrowDown"), 0);
  assert.equal(moveMenuFocusIndex(1, 3, "Home"), 0);
  assert.equal(moveMenuFocusIndex(1, 3, "End"), 2);
  assert.equal(moveMenuFocusIndex(1, 0, "ArrowDown"), -1);
});

test("repositioning an open menu commits its native surface before animation", async () => {
  const classNames = new Set(["is-open"]);
  const classMutations = [];
  const item = {
    dataset: { menuAction: PRODUCT_MENU_ACTIONS.settings },
    disabled: false,
    setAttribute() {},
    focus() { focusCount += 1; },
  };
  let focusCount = 0;
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

  await contextMenu.openAt(400, 500, {
    schemaVersion: 1,
    availableActions: [PRODUCT_MENU_ACTIONS.settings],
    checkedActions: [],
  });

  assert.deepEqual(classMutations, ["remove:is-open", "add:is-open"]);
  assert.equal(classNames.has("is-open"), true);
  assert.equal(menu.style.left, "400px");
  assert.equal(menu.style.top, "500px");
  assert.equal(focusCount, 0);

  await contextMenu.openAt(400, 500, {
    schemaVersion: 1,
    availableActions: [PRODUCT_MENU_ACTIONS.settings],
    checkedActions: [],
  }, { focusFirst: true });
  assert.equal(focusCount, 1);
  assert.equal(classNames.has("is-keyboard-open"), true);
});

test("menu surface expansion clears focused WebView controls before the native resize", async () => {
  const calls = [];
  const menu = {
    hidden: true,
    style: {},
    offsetWidth: 226,
    offsetHeight: 330,
    classList: {
      add() {},
      remove() {},
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
    querySelectorAll() {
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
    documentRef,
    windowRef,
    beforeSurfaceResize: () => calls.push("before-native-resize"),
    invoke: async (command) => calls.push(command),
  });

  await contextMenu.openAt(400, 500, {
    schemaVersion: 1,
    availableActions: [],
    checkedActions: [],
  });

  assert.deepEqual(calls, ["before-native-resize", "set_pet_context_menu_surface"]);
});

test("a portrait surface mutation invalidates an opening menu before restoring its native surface", async () => {
  const surfaceCommit = deferred();
  const calls = [];
  const classNames = new Set();
  const menu = {
    hidden: true,
    style: {},
    offsetWidth: 226,
    offsetHeight: 330,
    classList: {
      add(name) { classNames.add(name); },
      remove(...names) { names.forEach((name) => classNames.delete(name)); },
      contains(name) { return classNames.has(name); },
    },
    addEventListener() {},
    removeEventListener() {},
    contains() { return false; },
    getBoundingClientRect() { return { width: 226, height: 330 }; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
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
    documentRef,
    windowRef,
    invoke: async (command) => {
      calls.push({ command, hidden: menu.hidden });
      if (command === "set_pet_context_menu_surface") await surfaceCommit.promise;
    },
  });

  const opening = contextMenu.openAt(400, 500, {
    schemaVersion: 1,
    availableActions: [],
    checkedActions: [],
  });
  await Promise.resolve();
  assert.equal(menu.hidden, false);

  await contextMenu.dismissForSurfaceTransition();
  assert.deepEqual(calls.at(-1), { command: "close_pet_context_menu", hidden: true });
  surfaceCommit.resolve();
  await opening;

  assert.equal(menu.hidden, true);
  assert.equal(classNames.has("is-open"), false);
});

test("the pointer press that dismisses an open menu cannot fall through into native pet drag", () => {
  const calls = [];
  const documentListeners = new Map();
  const menu = {
    hidden: false,
    style: {},
    classList: {
      add() {},
      remove() {},
    },
    addEventListener() {},
    removeEventListener() {},
    contains() {
      return false;
    },
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
  };
  const documentRef = {
    activeElement: null,
    addEventListener(name, listener, options) {
      documentListeners.set(name, { listener, options });
    },
    removeEventListener() {},
  };
  const windowRef = {
    addEventListener() {},
    removeEventListener() {},
  };
  new PetContextMenu({
    menu,
    documentRef,
    windowRef,
    invoke: async (command) => calls.push(command),
  });
  const pointerDown = documentListeners.get("pointerdown");
  assert.equal(pointerDown.options, true, "dismissal must run during capture before drag regions");

  pointerDown.listener({
    button: 0,
    target: {},
    preventDefault: () => calls.push("prevent-default"),
    stopPropagation: () => calls.push("stop-propagation"),
  });

  assert.equal(menu.hidden, true);
  assert.deepEqual(calls, [
    "prevent-default",
    "stop-propagation",
    "close_pet_context_menu",
  ]);
});
