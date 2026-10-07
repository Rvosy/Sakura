import assert from "node:assert/strict";
import test from "node:test";
import { createPluginModule } from "../settings/plugin-module.js";
import { browserFixture } from "./fixtures/plugin-settings-fixture.js";

const importSource = source => import(`data:text/javascript,${encodeURIComponent(source)}`);

test("plugin modules contribute their own controls and keep draft validation and cleanup", async () => {
  const { document } = browserFixture();
  let value = "saved", cancelled = 0, released = 0;
  const module = createPluginModule({ document, importSource, onError: assert.fail,
    context: { document, read: () => value, write: next => { value = next; },
      cancelled: () => cancelled++, released: () => released++ },
    source: `export function mount(c) {
      const input=c.document.createElement('input');
      input.addEventListener('input',()=>c.write(input.value));
      return {element:input,update(){input.value=c.read()},validate(){if(!c.read())throw new Error('required')},
        contributions:()=>({recordIds:['existing-id']}),cancel(){c.cancelled()},dispose(){c.released()}};
    }`,
  });
  await module.ready;
  const input = module.element.children[0];
  assert.equal(input.value, "saved");
  input.value = "edited"; await input.fire("input");
  assert.equal(value, "edited");
  assert.deepEqual(module.contributions(), { recordIds: ["existing-id"] });
  value = ""; assert.throws(() => module.validate(), /required/);
  await module.cancel(); assert.equal(cancelled, 1);
  module.dispose(); module.dispose(); assert.equal(released, 1);
});

test("a settings module finishing after its dialog closes is disposed without attachment", async () => {
  const { document } = browserFixture();
  let complete, signal, released = 0;
  const module = createPluginModule({ document, source: "delayed", onError: assert.fail,
    context: {}, importSource: async () => ({ mount: context => {
      signal = context.signal; return new Promise(resolve => { complete = resolve; });
    } }),
  });
  await new Promise(resolve => setImmediate(resolve));
  module.dispose(); assert.equal(signal.aborted, true);
  complete({ element: document.createElement("input"), dispose: () => released++ });
  await module.ready;
  assert.equal(module.element.children.length, 0); assert.equal(released, 1);
});

test("module failures preserve the original error and prevent an apparently successful save", async () => {
  const { document } = browserFixture();
  const error = new Error("MODULE_FAILED", { cause: new Error("original cause") });
  const failures = [];
  const module = createPluginModule({ document, source: "broken", context: {},
    onError: value => failures.push(value), importSource: async () => { throw error; } });
  await module.ready;
  assert.deepEqual(failures, [error]);
  assert.throws(() => module.validate(), candidate => candidate === error);
  assert.equal(module.element.textContent, "插件设置加载失败。");
  module.dispose();
});
