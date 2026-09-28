import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import { visualValidationIssue } from "../studio/studio-model.js";

const source = await readFile(new URL("../studio/studio.js", import.meta.url), "utf8");
const feedback = source.slice(source.indexOf("function setError("), source.indexOf("\nasync function hostCall("));
const runBusy = source.slice(source.indexOf("async function runBusy("), source.indexOf("\nfunction refreshControls("));
const validateExpressionInputs = source.slice(source.indexOf("function validateExpressionInputs("), source.indexOf("\nfunction validateVoiceInputs("));

function fixture() {
  const dialogs = [], diagnostics = [], controls = [], visualActions = [];
  const resources = [{ id: "portrait.1", name: "日常立绘" }, { id: "portrait.2", name: "第二套立绘" }];
  const context = vm.createContext({
    fields: { errorText: { textContent: "" } },
    errorDialog: { show: (value) => { dialogs.push(value); visualActions.push({ action: "dialog", busy: context.busy }); } },
    runtimeDiagnostics: { reportError: (error) => diagnostics.push(error) },
    refreshControls: () => controls.push(context.busy),
    visualValidationIssue,
    visualReferences: () => ({ resources }),
    visualName: (resource) => resource.name,
    selectedVisualId: "portrait.2",
    switchPage: (page) => visualActions.push({ action: "page", page }),
    renderVisualResources: async ({ preferredId, flush }) => {
      visualActions.push({ action: "resource", preferredId, flush });
      context.selectedVisualId = preferredId;
    },
    visualEditor: {
      validate: () => true,
      focusField: (field) => visualActions.push({ action: "focus", field, busy: context.busy }),
    },
    busy: false,
  });
  vm.runInContext(`${feedback}\n${runBusy}\n${validateExpressionInputs}`, context);
  return { context, dialogs, diagnostics, controls, visualActions };
}

test("studio operation failure keeps diagnostics in the dialog and releases busy controls", async () => {
  const { context, dialogs, diagnostics, controls } = fixture();
  const error = new Error("STUDIO_SAVE_FAILED\nTraceback: fixture failure");
  error.diagnostics = { diagnostic: "underlying write failure" };
  await context.runBusy(async () => { throw error; });
  assert.equal(context.fields.errorText.textContent, "操作失败");
  assert.equal(dialogs.length, 1);
  assert.equal(dialogs[0].error, error);
  assert.equal(diagnostics[0], error);
  assert.equal(context.busy, false);
  assert.deepEqual(controls, [true, false]);
});

test("studio backend visual validation selects and focuses the invalid resource before opening its dialog", async () => {
  const { context, dialogs, visualActions } = fixture();
  const error = new Error("VISUAL_RESOURCE_INVALID|character.studio|/visuals/resources/portrait.1/expressionRows/1/label|第 2 张立绘的表情标签重复");
  error.diagnostics = { diagnostic: "fixture plugin validation" };
  await context.runBusy(async () => { throw error; });
  assert.equal(context.selectedVisualId, "portrait.1");
  assert.deepEqual(visualActions, [
    { action: "page", page: "portrait" },
    { action: "resource", preferredId: "portrait.1", flush: false },
    { action: "focus", field: "/expressionRows/1/label", busy: false },
    { action: "dialog", busy: false },
  ]);
  assert.equal(dialogs[0].error, error);
  assert.equal(dialogs[0].title, "形态「日常立绘」校验失败");
  assert.equal(context.fields.errorText.textContent, dialogs[0].title);
});

test("studio validation for a missing resource still shows the original failure without changing selection", async () => {
  const { context, dialogs, visualActions } = fixture();
  const error = "VISUAL_RESOURCE_INVALID|character.studio|/visuals/resources/deleted/expressionRows/1/label|表情标签无效";
  await context.runBusy(async () => { throw error; });
  assert.equal(context.selectedVisualId, "portrait.2");
  assert.deepEqual(visualActions, [{ action: "dialog", busy: false }]);
  assert.equal(dialogs[0].error, error);
});

test("studio current visual validation focuses the plugin field and preserves its original error in the dialog", () => {
  const { context, dialogs, visualActions } = fixture();
  const error = new Error("第 2 张立绘的表情标签重复");
  error.field = "/expressionRows/1/label";
  context.visualEditor.validate = () => { throw error; };
  assert.equal(context.validateExpressionInputs(), false);
  assert.deepEqual(visualActions, [
    { action: "page", page: "portrait" },
    { action: "focus", field: error.field, busy: false },
    { action: "dialog", busy: false },
  ]);
  assert.equal(dialogs[0].error, error);
  assert.equal(dialogs[0].title, "形态「第二套立绘」校验失败");
});

test("studio error notifications preserve structured failures", () => {
  const { context, dialogs } = fixture();
  const error = { code: "PLUGIN_UNAVAILABLE", message: "插件不可用", cause: new Error("fixture cause") };
  context.notify(error, "error");
  assert.equal(dialogs.length, 1);
  assert.equal(dialogs[0].error, error);
  assert.equal(context.fields.errorText.textContent, "操作失败");
});

test("studio local field validation remains inline without opening an error dialog", () => {
  const { context, dialogs } = fixture();
  context.setError("请输入角色名称。");
  assert.equal(context.fields.errorText.textContent, "请输入角色名称。");
  context.setError("");
  assert.equal(context.fields.errorText.textContent, "");
  assert.equal(dialogs.length, 0);
});
