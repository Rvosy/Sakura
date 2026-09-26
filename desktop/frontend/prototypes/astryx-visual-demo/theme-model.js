import { FALLBACK_THEME_TOKENS } from "../../core/theme.js";
import { normalizeColorText } from "../../core/theme-runtime.js";

export const neutralSurfaces = Object.freeze({
  light: { body: "#ffffff", sidebar: "#f7f7f7", card: "#ffffff", input: "#f5f5f5", border: "#e5e5e5", text: "#252525", secondary: "#737373" },
  dark: { body: "#181818", sidebar: "#1e1e1e", card: "#252525", input: "#2e2e2e", border: "#3d3d3d", text: "#ededed", secondary: "#ababab" },
});
// Accent is deliberately absent from the surface palette.
export function themeVariables(mode, accent, systemDark = false) {
  const scheme = mode === "system" ? systemDark ? "dark" : "light" : mode;
  const palette = neutralSurfaces[scheme];
  return { scheme, accent, ...palette };
}
export const editableColors = Object.freeze([
  {option:"accent", token:"primary", legacy:"primary_color", label:"主题色"},
  {option:"hover", token:"primaryHover", legacy:"primary_hover_color", label:"悬停色"},
  {option:"highlight", token:"accent", legacy:"accent_color", label:"强调色"},
]);

export function readThemeColors(tokens) {
  return Object.fromEntries(editableColors.map(({option,token,legacy}) => [option,
    normalizeColorText(tokens?.[token] ?? tokens?.[legacy], FALLBACK_THEME_TOKENS[token]),
  ]));
}

// Only patch the edited colors; the existing settings controller owns all other fields.
export function colorEditsToLegacy(changes) {
  return Object.fromEntries(editableColors.flatMap(({option,legacy}) => {
    const color = normalizeColorText(changes[option]);
    return color ? [[legacy,color]] : [];
  }));
}
