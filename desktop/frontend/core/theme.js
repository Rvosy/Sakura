const SAFE_COLOR = /^(?:#(?:[0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})|(?:rgb|hsl)a?\([\d\s.,%+-]+\))$/i;

export const THEMES = Object.freeze({
  blossom: Object.freeze({
    ink: "#2d202b",
    muted: "#766571",
    accent: "#b95882",
    accentDeep: "#71364f",
    paper: "#fff8fb",
    line: "#d8afc1",
  }),
  moon: Object.freeze({
    ink: "#f7edf3",
    muted: "#c6afbd",
    accent: "#ef8ab3",
    accentDeep: "#f5bad1",
    paper: "#2b202b",
    line: "#705064",
  }),
});

const VARIABLES = Object.freeze({
  ink: "--ink",
  muted: "--muted",
  accent: "--sakura",
  accentDeep: "--sakura-deep",
  paper: "--paper",
  line: "--line",
});

export function themeToCssVariables(theme) {
  const source = theme && typeof theme === "object" ? theme : THEMES.blossom;
  return Object.freeze(
    Object.fromEntries(
      Object.entries(VARIABLES).map(([field, variable]) => [
        variable,
        typeof source[field] === "string" && SAFE_COLOR.test(source[field]) ? source[field] : THEMES.blossom[field],
      ]),
    ),
  );
}

export function applyTheme(name, root = document.documentElement) {
  const selected = Object.hasOwn(THEMES, name) ? name : "blossom";
  for (const [variable, value] of Object.entries(themeToCssVariables(THEMES[selected]))) root.style.setProperty(variable, value);
  root.dataset.theme = selected;
  return selected;
}
