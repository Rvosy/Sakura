import assert from "node:assert/strict";
import test from "node:test";

import { createScreenAttachmentController } from "../chat/screen-attachment-controller.js";

class FakeElement {
  constructor() {
    this.listeners = new Map();
    this.attributes = new Map();
    this.dataset = {};
    this.hidden = false;
    this.disabled = false;
    this.focused = false;
  }

  addEventListener(name, listener) { this.listeners.set(name, listener); }
  emit(name, event = {}) { this.listeners.get(name)?.(event); }
  setAttribute(name, value) { this.attributes.set(name, value); }
  focus() { this.focused = true; }
  contains(target) { return target === this; }
}

function harness({
  waitForMotion = async () => {},
  beforeOpen = async () => {},
  openSurface = async () => {},
  closeSurface = async () => {},
  measureSurface = () => null,
} = {}) {
  const composer = new FakeElement();
  const toggle = new FakeElement();
  const menu = new FakeElement();
  const captureItem = new FakeElement();
  const calls = [];
  const errors = [];
  const controller = createScreenAttachmentController({
    composer,
    toggle,
    menu,
    captureItem,
    invoke: async (...args) => { calls.push(args); },
    onError: (message) => errors.push(message),
    requestFrame: (callback) => callback(),
    waitForMotion,
    beforeOpen,
    openSurface,
    closeSurface,
    measureSurface,
  });
  return { composer, toggle, menu, captureItem, calls, errors, controller };
}

test("plus control opens the toolbar overlay and starts one native capture action", async () => {
  const env = harness();
  assert.equal(env.menu.hidden, true);
  assert.equal(env.composer.dataset.attachmentMenu, "closed");

  env.toggle.emit("click");
  await new Promise(setImmediate);
  assert.equal(env.controller.isOpen(), true);
  assert.equal(env.menu.hidden, false);
  assert.equal(env.menu.dataset.open, "true");
  assert.equal(env.composer.dataset.attachmentMenu, "open");
  assert.equal(env.captureItem.focused, true);

  env.captureItem.emit("click");
  await new Promise(setImmediate);
  assert.deepEqual(env.calls, [["start_screen_capture"]]);
  assert.equal(env.controller.isOpen(), false);
  assert.equal(env.toggle.disabled, true);
  assert.equal(env.menu.hidden, true);
});

test("tool dock acquires and releases its native click surface", async () => {
  const surfaces = [];
  const rect = [130, 882, 216, 88];
  const env = harness({
    openSurface: async (value) => surfaces.push(["open", value]),
    closeSurface: async () => surfaces.push(["close"]),
    measureSurface: () => rect,
  });

  env.toggle.emit("click");
  await new Promise(setImmediate);
  assert.deepEqual(surfaces, [["open", rect]]);
  env.toggle.emit("click");
  await new Promise(setImmediate);
  assert.deepEqual(surfaces, [["open", rect], ["close"]]);
});

test("attachment menu reverses its own motion without changing composer geometry", async () => {
  const pending = [];
  const env = harness({
    waitForMotion: () => new Promise((resolve) => pending.push(resolve)),
  });

  env.toggle.emit("click");
  await new Promise(setImmediate);
  assert.equal(env.controller.isOpen(), true);
  assert.equal(env.menu.hidden, false);
  assert.equal(env.menu.dataset.open, "true");
  assert.equal("accessoryHeight" in env.composer.dataset, false);

  env.toggle.emit("click");
  assert.equal(env.controller.isOpen(), false);
  assert.equal(env.composer.dataset.attachmentMenu, "closed");
  assert.equal(env.menu.dataset.open, "false");
  assert.equal(env.menu.hidden, false, "closing motion finishes before hidden is restored");
  pending.shift()();
  await new Promise(setImmediate);
  assert.equal(env.menu.hidden, true);
});

test("opaque screenshot attachments replace and release each other but a sent one is consumed by chat", async () => {
  const env = harness();
  const first = `screen-${"1".repeat(32)}`;
  const second = `screen-${"2".repeat(32)}`;

  assert.equal(env.controller.handleAttached({ attachmentId: first, width: 640, height: 480 }), true);
  assert.equal(env.controller.attachmentId(), first);
  assert.equal(env.toggle.dataset.attached, "true");
  assert.equal(env.controller.handleAttached({ attachmentId: second, width: 320, height: 200 }), true);
  await Promise.resolve();
  assert.deepEqual(env.calls, [[
    "release_screen_attachment",
    { payload: { attachmentId: first } },
  ]]);

  assert.equal(env.controller.markSent(second), true);
  assert.equal(env.controller.attachmentId(), null);
  assert.equal(env.calls.length, 1);
});

test("generation invalidation releases an unsent attachment best-effort", async () => {
  const env = harness();
  const attachmentId = `screen-${"a".repeat(32)}`;
  env.controller.handleAttached({ attachmentId, width: 100, height: 100 });
  env.controller.invalidate();
  await Promise.resolve();
  assert.deepEqual(env.calls, [[
    "release_screen_attachment",
    { payload: { attachmentId } },
  ]]);
  assert.equal(env.controller.attachmentId(), null);
  assert.equal(env.menu.hidden, true);
});
