import assert from "node:assert/strict";
import test from "node:test";

import { createScreenAttachmentController } from "../chat/screen-attachment-controller.js";

class FakeElement {
  constructor(ownerDocument) {
    this.ownerDocument = ownerDocument;
    this.listeners = new Map();
    this.attributes = new Map();
    this.dataset = {};
    this.children = [];
    this.hidden = false;
    this.disabled = false;
    this.focused = false;
  }

  addEventListener(name, listener) { this.listeners.set(name, listener); }
  emit(name, event = {}) { this.listeners.get(name)?.(event); }
  setAttribute(name, value) { this.attributes.set(name, value); }
  focus() { this.focused = true; }
  contains(target) { return target === this; }
  append(...values) { this.children.push(...values); }
  replaceChildren(...values) { this.children = [...values]; }
}

class FakeDocument {
  constructor() { this.activeElement = null; }
  createElement() { return new FakeElement(this); }
}

function harness({
  waitForMotion = async () => {},
  beforeOpen = async () => {},
  openSurface = async () => {},
  closeSurface = async () => {},
  measureSurface = () => null,
  invokeImpl = async () => undefined,
} = {}) {
  const doc = new FakeDocument();
  const composer = doc.createElement();
  const toggle = doc.createElement();
  const menu = doc.createElement();
  const captureItem = doc.createElement();
  const attachmentList = doc.createElement();
  const calls = [];
  const errors = [];
  const controller = createScreenAttachmentController({
    composer,
    toggle,
    menu,
    captureItem,
    attachmentList,
    invoke: async (...args) => {
      calls.push(args);
      return invokeImpl(...args);
    },
    onError: (message) => errors.push(message),
    requestFrame: (callback) => callback(),
    waitForMotion,
    beforeOpen,
    openSurface,
    closeSurface,
    measureSurface,
  });
  return { composer, toggle, menu, captureItem, attachmentList, calls, errors, controller };
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

test("opaque screenshot items append to one group and the sent group is consumed by chat", async () => {
  const env = harness();
  const first = `screen-${"1".repeat(32)}`;
  const firstItem = `shot-${"1".repeat(32)}`;
  const secondItem = `shot-${"2".repeat(32)}`;

  assert.equal(env.controller.handleAttached({
    attachmentId: first, itemId: firstItem, width: 640, height: 480, count: 1,
  }), true);
  assert.equal(env.controller.attachmentId(), first);
  assert.equal(env.toggle.dataset.attached, "true");
  assert.equal(env.toggle.dataset.attachmentCount, "1");
  assert.equal(env.controller.handleAttached({
    attachmentId: first, itemId: secondItem, width: 320, height: 200, count: 2,
  }), true);
  assert.equal(env.controller.attachments().length, 2);
  assert.equal(env.calls.length, 0);

  assert.equal(env.controller.markSent(first), true);
  assert.equal(env.controller.attachmentId(), null);
  assert.equal(env.attachmentList.hidden, true);
  assert.equal(env.composer.dataset.attachmentCount, "0");
});

test("one screenshot can be removed without releasing the remaining group", async () => {
  const attachmentId = `screen-${"a".repeat(32)}`;
  const firstItem = `shot-${"1".repeat(32)}`;
  const secondItem = `shot-${"2".repeat(32)}`;
  const env = harness({
    invokeImpl: async (command, value) => command === "remove_screen_attachment_item"
      ? { accepted: true, ...value.payload, count: value.payload.itemId === firstItem ? 1 : 0 }
      : undefined,
  });
  env.controller.handleAttached({ attachmentId, itemId: firstItem, width: 640, height: 480, count: 1 });
  env.controller.handleAttached({ attachmentId, itemId: secondItem, width: 320, height: 200, count: 2 });

  assert.equal(await env.controller.removeAttachment(firstItem), true);
  assert.deepEqual(env.controller.attachments(), [{ itemId: secondItem, width: 320, height: 200 }]);
  assert.equal(env.controller.attachmentId(), attachmentId);
  assert.equal(env.composer.dataset.attachmentCount, "1");
  assert.equal(await env.controller.removeAttachment(secondItem), true);
  assert.equal(env.controller.attachmentId(), null);
  assert.equal(env.composer.dataset.attachmentCount, "0");
  assert.equal(env.attachmentList.hidden, true);
});

test("six screenshots disable capture but keep the attachment menu available", () => {
  const env = harness();
  const attachmentId = `screen-${"a".repeat(32)}`;
  for (let index = 1; index <= 6; index += 1) {
    assert.equal(env.controller.handleAttached({
      attachmentId,
      itemId: `shot-${index.toString(16).padStart(32, "0")}`,
      width: 100,
      height: 100,
      count: index,
    }), true);
  }
  assert.equal(env.captureItem.disabled, true);
  assert.equal(env.toggle.disabled, false);
  assert.equal(env.toggle.dataset.attachmentCount, "6");
});

test("a pending send locks attachment controls and a rejected send retains the group", () => {
  const env = harness();
  const attachmentId = `screen-${"a".repeat(32)}`;
  env.controller.handleAttached({
    attachmentId,
    itemId: `shot-${"b".repeat(32)}`,
    width: 640,
    height: 480,
    count: 1,
  });

  env.controller.setSubmitting(true);
  assert.equal(env.toggle.disabled, true);
  assert.equal(env.attachmentList.children[0].children[1].disabled, true);
  env.controller.setSubmitting(false);
  assert.equal(env.controller.attachmentId(), attachmentId);
  assert.equal(env.controller.attachments().length, 1);
  assert.equal(env.toggle.disabled, false);
});

test("generation invalidation releases an unsent attachment best-effort", async () => {
  const env = harness();
  const attachmentId = `screen-${"a".repeat(32)}`;
  env.controller.handleAttached({
    attachmentId, itemId: `shot-${"b".repeat(32)}`, width: 100, height: 100, count: 1,
  });
  env.controller.invalidate();
  await Promise.resolve();
  assert.deepEqual(env.calls, [[
    "release_screen_attachment",
    { payload: { attachmentId } },
  ]]);
  assert.equal(env.controller.attachmentId(), null);
  assert.equal(env.menu.hidden, true);
});
