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

export function selectBootstrapCharacter(characters, selectedCharacterId) {
  const options = Array.isArray(characters) ? characters : [];
  const requested = String(selectedCharacterId || "");
  return options.some((character) => character?.id === requested)
    ? requested
    : String(options[0]?.id || "");
}

export function hasUnsavedEditorChanges(savedSnapshot, currentSnapshot) {
  return String(savedSnapshot) !== String(currentSnapshot);
}

export function operationCancelState(result) {
  if (result?.state === "finalizing") return "finalizing";
  return result?.cancelled ? "cancelling" : "finished";
}

export function runtimeReloadState(value) {
  return ["ready", "failed", "requested", "not_required"].includes(value)
    ? value
    : "unknown";
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
  return Boolean(text)
    && text !== "."
    && text !== ".."
    && /^[A-Za-z0-9_.-]+$/.test(text);
}

export function validateStudioResponse(value) {
  if (!value || typeof value !== "object" || Array.isArray(value) || value.schemaVersion !== 1) {
    throw new Error("角色工坊返回了无效数据。");
  }
  const serialized = JSON.stringify(value);
  if (serialized.includes('"packageDir"') || serialized.includes('"sourcePath"')) {
    throw new Error("角色工坊返回了不允许公开的路径字段。");
  }
  return value;
}
