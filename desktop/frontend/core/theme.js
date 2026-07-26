const SAFE_HEX = /^#[0-9a-f]{6}$/i;

export const FALLBACK_THEME_TOKENS = Object.freeze({
  primary: "#d55b91",
  primaryHover: "#bf3f7a",
  accent: "#b13e73",
  text: "#3d2b35",
  secondaryText: "#7a3656",
  mutedText: "#9b4f72",
  pageBackground: "#fff6fa",
  panelBackground: "#ffe8f1",
  inputBackground: "#ffffff",
  bubbleBackground: "#ffe8f1",
  border: "#eeacc8",
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
