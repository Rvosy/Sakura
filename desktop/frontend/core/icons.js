// Keep this local catalogue aligned with assets/lucide and core/icons.css.
export const iconNames = Object.freeze([
  "refresh-cw",
  "arrow-right",
  "audio-lines",
  "brain",
  "camera",
  "check",
  "chevron-down",
  "chevron-right",
  "chevron-up",
  "circle-arrow-up",
  "circle-question-mark",
  "cloud",
  "cpu",
  "database",
  "download",
  "external-link",
  "file-text",
  "folder",
  "globe",
  "heart",
  "images",
  "info",
  "layers",
  "link",
  "loader-circle",
  "messages-square",
  "mic",
  "monitor",
  "music",
  "palette",
  "plus",
  "puzzle",
  "scan-line",
  "search",
  "send",
  "send-horizontal",
  "settings",
  "settings-2",
  "shield-check",
  "sliders-horizontal",
  "smartphone",
  "sparkles",
  "speech",
  "sticky-note",
  "terminal",
  "triangle-alert",
  "user-round",
  "wrench",
  "x"
]);
const names = new Set(iconNames);
export const hasIcon = (name) => typeof name === "string" && names.has(name);

export function iconMarkup(name) {
  const resolved = hasIcon(name) ? name : "puzzle";
  return `<span class="sakura-icon icon-${resolved}" aria-hidden="true"></span>`;
}

export function createIcon(document, name) {
  const element = document.createElement("span");
  element.className = `sakura-icon icon-${hasIcon(name) ? name : "puzzle"}`;
  element.setAttribute("aria-hidden", "true");
  return element;
}
