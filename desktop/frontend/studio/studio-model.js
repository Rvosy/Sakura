export { normalizeColorText } from "../core/theme-runtime.js";

export function characterOptionLabel(character) {
  return character?.display_name || character?.id || "";
}

export function characterOptionGroup(character) {
  return character?.is_installed
    ? { id: "published", label: "角色列表", sourceLabel: "已添加" }
    : { id: "workspace", label: "草稿", sourceLabel: "草稿" };
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
    throw new Error("角色数据无效，请重新打开角色工坊。");
  }
  const serialized = JSON.stringify(value);
  if (serialized.includes('"packageDir"') || serialized.includes('"sourcePath"')) {
    throw new Error("角色数据校验失败，请重新打开角色工坊。");
  }
  return value;
}
