import assert from "node:assert/strict";
import test from "node:test";

import { createBubbleScroll } from "../pet/bubble-scroll.js";

class FakeViewport {
  constructor() {
    this.clientHeight = 40;
    this.scrollHeight = 40;
    this.scrollTop = 0;
    this.listeners = new Map();
    this.value = "";
  }

  set textContent(value) {
    this.value = value;
    this.scrollHeight = Math.max(this.clientHeight, Array.from(value).length * 10);
  }

  get textContent() {
    return this.value;
  }

  addEventListener(type, listener) {
    this.listeners.set(type, listener);
  }

  removeEventListener(type, listener) {
    if (this.listeners.get(type) === listener) this.listeners.delete(type);
  }

  scrollTo(value) {
    this.scrollTop = value;
    this.listeners.get("scroll")?.();
  }
}

test("continuous typing follows the end until the reader scrolls upward", () => {
  const viewport = new FakeViewport();
  const scroll = createBubbleScroll({ viewport, bottomThresholdPx: 2 });
  scroll.beginReply();
  for (const text of ["a", "ab", "abcd", "abcdef", "abcdefghij"]) scroll.updateText(text);
  assert.equal(viewport.scrollTop, 60);

  viewport.scrollTo(10);
  scroll.updateText("abcdefghijk");
  assert.equal(viewport.scrollTop, 10);
  assert.equal(scroll.snapshot().following, false);
});

test("returning to the end restores follow, while skip always reveals the final line", () => {
  const viewport = new FakeViewport();
  const scroll = createBubbleScroll({ viewport, bottomThresholdPx: 2 });
  scroll.beginReply();
  scroll.updateText("abcdefghij");
  viewport.scrollTo(10);
  scroll.updateText("abcdefghijk");

  viewport.scrollTo(viewport.scrollHeight - viewport.clientHeight);
  scroll.updateText("abcdefghijkl");
  assert.equal(viewport.scrollTop, 80);

  viewport.scrollTo(0);
  scroll.updateText("abcdefghijklmnop", { forceEnd: true });
  assert.equal(viewport.scrollTop, 120);
});

test("a new reply resets manual scroll state and dispose detaches the listener", () => {
  const viewport = new FakeViewport();
  const scroll = createBubbleScroll({ viewport, bottomThresholdPx: 2 });
  scroll.updateText("abcdefghij");
  viewport.scrollTo(0);
  assert.equal(scroll.snapshot().following, false);

  scroll.beginReply();
  scroll.updateText("new reply");
  assert.equal(viewport.scrollTop, 50);
  scroll.dispose();
  assert.equal(viewport.listeners.has("scroll"), false);
});
