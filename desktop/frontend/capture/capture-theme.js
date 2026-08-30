const SAFE_HEX = /^#[0-9a-f]{6}$/i;
const FALLBACK_CAPTURE_PRIMARY = "#4b9ac4";

export function normalizeCapturePrimary(value) {
  const candidate = typeof value === "string" && !value.startsWith("#") ? `#${value}` : value;
  return typeof candidate === "string" && SAFE_HEX.test(candidate)
    ? candidate.toLowerCase()
    : FALLBACK_CAPTURE_PRIMARY;
}

export function applyCaptureTheme(value, root = document.documentElement) {
  const primary = normalizeCapturePrimary(value);
  root.style.setProperty("--capture-primary", primary);
  return primary;
}
