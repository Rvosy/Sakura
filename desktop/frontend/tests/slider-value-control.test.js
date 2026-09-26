import assert from "node:assert/strict";
import test from "node:test";
import { upgradeSliderControls } from "../settings/slider-value-control.js";

function fixture() {
  let current;
  let focused;
  let inputEvents = 0;
  function element() {
    const listeners = {};
    return {
      dataset: {}, style: {}, offsetWidth: 56,
      addEventListener(type, callback) { (listeners[type] ||= []).push(callback); },
      fire(type, properties = {}) {
        for (const callback of listeners[type] || []) callback({preventDefault() {}, ...properties});
      },
      focus() { focused = this; },
      select() {},
      replaceWith(next) {
        if (focused === this) {
          focused = null;
          this.fire("blur");
        }
        // Model the browser's replacement failure when blur has already replaced this node.
        assert.equal(current, this, "the focused editor must only be replaced once");
        current = next;
      },
    };
  }
  const output = element();
  const slider = {value:"640",min:"420",max:"860",step:"1",disabled:false,
    dispatchEvent(event) { assert.equal(event.type,"input"); inputEvents++; },
  };
  current = output;
  const control = {querySelector: selector => selector === ".slider-value" ? output : slider};
  const document = {querySelectorAll: () => [control],createElement: element};
  upgradeSliderControls(document);
  upgradeSliderControls(document);
  return {output,slider, get current() { return current; }, get inputEvents() { return inputEvents; }};
}

test("repeated slider edits commit or cancel once even when replacement fires blur", () => {
  const f = fixture();
  for (const [value,key,expected] of [["680","Enter","680"],["700","Escape","680"],["720","Enter","720"]]) {
    f.output.fire("click");
    const editor = f.current;
    assert.notEqual(editor,f.output);
    editor.value = value;
    editor.fire("keydown",{key});
    editor.fire("blur");
    assert.equal(f.current,f.output);
    assert.equal(f.slider.value,expected);
    assert.equal(f.output.textContent,expected);
  }
  assert.equal(f.inputEvents,2);
});

test("blur commits within bounds and a disabled slider cannot open an editor", () => {
  const f = fixture();
  f.output.fire("click");
  f.current.value = "999";
  f.current.fire("blur");
  assert.equal(f.slider.value,"860");
  assert.equal(f.current,f.output);
  assert.equal(f.inputEvents,1);
  f.slider.disabled = true;
  f.output.fire("click");
  assert.equal(f.current,f.output);
});
