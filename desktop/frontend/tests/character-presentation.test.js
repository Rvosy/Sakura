import assert from "node:assert/strict";
import test from "node:test";

import {
  loadCurrentCharacterPresentation,
  portraitSequence,
  validateCharacterPresentation,
} from "../pet/character-presentation.js";

function sample(overrides = {}) {
  const keys = ["__default__", "开心", "思考"];
  const resourceIds = Object.fromEntries(keys.map((key, index) => [key, `character-v1-53616b757261-portrait-${index + 10}`]));
  const resourceUrls = Object.fromEntries(keys.map((key, index) => [key, `http://sakura-character.localhost/v1/67656e2d31/character-v1-53616b757261-portrait-${index + 10}`]));
  const metadata = Object.fromEntries(keys.map((key) => [key, { width: 2096, height: 1846, byteLength: 2048 }]));
  return {
    schemaVersion: 1,
    generationId: "gen-1",
    characterId: "Sakura",
    displayName: "夜乃桜",
    initialMessage: "你好。",
    themeTokens: { primary: "#D55B91" },
    defaultPortraitKey: "__default__",
    portraitKeys: keys,
    portraitResourceIds: resourceIds,
    portraitResourceUrls: resourceUrls,
    portraitMetadata: metadata,
    ...overrides,
  };
}

test("a controlled product DTO keeps identity, theme, expressions, URLs, and metadata", () => {
  const presentation = validateCharacterPresentation(sample());
  assert.equal(presentation.displayName, "夜乃桜");
  assert.equal(presentation.themeTokens.primary, "#d55b91");
  assert.deepEqual(portraitSequence(presentation), {
    default: "__default__",
    thinking: "思考",
    positive: "开心",
    concerned: "开心",
    multi: ["开心", "思考"],
  });
  assert.equal(JSON.stringify(presentation).includes("characters/"), false);
  assert.equal(JSON.stringify(presentation).includes("D:\\"), false);
});

test("bare paths, traversal URLs, remote origins, and malformed mappings fail closed", () => {
  for (const url of [
    "D:/Project/sakura/characters/Sakura/portrait/A020.png",
    "file:///D:/Project/sakura/characters/Sakura/portrait/A020.png",
    "http://sakura-character.localhost/v1/67656e2d31/../secret",
    "https://example.com/portrait.png",
  ]) {
    const value = sample();
    value.portraitResourceUrls.__default__ = url;
    assert.throws(() => validateCharacterPresentation(value), /RESOURCE_URL/);
  }
  const missing = sample();
  delete missing.portraitMetadata["开心"];
  assert.throws(() => validateCharacterPresentation(missing), /MAPPING/);
});

test("invalid character IDs, duplicate keys, and invalid metadata fail closed", () => {
  assert.throws(() => validateCharacterPresentation(sample({ characterId: "../Sakura" })), /INVALID/);
  assert.throws(() => validateCharacterPresentation(sample({ portraitKeys: ["__default__", "__default__", "思考"] })), /PORTRAITS/);
  const dimensions = sample();
  dimensions.portraitMetadata.__default__.width = 0;
  assert.throws(() => validateCharacterPresentation(dimensions), /METADATA/);
});

test("not-ready publication retries but structural failures are immediate", async () => {
  let attempts = 0;
  const loaded = await loadCurrentCharacterPresentation({
    invoke: async () => {
      attempts += 1;
      if (attempts < 3) throw new Error("CHARACTER_PRESENTATION_NOT_READY");
      return sample();
    },
    attempts: 3,
    delayMs: 0,
    setTimer: (callback) => { callback(); return 1; },
  });
  assert.equal(attempts, 3);
  assert.equal(loaded.characterId, "Sakura");

  await assert.rejects(
    loadCurrentCharacterPresentation({ invoke: async () => sample({ characterId: "../escape" }), attempts: 9 }),
    /INVALID/,
  );
});
