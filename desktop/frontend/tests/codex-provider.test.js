import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { browserFixture, featureFixture, field, snapshot, settle } from "./fixtures/plugin-settings-fixture.js";
import { createConnectionEditor } from "../../../plugins/builtin/sakura_model_openai_compatible/frontend/connections.js";

const [accountSource, connectionsSource] = await Promise.all([
  readFile(new URL("../../../plugins/optional/sakura_codex/frontend/account.js", import.meta.url), "utf8"),
  readFile(new URL("../../../plugins/builtin/sakura_model_openai_compatible/frontend/connections.js", import.meta.url), "utf8"),
]);
const importSource = source => import("data:text/javascript;base64," + Buffer.from(source).toString("base64"));
const { mount } = await importSource(accountSource);
const models = [{ modelId: "first", label: "First", inputModalities: ["text", "image"] }, { modelId: "second", label: "Second" }];
const account = { loggedIn: true, email: "person@example.com", plan: "plus" };
const button = (root, text) => root.querySelectorAll("button").find(item => item.textContent.endsWith(text));

function fixture(action, initial = {}) {
  const browser = browserFixture();
  browser.window.crypto = { randomUUID: () => "current-request" };
  const values = { profiles: [{ profileId: "first-account", label: "OpenAI Official", createdAt: 1, account: { loggedIn: false }, models: [], ...initial }] };
  const calls = [], errors = [], copied = [];
  browser.window.navigator = { clipboard: { writeText: async text => copied.push(text) } };
  const lifetime = new AbortController();
  let editor;
  const component = mount({ ...browser, read: key => values[key], write: () => assert.fail("账号操作应直接保存"),
    action: async (name, args) => {
      calls.push({ name, args });
      const result = await action(name, args);
      Object.assign(values.profiles[0], result.values || {});
      return { values: structuredClone(values) };
    }, signal: lifetime.signal,
    createIcon: () => browser.document.createElement("span"), isCurrent: () => true, onError: error => errors.push(error), notify() {}, onCatalogChanged: () => editor?.updateContributions() });
  const panel = component.contributions().modelServices[0].element;
  browser.document.body.append(component.element, panel);
  return { ...browser, values, calls, errors, copied, component, panel, lifetime,
    button: text => button(panel, text),
    workbench() {
      const connections = [{ id: "existing", alias: "Existing", base_url: "https://example.test/v1", models: ["saved"] }];
      editor = createConnectionEditor({ ...browser, read: () => connections, write() {}, probe() {}, cancel: async () => {},
        createIcon: () => browser.document.createElement("span"), onError: error => errors.push(error), notify() {},
        modelServices: () => component.contributions().modelServices, modelServicePresets: () => component.contributions().modelServicePresets });
      panel.remove(); browser.document.body.append(editor.element); editor.update();
      return editor;
    },
  };
}

test("交替添加 API 和多个账号连接按添加顺序排列，登录相互独立且保留 API 草稿", async t => {
  // Node 用 data URL 执行浏览器的插件模块，其他设置逻辑保持原样。
  const NativeBlob = globalThis.Blob;
  globalThis.Blob = class { constructor(parts) { this.source = parts.join(""); } };
  t.after(() => { globalThis.Blob = NativeBlob; });
  t.mock.method(URL, "createObjectURL", blob => "data:text/javascript;base64," + Buffer.from(blob.source).toString("base64"));
  t.mock.method(URL, "revokeObjectURL", () => {});
  const data = snapshot();
  const template = data.plugins[0];
  const section = (id, source, fields, values) => ({
    sectionId: id, title: id, reasonCode: "READY",
    placement: { pageId: "host:providers", region: "content", order: id === "connections" ? 10 : 20, available: true },
    presentation: { component: "module", standalone: true, source }, fields, values, actions: [], collections: [],
  });
  const codexValues = { profiles: [], operationRequest: {} };
  data.plugins = [
    { ...template, pluginId: "sakura.model.openai_compatible", installId: "builtin",
      sections: [section("connections", connectionsSource, [field("connections", { type: "data", default: [] })],
        { connections: [{ id: "api", alias: "Existing", base_url: "https://example.test/v1", models: ["saved"] }] })] },
    { ...template, pluginId: "sakura.model.codex", installId: "codex",
      sections: [section("account", accountSource,
        [field("operationRequest", { type: "data", default: {} }), ...["profiles"].map(key => field(key, { type: "data", readonly: true }))],
        codexValues)] },
  ];
  const ui = featureFixture(async (command, args) => {
    if (command !== "settings_plugins_action") return structuredClone(data);
    if (args.actionId === "add") codexValues.profiles.push({ profileId: "codex-" + codexValues.profiles.length,
      label: codexValues.profiles.length ? "OpenAI Official 2" : "OpenAI Official", createdAt: args.values.operationRequest.createdAt,
      models: [], account: { loggedIn: false } });
    if (args.actionId === "start") Object.assign(codexValues.profiles.find(p => p.profileId === args.values.operationRequest.profileId), { account, models, operationResult: {
      requestId: args.values.operationRequest.requestId, state: "completed",
    } });
    return { values: structuredClone(codexValues) };
  });
  t.after(() => ui.feature.dispose());
  let sequence = 0; ui.window.crypto = { randomUUID: () => `id-${++sequence}` };
  t.mock.method(Date, "now", () => 1000);
  const page = ui.document.createElement("section"); page.id = "page-providers"; ui.document.body.append(page);
  ui.feature.initialize(structuredClone(data)); await settle(); await settle();
  const editor = ui.document.querySelector(".connection-editor");
  assert.ok(editor, ui.errors.map(String).join("\n"));
  const alias = editor.querySelector('[data-provider-field="alias"]');
  alias.value = "Unsaved"; await alias.fire("input");
  await button(editor, "添加模型服务").fire("click");
  const codexOption = button(ui.document.querySelector(".provider-add-dialog"), "OpenAI Official");
  assert.ok(codexOption, ui.errors.map(String).join("\n") || ui.document.querySelector(".provider-add-dialog").textContent);
  await codexOption.fire("click");
  await button(editor, "添加模型服务").fire("click");
  await button(ui.document.querySelector(".provider-add-dialog"), "Google 官方").fire("click");
  await button(editor, "添加模型服务").fire("click");
  await button(ui.document.querySelector(".provider-add-dialog"), "OpenAI Official").fire("click");
  const names = () => editor.querySelectorAll(".provider-card").map(card => card.querySelector("strong").textContent);
  assert.deepEqual(names(), ["Unsaved", "OpenAI Official", "Google 官方", "OpenAI Official 2"]);
  await editor.querySelectorAll(".provider-card")[1].fire("click");
  await settle();
  const detail = editor.querySelector(".admin-detail");
  await button(detail, "登录 ChatGPT").fire("click");
  assert.deepEqual(codexValues.profiles[1].models, []);
  assert.equal(codexValues.profiles[1].account.loggedIn, false);
  assert.deepEqual(names(), ["Unsaved", "OpenAI Official", "Google 官方", "OpenAI Official 2"]);
  assert.match(detail.textContent, /person@example.com/);
  assert.deepEqual(ui.feature.providerCatalog().find(p => p.serviceKey === "sakura.model.codex").profiles[0].models, models);
  await editor.querySelectorAll(".provider-card").find(card => card.textContent.includes("Unsaved")).fire("click");
  assert.equal(editor.querySelector('[data-provider-field="alias"]').value, "Unsaved");
  assert.equal(ui.feature.isDirty(), true);
  assert.deepEqual(ui.errors, []);
});

test("登录代码可以复制，登录后全部模型立即进入用途目录", async () => {
  const f = fixture(async name => {
    if (name === "start") return { values: { operationResult: { requestId: "current-request", state: "running", userCode: "ABCD-EFGH" } } };
    if (name === "status") return { values: { account, models, operationResult: { requestId: "current-request", state: "completed" } } };
    return {};
  });
  const click = f.button("登录 ChatGPT").fire("click");
  await settle();
  assert.equal(f.panel.querySelector('[aria-label="登录代码"]').textContent, "ABCD-EFGH");
  await f.button("复制代码").fire("click");
  assert.deepEqual(f.copied, ["ABCD-EFGH"]);
  assert.equal(f.panel.querySelector('[aria-label="复制代码"]').textContent, "已复制");
  assert.ok(f.calls.some(call => call.name === "openLogin"));
  await f.runTimers(250); await click;
  assert.match(f.panel.textContent, /person@example.com/);
  assert.deepEqual(f.component.contributions().modelCatalog[0].profiles[0].models, models);
  f.component.update();
  assert.deepEqual(f.component.contributions().modelCatalog[0].profiles[0].models, models);
  assert.deepEqual(f.errors, []);
  f.component.dispose();
});

test("同步失败保留登录，重试后更新完整目录", async () => {
  let attempt = 0;
  const f = fixture(async name => {
    if (name !== "start") return {};
    attempt++;
    return { values: { account, models: attempt === 1 ? [] : models, operationResult: {
      requestId: "current-request", state: attempt === 1 ? "failed" : "completed", message: attempt === 1 ? "目录暂不可用" : "完成",
    } } };
  });
  await f.button("登录 ChatGPT").fire("click");
  assert.equal(f.button("重试获取模型").hidden, false);
  assert.match(f.panel.textContent, /person@example.com/);
  await f.button("重试获取模型").fire("click");
  assert.deepEqual(f.component.contributions().modelCatalog[0].profiles[0].models, models);
  assert.equal(f.calls.filter(call => call.name === "start").at(-1).args.operationRequest.operation, "sync_models");
  assert.equal(f.errors[0].message, "目录暂不可用");
  f.component.dispose();
});

for (const leave of ["switch", "close"]) {
  test(leave === "switch" ? "切换服务会取消登录，迟到结果不回填" : "关闭设置会取消登录，迟到结果不回填", async () => {
    let resolve;
    const deferred = new Promise(done => { resolve = done; });
    const f = fixture(async name => name === "start" ? deferred : {});
    const editor = f.workbench();
    await editor.element.querySelectorAll(".provider-card").find(card => card.textContent.includes("OpenAI Official")).fire("click");
    const click = f.button("登录 ChatGPT").fire("click"); await settle();
    if (leave === "switch") {
      await editor.element.querySelectorAll(".provider-card").find(card => card.textContent.includes("Existing")).fire("click");
    } else {
      f.lifetime.abort(); f.component.dispose(); editor.dispose();
    }
    assert.ok(f.calls.some(call => call.name === "cancel" && call.args.operationRequest.requestId === "current-request"));
    resolve({ values: { account, models, operationResult: { requestId: "current-request", state: "completed" } } });
    await click;
    assert.deepEqual(f.component.contributions().modelCatalog[0].profiles[0].models, []);
    assert.deepEqual(f.errors, []);
    editor.dispose(); f.component.dispose();
  });
}
