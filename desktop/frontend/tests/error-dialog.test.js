import assert from "node:assert/strict";
import test from "node:test";
import { createErrorDialog } from "../core/error-dialog.js";

// Replace only the native DOM/dialog boundary; the real presenter owns all text and events.
class Element {
  constructor(tagName) {
    this.tagName = tagName;
    this.children = [];
    this.dataset = {};
    this.attributes = new Map();
    this.listeners = new Map();
    this.open = false;
    this.showModalCalls = 0;
    this.text = "";
  }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return this.text + this.children.map(child => child.textContent).join(""); }
  append(...children) {
    for (const child of children) { child.parent = this; this.children.push(child); }
  }
  remove() {
    if (this.parent) this.parent.children.splice(this.parent.children.indexOf(this), 1);
    this.parent = null;
  }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  removeAttribute(name) { this.attributes.delete(name); }
  addEventListener(name, callback) {
    if (!this.listeners.has(name)) this.listeners.set(name, []);
    this.listeners.get(name).push(callback);
  }
  async emit(name) { for (const callback of this.listeners.get(name) || []) await callback({ target: this }); }
  showModal() {
    assert.equal(this.open, false, "an open dialog must not be opened again");
    this.open = true;
    this.showModalCalls += 1;
  }
  close() {
    if (!this.open) return;
    this.open = false;
    this.emit("close");
  }
  querySelector(tagName) {
    for (const child of this.children) {
      if (child.tagName === tagName) return child;
      const match = child.querySelector(tagName);
      if (match) return match;
    }
    return null;
  }
  querySelectorAll(tagName) {
    return this.children.flatMap(child => [
      ...(child.tagName === tagName ? [child] : []), ...child.querySelectorAll(tagName),
    ]);
  }
}

function fixture(options = {}) {
  const document = { body: new Element("body"), createElement: tag => new Element(tag) };
  return { document, controller: createErrorDialog({ document, ...options }) };
}

const button = (dialog, text) => dialog.querySelectorAll('button').find(element => element.textContent === text);

test("successive failures update one open dialog and its accessible description", () => {
  const { document, controller } = fixture();
  const first = controller.show({ title: "Download failed", message: "Try again", error: "first failure" });
  assert.equal(first.getAttribute("aria-describedby"), first.querySelector("p").id);
  const second = controller.show({ title: "Install failed", error: "second failure" });
  assert.equal(second, first);
  assert.equal(document.body.children.length, 1);
  assert.equal(first.showModalCalls, 1);
  assert.equal(first.querySelector("h2").textContent, "Install failed");
  assert.equal(first.querySelector("pre").textContent, "second failure");
  assert.equal(first.querySelector("p").hidden, true);
  assert.equal(first.getAttribute("aria-describedby"), null);
});

test("closing the dialog allows reopening it without adding another window", async () => {
  let opened = 0, closed = 0;
  const { document, controller } = fixture({ onOpen: () => opened++, onClose: () => closed++ });
  const dialog = controller.show({ error: "failure" });
  await button(dialog, "关闭").emit("click");
  assert.equal(controller.isOpen, false);
  assert.equal(closed, 1);
  controller.close();
  assert.equal(closed, 1);
  assert.equal(controller.show({ error: "failure" }), dialog);
  assert.equal(controller.isOpen, true);
  assert.equal(opened, 2);
  assert.equal(document.body.children.length, 1);
});

test("disposing prevents a late failure from recreating the dialog", () => {
  for (const openedFirst of [false, true]) {
    const { document, controller } = fixture();
    if (openedFirst) controller.show({ error: "failure" });
    controller.dispose();
    controller.dispose();
    assert.equal(controller.show({ error: "late failure" }), null);
    assert.equal(controller.isOpen, false);
    assert.equal(document.body.children.length, 0);
  }
});
