import test from "node:test";
import assert from "node:assert/strict";
import {
  createStudioState, makeVisual, makeDoc, saveDraft, publishDoc, discardDraft, toSettingsCharacters,
} from "../prototypes/studio-demo/model.js";

test("multiple portrait sets stay isolated and reach settings only after saving the character", () => {
  const state = createStudioState();
  const original = structuredClone(state.docs.navi);
  const draft = structuredClone(original);
  const second = makeVisual("portrait", "第二套立绘", "second-portrait");
  second.expressions = { 默认: "portraits/second/default.png", 开心: "portraits/second/happy.png" };
  second.defaultPortrait = second.expressions.默认;
  draft.visuals.push(second);
  draft.defaultVisualId = second.id;
  saveDraft(state, draft);
  assert.equal(toSettingsCharacters(state)[0].visuals.length, 1);
  assert.deepEqual(state.docs.navi.visuals[0], original.visuals[0]);
  publishDoc(state, state.docs.navi);
  const preview = toSettingsCharacters(state);
  assert.deepEqual(preview[0].visuals.map(v => v.label), ["日常立绘", "第二套立绘"]);
  assert.equal(preview[0].defaultVisual, second.id);
  assert.equal(preview[1].visuals.length, 1);
  draft.visuals[1].expressions.开心 = "changed.png";
  assert.equal(state.published.navi.visuals[1].expressions.开心, "portraits/second/happy.png");
});

test("disabling a plugin preserves the model resource and discarding restores the saved package", () => {
  const state = createStudioState();
  const draft = structuredClone(state.docs.navi);
  draft.visuals.push({ ...makeVisual("live2d", "Live2D", "l2d"), modelFile: "models/navi.model3.json" });
  publishDoc(state, draft);
  state.plugins = ["portrait"];
  state.docs.navi.visuals[1].name = "未保存的名称";
  state.docs.navi.visuals.splice(0, 1);
  discardDraft(state, "navi");
  assert.equal(state.docs.navi.visuals.length, 2);
  assert.equal(state.docs.navi.visuals[1].name, "Live2D");
  assert.equal(toSettingsCharacters(state)[0].visuals[1].pluginId, "example.live2d");
});

test("new character drafts do not appear in settings until added to the character list", () => {
  const state = createStudioState();
  const doc = makeDoc("friend", "新伙伴", true);
  state.docs[doc.id] = doc;
  assert.equal(toSettingsCharacters(state).length, 2);
  publishDoc(state, doc);
  assert.equal(toSettingsCharacters(state).length, 3);
  const second = makeDoc("temporary", "临时草稿");
  state.docs[second.id] = second;
  discardDraft(state, second.id);
  assert.equal(state.docs.temporary, undefined);
  assert.equal(state.published.friend.displayName, "新伙伴");
});
