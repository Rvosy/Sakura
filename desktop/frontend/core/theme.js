const themeVariables = {
  primary_color: "--sakura-primary",
  primary_hover_color: "--sakura-primary-hover",
  accent_color: "--sakura-accent",
  text_color: "--sakura-text",
  secondary_text_color: "--sakura-secondary-text",
  muted_text_color: "--sakura-muted-text",
  page_background_color: "--sakura-page",
  panel_background_color: "--sakura-panel",
  input_background_color: "--sakura-input",
  bubble_background_color: "--sakura-bubble",
  border_color: "--sakura-border",
};

export function themeToCssVariables(theme = {}) {
  return Object.fromEntries(
    Object.entries(themeVariables)
      .filter(([field]) => typeof theme[field] === "string" && theme[field])
      .map(([field, variable]) => [variable, theme[field]]),
  );
}

export function applyTheme(theme, root = document.documentElement) {
  for (const [variable, value] of Object.entries(themeToCssVariables(theme))) {
    root.style.setProperty(variable, value);
  }
  root.dataset.visualEffect = theme?.visual_effect_mode || "gaussian_blur";
}
