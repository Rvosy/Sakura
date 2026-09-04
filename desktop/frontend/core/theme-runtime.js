export const THEME_CSS_VARIABLES = Object.freeze({
  primary_color: "--sakura-primary",
  primary_hover_color: "--sakura-primary-hover",
  accent_color: "--sakura-accent",
  text_color: "--sakura-text",
  secondary_text_color: "--sakura-secondary-text",
  muted_text_color: "--sakura-muted-text",
  page_background_color: "--sakura-page-bg",
  panel_background_color: "--sakura-panel-bg",
  input_background_color: "--sakura-input-bg",
  bubble_background_color: "--sakura-bubble-bg",
  border_color: "--sakura-border",
});

export const RUNTIME_THEME_FIELDS = Object.freeze({
  primary: "primary_color",
  primaryHover: "primary_hover_color",
  accent: "accent_color",
  text: "text_color",
  secondaryText: "secondary_text_color",
  mutedText: "muted_text_color",
  pageBackground: "page_background_color",
  panelBackground: "panel_background_color",
  inputBackground: "input_background_color",
  bubbleBackground: "bubble_background_color",
  border: "border_color",
});

export function isHexColor(value) {
  return typeof value === "string" && /^#[0-9a-fA-F]{6}$/.test(value);
}

export function normalizeColorText(value, fallback = "") {
  const text = String(value || "").trim();
  const prefixed = text.startsWith("#") ? text : `#${text}`;
  return isHexColor(prefixed) ? prefixed.toLowerCase() : fallback;
}

export function applyThemeTokens(theme, root = document.documentElement) {
  const style = root?.style;
  if (!style) return;
  Object.entries(THEME_CSS_VARIABLES).forEach(([key, cssVariable]) => {
    const value = theme?.[key];
    if (isHexColor(value)) style.setProperty(cssVariable, value);
  });
}

export function toLegacyThemeTokens(themeTokens) {
  return Object.fromEntries(Object.entries(RUNTIME_THEME_FIELDS).map(([field, legacyField]) => [
    legacyField,
    themeTokens?.[field],
  ]));
}

export function applyRuntimeThemeTokens(themeTokens, root = document.documentElement) {
  applyThemeTokens(toLegacyThemeTokens(themeTokens), root);
}
