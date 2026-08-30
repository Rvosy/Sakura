export function normalizeColorText(value, fallback = "") {
  const text = String(value || "").trim();
  const prefixed = text.startsWith("#") ? text : `#${text}`;
  return /^#[0-9a-fA-F]{6}$/.test(prefixed) ? prefixed.toLowerCase() : fallback;
}

export function characterOptionLabel(character) {
  return character?.display_name || character?.id || "";
}

export function characterOptionGroup(character) {
  return character?.is_installed
    ? { id: "published", label: "已发布角色", sourceLabel: "已发布" }
    : { id: "workspace", label: "工作区", sourceLabel: "工作区" };
}

export function uniqueReplyTones(references) {
  const seen = new Set();
  const tones = [];
  (Array.isArray(references) ? references : []).forEach((reference) => {
    const tone = String(reference?.tone || "").trim();
    if (tone && !seen.has(tone)) {
      seen.add(tone);
      tones.push(tone);
    }
  });
  return tones;
}

export function isValidCharacterId(value) {
  const text = String(value || "").trim();
  return Boolean(text) && text !== "." && text !== ".." && /^[A-Za-z0-9_.-]+$/.test(text);
}
