import assert from "node:assert/strict";
import test from "node:test";

import { constrainedPortraitScale, validateAppearancePublication } from "../pet/appearance.js";

const themeTokens = Object.fromEntries([
  "primary", "primaryHover", "accent", "text", "secondaryText", "mutedText",
  "pageBackground", "panelBackground", "inputBackground", "bubbleBackground", "border",
].map((key) => [key, "#A1B2C3"]));
const presentation = { generationId: "generation-a", characterId: "Sakura" };
const publication = {
  schemaVersion: 1,
  coreGenerationId: "generation-a",
  characterId: "Sakura",
  values: {
    portraitScalePercent: 125,
    speechFontSize: 20,
    nameFontSize: 14,
    inputFontSize: 16,
    buttonFontSize: 16,
    themeTokens,
  },
};

test("pet appearance rejects stale generation, forged identity, and invalid fields", () => {
  assert.equal(validateAppearancePublication(publication, presentation).themeTokens.primary, "#a1b2c3");
  assert.throws(() => validateAppearancePublication({ ...publication, coreGenerationId: "old" }, presentation));
  assert.throws(() => validateAppearancePublication({ ...publication, characterId: "N.A.V.I." }, presentation));
  assert.throws(() => validateAppearancePublication({
    ...publication,
    values: { ...publication.values, portraitScalePercent: 151 },
  }, presentation));
});

test("portrait scaling is bottom-centered and constrained by the fixed native envelope", () => {
  const common = {
    sourceSize: [400, 800],
    portraitRect: [108, 12, 600, 656],
    windowSize: [816, 680],
  };
  assert.equal(constrainedPortraitScale({ ...common, requestedPercent: 50 }), 0.5);
  assert.equal(constrainedPortraitScale({ ...common, requestedPercent: 100 }), 1);
  assert.ok(constrainedPortraitScale({ ...common, requestedPercent: 150 }) <= 668 / 656);
  assert.throws(() => constrainedPortraitScale({ ...common, requestedPercent: 200 }));
});
