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
  emit(name) { for (const callback of this.listeners.get(name) || []) callback({ target: this }); }
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
}

function fixture(options = {}) {
  const document = { body: new Element("body"), createElement: tag => new Element(tag) };
  return { document, controller: createErrorDialog({ document, ...options }) };
}

test("error details preserve source diagnostics as text and redact credentials", () => {
  const { controller } = fixture();
  const markup = '<img src=x onerror="window.injected=true">';
  const dialog = controller.show({
    error: {
      code: "MARKETPLACE_FETCH_FAILED",
      message: `request rejected ${markup}`,
      details: { diagnostics: {
        diagnostic: "C:\\Sakura\\cache: winerror=10061 api_key=fixture-private-value",
        exception_stack: "Traceback\n  File download.py, line 12",
      } },
      cause: new Error("tcp connection refused"),
    },
  });
  const diagnostic = dialog.querySelector("pre");
  for (const value of ["MARKETPLACE_FETCH_FAILED", markup, "C:\\Sakura\\cache", "10061", "Traceback\n", "tcp connection refused"]) {
    assert.ok(diagnostic.textContent.includes(value), value);
  }
  assert.ok(!diagnostic.textContent.includes("fixture-private-value"));
  assert.ok(diagnostic.textContent.includes("[REDACTED]"));
  assert.equal(diagnostic.children.length, 0);
  assert.equal(dialog.querySelector("img"), null);
});

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

test("closing the dialog allows reopening it without adding another window", () => {
  let opened = 0, closed = 0;
  const { document, controller } = fixture({ onOpen: () => opened++, onClose: () => closed++ });
  const dialog = controller.show({ error: "failure" });
  dialog.querySelector("button").emit("click");
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
