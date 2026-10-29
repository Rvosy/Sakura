import assert from "node:assert/strict";
import test from "node:test";

import { validateComposerToolsSnapshot } from "../chat/composer-tool-dock.js";

function snapshot(tools = []) {
  return { schemaVersion: 1, coreGenerationId: "generation-a", tools };
}

function tool(overrides = {}) {
  return {
    id: "com.example.tools:browser",
    pluginId: "com.example.tools",
    toolId: "browser",
    label: "浏览器",
    description: "打开受控浏览器",
    icon: "globe",
    order: 20,
    ...overrides,
  };
}

test("composer tool snapshots reject markup icons, duplicate ids, and private fields", () => {
  assert.throws(
    () => validateComposerToolsSnapshot(snapshot([tool({ icon: "<svg>" })])),
    /COMPOSER_TOOLS_RESPONSE_INVALID/,
  );
  assert.throws(
    () => validateComposerToolsSnapshot(snapshot([tool(), tool()])),
    /COMPOSER_TOOLS_RESPONSE_INVALID/,
  );
  assert.throws(
    () => validateComposerToolsSnapshot(snapshot([tool({ callbackHandle: "private" })])),
    /COMPOSER_TOOLS_RESPONSE_INVALID/,
  );
});
