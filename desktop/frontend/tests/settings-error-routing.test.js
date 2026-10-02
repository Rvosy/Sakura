import assert from "node:assert/strict";
import test from "node:test";
import { browserFixture, settle } from "./fixtures/plugin-settings-fixture.js";
import { createConnectionEditor } from "../settings/connection-editor.js";
import { createCharacterVisualSettings } from "../settings/character-visual-settings.js";
import { openDownloadSources } from "../settings/download-source-settings.js";

for (const [buttonText, operation] of [["获取模型列表", "list_models"], ["测试连接", "test_connection"]]) {
  test(`model ${operation} allows a connection without an API key`, async () => {
    const { document, window } = browserFixture();
    const calls = [], errors = [];
    const editor = createConnectionEditor({ document, window,
      read: () => [{ id: "local", base_url: "http://192.168.1.20:8000/v1", models: ["local-model"] }],
      write() {}, cancel() {}, notify() {},
      probe: async (...args) => { calls.push(args); return { models: [] }; },
      onError: (error) => { if (error) errors.push(error); },
    });
    document.body.append(editor.element); editor.update();
    await editor.element.querySelectorAll("button").find(button => button.textContent === buttonText).fire("click");
    assert.equal(calls.length, 1);
    assert.equal(calls[0][0], operation);
    assert.deepEqual(calls[0][1].credential, { action: "keep", value: "" });
    assert.deepEqual(errors, []);
    editor.dispose();
  });
}

test("model probe forwards the original error and keeps its diagnostics out of the connection form", async () => {
  const { document, window } = browserFixture();
  const failure = new Error("MODEL_CONNECTION_FAILED", { cause: new Error("probe transport diagnostic") });
  const reports = [];
  const editor = createConnectionEditor({ document, window,
    read: () => [{ id: "model-service", alias: "Fixture", base_url: "https://example.test/v1", configured: true, models: [] }],
    write() {}, cancel() {}, notify() {}, probe: async () => { throw failure; },
    onError: (error, message) => { if (error) reports.push({ error, message }); },
  });
  document.body.append(editor.element); editor.update();
  await editor.element.querySelectorAll("button").find(button => button.textContent === "获取模型列表").fire("click");
  assert.equal(reports.length, 1);
  assert.equal(reports[0].error, failure);
  assert.ok(reports[0].message);
  assert.doesNotMatch(editor.element.textContent, /MODEL_CONNECTION_FAILED|probe transport diagnostic/);
  editor.dispose();
});

for (const stage of ["read", "open"]) {
  test(`visual settings ${stage} failure keeps a brief status and forwards the original exception`, async () => {
    const { document } = browserFixture();
    for (const id of ["visualSelect", "visualStatus", "visualStatusText", "visualPluginAction", "visualConfigure"]) {
      const element = document.createElement(id === "visualSelect" ? "select" : "button"); element.id = id; document.body.append(element);
    }
    const failure = new Error("VISUAL_FIXTURE_FAILED", { cause: new Error("visual diagnostic") });
    const reports = [];
    const feature = createCharacterVisualSettings({ document, refreshSelect() {}, onDirty() {}, openPlugin() {},
      onError: (error) => reports.push(error),
      invoke: async (command) => {
        if (stage === "read" || command === "open_character_studio") throw failure;
        return { characterId: "alpha", defaultResourceId: "portrait", resources: [{ id: "portrait", name: "Portrait", reasonCode: "READY" }] };
      },
    });
    await feature.refresh("alpha");
    if (stage === "open") await document.getElementById("visualConfigure").fire("click");
    assert.deepEqual(reports, [failure]);
    assert.ok(document.getElementById("visualStatusText").textContent);
    assert.doesNotMatch(document.body.textContent, /VISUAL_FIXTURE_FAILED|visual diagnostic/);
    feature.dispose();
  });
}

test("download source save failure leaves the editor open and forwards the original error", async () => {
  const { document } = browserFixture();
  const failure = new Error("DOWNLOAD_SOURCE_FIXTURE_FAILED");
  const reports = [];
  await openDownloadSources({ document,
    invoke: async (command) => { if (command.endsWith("_get")) return []; throw failure; },
    notify: (...args) => reports.push(args),
  });
  const dialog = document.getElementById("download-sources-dialog");
  const save = dialog.querySelectorAll("button").find(button => button.textContent === "保存");
  await save.onclick();
  assert.deepEqual(reports, [[failure, "error"]]);
  assert.equal(dialog.open, true);
  assert.equal(save.disabled, false);
  assert.doesNotMatch(dialog.textContent, /DOWNLOAD_SOURCE_FIXTURE_FAILED/);
});

test("legacy scan failure is reported through the shared dialog and releases the import operation", async () => {
  const { document } = browserFixture();
  const originalDocument = globalThis.document;
  const originalWindow = globalThis.window;
  const createElement = document.createElement;
  document.createElement = (tag) => {
    const element = createElement(tag);
    if (tag !== "dialog") return element;
    // Stand in only for parsing the dialog's static HTML; production owns every event and state transition.
    for (const name of ["source", "feedback", "results", "choose", "start", "cancel", "empty", "completed", "summary", "receipt"]) {
      const control = createElement("div"); control.setAttribute(`data-${name}`, ""); element.append(control);
    }
    const close = createElement("button"); close.className = "legacy-import-close";
    element.append(close, createElement("img"));
    return element;
  };
  globalThis.document = document;
  globalThis.window = { addEventListener() {} };
  const failure = new Error("LEGACY_SCAN_FIXTURE_FAILED", { cause: new Error("scan diagnostic") });
  const reports = [], operations = [];
  let feature;
  try {
    const { openLegacyDataImport } = await import("../settings/legacy-data-import.js");
    feature = openLegacyDataImport({ client: { legacyRoleDataImportChoose: async () => { throw failure; } },
      setBusy: (value) => operations.push(value), onError: (error) => reports.push(error),
    });
    feature.dialog.querySelector("[data-choose]").onclick();
    await settle();
    assert.deepEqual(reports, [failure]);
    assert.deepEqual(operations, [true, false]);
    assert.equal(feature.dialog.querySelector("[data-start]").disabled, true);
    assert.doesNotMatch(feature.dialog.textContent, /LEGACY_SCAN_FIXTURE_FAILED|scan diagnostic/);
  } finally { feature?.close(); globalThis.document = originalDocument; globalThis.window = originalWindow; }
});
