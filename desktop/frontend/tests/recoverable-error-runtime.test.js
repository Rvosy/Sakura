import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("../app.js", import.meta.url), "utf8");
const callbacks = source.slice(source.indexOf("function showRecoverableError("),
  source.indexOf("\nfunction isBubbleScrollbarHit("));

function fixture(t) {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const element = { hidden: true, textContent: "", dataset: {} };
  const context = vm.createContext({
    presentationError: element,
    recoverableErrorTimer: null,
    setTimeout,
    clearTimeout,
  });
  vm.runInContext(callbacks, context);
  return { element, show: context.showRecoverableError, clear: context.clearRecoverableError };
}

test("every bottom warning clears after five seconds, including ASR controls", t => {
  const f = fixture(t);
  f.show("语音识别失败");
  // The ASR callback replaces the text with a message and action buttons.
  f.element.textContent = "语音识别失败重试打开设置";
  f.element.dataset.asrError = "true";
  t.mock.timers.tick(4999);
  assert.equal(f.element.hidden, false);
  t.mock.timers.tick(1);
  assert.equal(f.element.hidden, true);
  assert.equal(f.element.textContent, "");
  assert.equal(f.element.dataset.asrError, undefined);
});

test("a replacement warning gets five seconds without the old timer clearing it", t => {
  const f = fixture(t);
  f.show("角色加载失败");
  t.mock.timers.tick(4000);
  f.show("设置暂时无法打开");
  t.mock.timers.tick(1000);
  assert.equal(f.element.hidden, false);
  assert.equal(f.element.textContent, "设置暂时无法打开");
  t.mock.timers.tick(4000);
  assert.equal(f.element.hidden, true);
});

test("repeated identical warnings do not extend the visible interval", t => {
  const f = fixture(t);
  f.show("命中区域更新失败");
  t.mock.timers.tick(4000);
  f.show("命中区域更新失败");
  t.mock.timers.tick(1000);
  assert.equal(f.element.hidden, true);
});

test("early recovery cancels the timer before a later warning is shown", t => {
  const f = fixture(t);
  f.show("旧错误");
  t.mock.timers.tick(1000);
  f.clear();
  assert.equal(f.element.hidden, true);
  assert.equal(f.element.textContent, "");
  t.mock.timers.tick(1000);
  f.show("新错误");
  t.mock.timers.tick(3000);
  assert.equal(f.element.hidden, false);
  t.mock.timers.tick(2000);
  assert.equal(f.element.hidden, true);
});
