import assert from "node:assert/strict";
import test from "node:test";

import { isValidCharacterId, validateStudioResponse, visualValidationIssue } from "../studio/studio-model.js";

test("visual validation preserves resource and plugin field through the native error envelope", () => {
  const message = "形态「立绘」：标签无效|请修改。\n诊断详情";
  assert.deepEqual(visualValidationIssue(`VISUAL_RESOURCE_INVALID|character.studio|/visuals/resources/portrait.2/expressionRows/1/label|${message}`), {
    resourceId: "portrait.2", field: "/expressionRows/1/label", message,
  });
  assert.equal(visualValidationIssue("VISUAL_RESOURCE_INVALID|character.studio"), null);
  assert.equal(visualValidationIssue("VISUAL_RESOURCE_INVALID|character.studio|theme.primary|invalid"), null);
  assert.equal(visualValidationIssue("VISUAL_RESOURCE_INVALID|other|/visuals/resources/portrait|invalid"), null);
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
  assert.throws(() => validateStudioResponse({ characters: [] }));
  assert.throws(
    () => validateStudioResponse({ schemaVersion: 1, doc: { packageDir: "/private" } }),
  );
});
