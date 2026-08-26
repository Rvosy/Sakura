const SAFE_HEX = /^#[0-9a-f]{6}$/i;

export const FALLBACK_THEME_TOKENS = Object.freeze({
  primary: "#4b9ac4",
  primaryHover: "#3b83aa",
  accent: "#e36c96",
  text: "#27445a",
  secondaryText: "#54768b",
  mutedText: "#7d99a9",
  pageBackground: "#f8fcfe",
  panelBackground: "#eaf5fa",
  inputBackground: "#ffffff",
  bubbleBackground: "#e3f1f7",
  border: "#accfde",
});

const VARIABLES = Object.freeze({
  primary: "--primary",
  primaryHover: "--primary-hover",
  accent: "--accent",
  text: "--text",
  secondaryText: "--secondary-text",
  mutedText: "--muted-text",
  pageBackground: "--page-background",
  panelBackground: "--panel-background",
  inputBackground: "--input-background",
  bubbleBackground: "--bubble-background",
  border: "--border",
});

export function normalizeThemeTokens(tokens) {
  const source = tokens && typeof tokens === "object" ? tokens : {};
  return Object.freeze(
    Object.fromEntries(
      Object.keys(VARIABLES).map((key) => [
        key,
        typeof source[key] === "string" && SAFE_HEX.test(source[key])
          ? source[key].toLowerCase()
          : FALLBACK_THEME_TOKENS[key],
      ]),
    ),
  );
}

export function themeToCssVariables(tokens) {
  const normalized = normalizeThemeTokens(tokens);
  return Object.freeze(
    Object.fromEntries(Object.entries(VARIABLES).map(([key, variable]) => [variable, normalized[key]])),
  );
}

export function applyTheme(tokens, root = document.documentElement) {
  for (const [variable, value] of Object.entries(themeToCssVariables(tokens))) root.style.setProperty(variable, value);
  root.dataset.theme = "character";
  return normalizeThemeTokens(tokens);
}
