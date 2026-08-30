import assert from "node:assert/strict";
import test from "node:test";

import {
  characterOptionGroup,
  characterOptionLabel,
  isValidCharacterId,
  normalizeColorText,
  uniqueReplyTones,
} from "../studio-model.js";

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
