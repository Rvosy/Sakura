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

function harness({ onLayoutChange } = {}) {
  const composer = new FakeElement();
  const toggle = new FakeElement();
  const menu = new FakeElement();
  const captureItem = new FakeElement();
  const calls = [];
  const errors = [];
  let layouts = 0;
  const controller = createScreenAttachmentController({
    composer,
    toggle,
    menu,
    captureItem,
    invoke: async (...args) => { calls.push(args); },
    onLayoutChange: () => {
      layouts += 1;
      return onLayoutChange?.();
    },
    onError: (message) => errors.push(message),
  });
  return { composer, toggle, menu, captureItem, calls, errors, controller, layouts: () => layouts };
}

test("plus control expands the composer and starts one native capture action", async () => {
  const env = harness();
  assert.equal(env.menu.hidden, true);
  assert.equal(env.composer.dataset.accessoryHeight, "0");

  env.toggle.emit("click");
  await new Promise(setImmediate);
  assert.equal(env.controller.isOpen(), true);
  assert.equal(env.menu.hidden, false);
  assert.equal(env.composer.dataset.accessoryHeight, "60");
  assert.equal(env.captureItem.focused, true);

  env.captureItem.emit("click");
  await new Promise(setImmediate);
  assert.deepEqual(env.calls, [["start_screen_capture"]]);
  assert.equal(env.controller.isOpen(), false);
  assert.equal(env.toggle.disabled, true);
  assert.equal(env.layouts() >= 2, true);
});

test("attachment contents change only after each target composer rectangle is acknowledged", async () => {
  const pending = [];
  const env = harness({
    onLayoutChange: () => new Promise((resolve) => pending.push(resolve)),
  });

  env.toggle.emit("click");
  assert.equal(env.controller.isOpen(), true);
  assert.equal(env.composer.dataset.accessoryHeight, "60");
  assert.equal(env.menu.hidden, true, "one-row geometry must not expose clipped menu contents");
  pending.shift()();
  await new Promise(setImmediate);
  assert.equal(env.menu.hidden, false);

  env.toggle.emit("click");
  assert.equal(env.controller.isOpen(), false);
  assert.equal(env.composer.dataset.accessoryHeight, "0");
  assert.equal(env.menu.hidden, false, "expanded contents stay stable until collapse is acknowledged");
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
