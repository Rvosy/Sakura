const HEX = /^#[0-9a-f]{6}$/i;
const THEME_KEYS = Object.freeze([
  "primary",
  "primaryHover",
  "accent",
  "text",
  "secondaryText",
  "mutedText",
  "pageBackground",
  "panelBackground",
  "inputBackground",
  "bubbleBackground",
  "border",
]);

export function validateAppearancePublication(publication, presentation) {
  if (
    publication?.schemaVersion !== 2
    || publication.coreGenerationId !== presentation?.generationId
    || publication.characterId !== presentation?.characterId
  ) {
    throw new Error("APPEARANCE_IDENTITY_STALE");
  }
  const values = publication.values;
  for (const [field, minimum, maximum] of [
    ["portraitScalePercent", 50, 150],
    ["controlPanelWidth", 420, 760],
    ["bubbleMaxHeight", 96, 260],
    ["controlPanelVerticalOffset", -60, 160],
    ["inputBarOffset", 0, 60],
    ["speechFontSize", 10, 24],
    ["nameFontSize", 10, 20],
    ["inputFontSize", 12, 20],
  ]) {
    if (!Number.isSafeInteger(values?.[field]) || values[field] < minimum || values[field] > maximum) {
      throw new Error(`APPEARANCE_FIELD_INVALID:${field}`);
    }
  }
  if (
    !values.themeTokens
    || Object.keys(values.themeTokens).length !== THEME_KEYS.length
    || THEME_KEYS.some((key) => !HEX.test(values.themeTokens[key] || ""))
  ) {
    throw new Error("APPEARANCE_THEME_INVALID");
  }
  return Object.freeze({
    ...values,
    themeTokens: Object.freeze(
      Object.fromEntries(THEME_KEYS.map((key) => [key, values.themeTokens[key].toLowerCase()])),
    ),
  });
}

export function constrainedPortraitScale({ requestedPercent, sourceSize, portraitRect, windowSize }) {
  const [sourceWidth, sourceHeight] = sourceSize || [];
  const [x, y, targetWidth, targetHeight] = portraitRect || [];
  const [windowWidth, windowHeight] = windowSize || [];
  if (
    ![sourceWidth, sourceHeight, x, y, targetWidth, targetHeight, windowWidth, windowHeight].every(Number.isFinite)
    || sourceWidth <= 0
    || sourceHeight <= 0
    || targetWidth <= 0
    || targetHeight <= 0
    || requestedPercent < 50
    || requestedPercent > 150
  ) {
    throw new Error("APPEARANCE_PORTRAIT_SCALE_INVALID");
  }
  const contain = Math.min(targetWidth / sourceWidth, targetHeight / sourceHeight);
  const baseWidth = sourceWidth * contain;
  const baseHeight = sourceHeight * contain;
  const centerX = x + targetWidth / 2;
  const bottom = y + targetHeight;
  const maxWidth = 2 * Math.min(centerX, windowWidth - centerX);
  const maxHeight = Math.min(bottom, windowHeight);
  return Math.min(requestedPercent / 100, maxWidth / baseWidth, maxHeight / baseHeight);
}

export function applyAppearanceVariables(values, root = document.documentElement) {
  root.style.setProperty("--speech-font-size", `${values.speechFontSize}px`);
  root.style.setProperty("--name-font-size", `${values.nameFontSize}px`);
  root.style.setProperty("--input-font-size", `${values.inputFontSize}px`);
}

export function appearanceChanges(previous, next) {
  const theme = THEME_KEYS.some((key) => previous?.themeTokens?.[key] !== next?.themeTokens?.[key]);
  const fonts = ["speechFontSize", "nameFontSize", "inputFontSize"]
    .some((field) => previous?.[field] !== next?.[field]);
  const layout = ["controlPanelWidth", "bubbleMaxHeight", "controlPanelVerticalOffset", "inputBarOffset"]
    .some((field) => previous?.[field] !== next?.[field]);
  return Object.freeze({
    theme,
    fonts,
    layout,
    portrait: previous?.portraitScalePercent !== next?.portraitScalePercent,
  });
}
