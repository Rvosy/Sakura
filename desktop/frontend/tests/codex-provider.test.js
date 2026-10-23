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
    createIcon: () => browser.document.createElement("span"), enhanceSelect() {}, refreshSelect() {}, closeSelects() {},
    isCurrent: () => true, onError: error => errors.push(error), notify() {}, onCatalogChanged: () => editor?.updateContributions() });
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
  const f = fixture(async (name, args) => {
    if (name === "start" && args.operationRequest.operation === "usage") return { values: {
      usage: { windows: [], updatedAt: 1 }, operationResult: { requestId: "current-request", state: "completed" },
    } };
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
  assert.equal(f.button("刷新模型").hidden, false);
  assert.match(f.panel.textContent, /person@example.com/);
  await f.button("刷新模型").fire("click");
  assert.deepEqual(f.component.contributions().modelCatalog[0].profiles[0].models, models);
  assert.equal(f.calls.filter(call => call.name === "start").at(-1).args.operationRequest.operation, "sync_models");
  assert.equal(f.errors[0].message, "目录暂不可用");
  f.component.dispose();
});

test("打开账号获取实际额度，刷新失败保留上次数据且可继续测试模型", async () => {
  const usage = { updatedAt: 1800000000, windows: [
    { label: "", windowSeconds: 18000, usedPercent: 25, resetsAt: 1800003600 },
    { label: "", windowSeconds: 604800, usedPercent: 0, resetsAt: 1800604800 },
    { label: "额外模型", windowSeconds: 3600, usedPercent: null, resetsAt: null },
  ] };
  let available = true;
  const f = fixture(async (name, args) => {
    if (name !== "start") return {};
    assert.equal(args.operationRequest.operation, "usage");
    return { values: { ...(available ? { usage } : {}), operationResult: {
      requestId: "current-request", operation: "usage", state: available ? "completed" : "failed",
      message: available ? "额度已更新" : "HTTP 503: quota unavailable",
    } } };
  }, { account, models });
  await f.component.contributions().modelServices[0].select();
  const card = f.panel.querySelector('[aria-label="账号额度"]');
  assert.deepEqual(card.querySelectorAll("progress").map(item => item.value), [75, 100]);
  assert.match(card.textContent, /5 小时/);
  assert.match(card.textContent, /7 天/);
  assert.match(card.textContent, /未提供用量/);
  const lastReading = card.querySelector(".codex-quota-windows").textContent;
  const lastUpdate = card.querySelector(".codex-usage-updated").textContent;
  available = false;
  await f.button("刷新额度").fire("click");
  assert.equal(card.querySelector(".codex-quota-windows").textContent, lastReading);
  assert.equal(card.querySelector(".codex-usage-updated").textContent, lastUpdate);
  assert.match(f.panel.textContent, /quota unavailable/);
  await f.button("复制详情").fire("click");
  assert.match(f.copied[0], /HTTP 503: quota unavailable/);
  assert.equal(f.button("测试模型").disabled, false);
  assert.equal(f.button("刷新模型").disabled, false);
  assert.deepEqual(f.errors, []);
  f.component.dispose();
});

for (const operation of ["sync_models", "test_connection", "logout"]) {
  test(`额度查询未返回时可直接执行 ${operation}，旧查询不回填`, async t => {
    let finishUsage, finishCancel, usageRequest;
    const usageResponse = new Promise(resolve => { finishUsage = resolve; });
    const cancelResponse = new Promise(resolve => { finishCancel = resolve; });
    const refreshed = [...models, { modelId: "new-model", label: "New" }];
    const f = fixture(async (name, args) => {
      const request = args.operationRequest;
      if (name === "cancel") return cancelResponse;
      if (name === "logout") return { values: { account: { loggedIn: false }, usage: null, operationResult: {} } };
      if (name !== "start") return {};
      if (request.operation === "usage") { usageRequest = request; return usageResponse; }
      return { values: { ...(request.operation === "sync_models" ? { models: refreshed } : {}), operationResult: {
        ...request, state: "completed", reply: request.operation === "test_connection" ? "OK" : undefined,
      } } };
    }, { account, models });
    let sequence = 0; f.window.crypto.randomUUID = () => `request-${++sequence}`;
    const selecting = f.component.contributions().modelServices[0].select(); await settle();
    t.after(async () => {
      finishCancel({}); finishUsage({ values: { operationResult: { ...usageRequest, state: "cancelled" } } });
      await selecting; f.component.dispose();
    });
    const label = { sync_models: "刷新模型", test_connection: "测试模型", logout: "退出登录" }[operation];
    assert.equal(f.button(label).disabled, false);
    const clicking = f.button(label).fire("click"); await settle();
    assert.ok(f.calls.some(call => call.name === "cancel" && call.args.operationRequest.requestId === usageRequest.requestId));
    assert.ok(f.calls.some(call => operation === "logout" ? call.name === "logout"
      : call.name === "start" && call.args.operationRequest.operation === operation));
    finishCancel({}); await clicking;
    finishUsage({ values: { usage: { windows: [], updatedAt: 2 },
      operationResult: { ...usageRequest, state: "completed" } } });
    await selecting;
    assert.equal(f.panel.querySelector(".codex-usage-updated").textContent, "");
    if (operation === "logout") assert.equal(f.component.contributions().modelServices[0].configured, false);
    if (operation === "sync_models") assert.deepEqual(f.component.contributions().modelCatalog[0].profiles[0].models, refreshed);
    if (operation === "test_connection") assert.equal(f.panel.querySelector(".codex-test-reply").textContent, "OK");
    assert.deepEqual(f.errors, []);
  });
}

for (const operation of ["usage", "test_connection"]) {
  test(`${operation} 轮询和完成时保留服务列表的键盘焦点`, async t => {
    let completed = false;
    const f = fixture(async (name, args) => {
      const request = args.operationRequest;
      if (!["start", "status"].includes(name) || !request.requestId) return {};
      return { values: { operationResult: { ...request, state: completed ? "completed" : "running",
        ...(completed && operation === "test_connection" ? { reply: "OK" } : {}),
      }, ...(completed && operation === "usage" ? { usage: { windows: [], updatedAt: 2 } } : {}) } };
    }, { account, models, usage: { windows: [], updatedAt: 1 } });
    const editor = f.workbench();
    t.after(async () => { await f.component.cancel(); await f.runTimers(250); editor.dispose(); f.component.dispose(); });
    await editor.element.querySelectorAll(".provider-card").find(card => card.textContent.includes("OpenAI Official")).fire("click");
    const clicking = f.button(operation === "usage" ? "刷新额度" : "测试模型").fire("click"); await settle();
    const focused = editor.element.querySelectorAll(".provider-card")[0]; focused.focus();
    await f.runTimers(250);
    assert.equal(f.document.activeElement, focused);
    assert.ok(f.document.body.contains(f.document.activeElement));
    completed = true;
    await f.runTimers(250); await clicking;
    assert.equal(f.document.activeElement, focused);
    assert.ok(f.document.body.contains(f.document.activeElement));
    if (operation === "test_connection") assert.equal(f.panel.querySelector(".codex-test-reply").textContent, "OK");
    assert.deepEqual(f.errors, []);
  });
}

test("同步成功后仍可刷新目录，测试指定模型并显示回复和耗时", async () => {
  const refreshed = [...models, { modelId: "new-model", label: "New" }];
  const f = fixture(async (name, args) => {
    if (name !== "start") return {};
    const request = args.operationRequest;
    return { values: { ...(request.operation === "sync_models" ? { models: refreshed } : {}), operationResult: {
      requestId: request.requestId, operation: request.operation, state: "completed",
      ...(request.operation === "test_connection" ? { modelId: request.modelId, reply: "OK", elapsedMs: 2500, message: "请求完成" } : {}),
    } } };
  }, { account, models });
  assert.equal(f.button("刷新模型").hidden, false);
  await f.button("刷新模型").fire("click");
  assert.deepEqual(f.component.contributions().modelCatalog[0].profiles[0].models, refreshed);
  const model = f.panel.querySelector('[aria-label="测试使用的模型"]');
  model.value = "new-model"; await model.fire("change");
  await f.button("测试模型").fire("click");
  assert.equal(f.calls.at(-1).args.operationRequest.modelId, "new-model");
  assert.match(f.panel.textContent, /new-model.*2\.50 秒/);
  assert.equal(f.panel.querySelector(".codex-test-reply").textContent, "OK");
  await f.button("复制详情").fire("click");
  assert.match(f.copied[0], /new-model[\s\S]*OK/);
  model.value = "first"; await model.fire("change");
  assert.equal(f.panel.querySelector(".codex-test-reply").hidden, true);
  assert.deepEqual(f.component.contributions().modelCatalog[0].profiles[0].models, refreshed);
  assert.deepEqual(f.errors, []);
  f.component.dispose();
});

test("测试等待中切换模型会取消请求，迟到结果不覆盖新模型的回复", async () => {
  let resolve, finishCancel;
  const response = new Promise(done => { resolve = done; });
  const cancelled = new Promise(done => { finishCancel = done; });
  let started = false;
  const f = fixture(async (name, args) => {
    if (name === "cancel") return cancelled;
    if (name !== "start") return {};
    if (!started) { started = true; return response; }
    return { values: { operationResult: { requestId: args.operationRequest.requestId,
      operation: "test_connection", state: "completed", modelId: "second", reply: "新模型回复" } } };
  }, { account, models });
  const click = f.button("测试模型").fire("click"); await settle();
  assert.equal(f.button("取消测试").disabled, false);
  const model = f.panel.querySelector('[aria-label="测试使用的模型"]');
  model.value = "second"; const changed = model.fire("change"); await settle();
  assert.ok(f.calls.some(call => call.name === "cancel"));
  await f.button("测试模型").fire("click");
  resolve({ values: { operationResult: { requestId: "current-request", operation: "test_connection", state: "completed", modelId: "first", reply: "旧模型回复" } } });
  await click;
  finishCancel({}); await changed;
  assert.equal(model.value, "second");
  assert.equal(f.panel.querySelector(".codex-test-reply").textContent, "新模型回复");
  assert.deepEqual(f.errors, []);
  f.component.dispose();
});

for (const leave of ["switch", "close"]) for (const operation of ["login", "usage", "test_connection"]) {
  test(`${leave === "switch" ? "切换服务" : "关闭设置"}会取消 ${operation}，迟到结果不回填`, async () => {
    let resolve;
    const deferred = new Promise(done => { resolve = done; });
    const previousUsage = { windows: [], updatedAt: 1 };
    const f = fixture(async name => name === "start" ? deferred : {}, operation === "login" ? {} : { account, models, usage: previousUsage });
    const previousReading = f.panel.querySelector('.codex-usage-updated').textContent;
    const editor = f.workbench();
    await editor.element.querySelectorAll(".provider-card").find(card => card.textContent.includes("OpenAI Official")).fire("click");
    const click = f.button({ login: "登录 ChatGPT", usage: "刷新额度", test_connection: "测试模型" }[operation]).fire("click"); await settle();
    if (leave === "switch") {
      await editor.element.querySelectorAll(".provider-card").find(card => card.textContent.includes("Existing")).fire("click");
    } else {
      f.lifetime.abort(); f.component.dispose(); editor.dispose();
    }
    assert.ok(f.calls.some(call => call.name === "cancel" && call.args.operationRequest.requestId === "current-request"));
    resolve({ values: { account, models, usage: { windows: [], updatedAt: 2 },
      operationResult: { requestId: "current-request", operation, state: "completed", reply: "late reply" } } });
    await click;
    assert.deepEqual(f.component.contributions().modelCatalog[0].profiles[0].models, operation === "login" ? [] : models);
    assert.equal(f.panel.querySelector(".codex-test-reply").textContent, "");
    if (operation !== "login") assert.equal(f.panel.querySelector('.codex-usage-updated').textContent, previousReading);
    assert.deepEqual(f.errors, []);
    editor.dispose(); f.component.dispose();
  });
}
