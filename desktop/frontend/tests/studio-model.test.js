import assert from "node:assert/strict";
import test from "node:test";

import {
  characterOptionGroup,
  characterOptionLabel,
  hasUnsavedEditorChanges,
  isValidCharacterId,
  normalizeColorText,
  operationCancelState,
  runtimeReloadState,
  selectBootstrapCharacter,
  uniqueReplyTones,
  validateStudioResponse,
} from "../studio/studio-model.js";

test("normalizes valid colors and preserves the requested fallback", () => {
  assert.equal(normalizeColorText("A1B2C3", "#000000"), "#a1b2c3");
  assert.equal(normalizeColorText("not-a-color", "#123456"), "#123456");
});

test("separates published characters from workspace drafts", () => {
  assert.deepEqual(characterOptionGroup({ is_installed: true }), {
    id: "published",
    label: "已发布角色",
    sourceLabel: "已发布",
  });
  assert.equal(characterOptionGroup({ is_installed: false }).id, "workspace");
  assert.equal(characterOptionLabel({ id: "sakura", display_name: "Sakura" }), "Sakura");
  assert.equal(characterOptionLabel({ id: "draft" }), "draft");
});

test("selects the requested bootstrap character and falls back deterministically", () => {
  const characters = [{ id: "sakura" }, { id: "navi" }];
  assert.equal(selectBootstrapCharacter(characters, "navi"), "navi");
  assert.equal(selectBootstrapCharacter(characters, "missing"), "sakura");
  assert.equal(selectBootstrapCharacter([], "missing"), "");
});

test("keeps edits made while an autosave request is in flight dirty", () => {
  assert.equal(hasUnsavedEditorChanges("saved-a", "saved-a"), false);
  assert.equal(hasUnsavedEditorChanges("saved-a", "edited-b"), true);
});

test("normalizes cancellation and runtime reload states", () => {
  assert.equal(operationCancelState({ state: "finalizing" }), "finalizing");
  assert.equal(operationCancelState({ cancelled: true }), "cancelling");
  assert.equal(operationCancelState({ cancelled: false }), "finished");
  assert.equal(runtimeReloadState("ready"), "ready");
  assert.equal(runtimeReloadState("failed"), "failed");
  assert.equal(runtimeReloadState("unexpected"), "unknown");
});

test("deduplicates non-empty reply tones without changing order", () => {
  assert.deepEqual(uniqueReplyTones([
    { tone: " 温柔 " },
    { tone: "" },
    { tone: "开心" },
    { tone: "温柔" },
  ]), ["温柔", "开心"]);
});

test("accepts package-safe character ids and rejects traversal aliases", () => {
  assert.equal(isValidCharacterId("N.A.V.I."), true);
  assert.equal(isValidCharacterId("sakura-01"), true);
  assert.equal(isValidCharacterId("../sakura"), false);
  assert.equal(isValidCharacterId("."), false);
  assert.equal(isValidCharacterId(".."), false);
  assert.equal(isValidCharacterId(""), false);
});

test("accepts schema v1 responses and rejects leaked host paths", () => {
  assert.equal(validateStudioResponse({ schemaVersion: 1, characters: [] }).schemaVersion, 1);
  assert.throws(() => validateStudioResponse({ characters: [] }), /无效数据/);
  assert.throws(
    () => validateStudioResponse({ schemaVersion: 1, doc: { packageDir: "/private" } }),
    /不允许公开/,
  );
});
