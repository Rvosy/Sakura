import { normalizeThemeTokens } from "../core/theme.js";

const ASSET_URL = /^(?:sakura-character:\/\/localhost|http:\/\/sakura-character\.localhost)\/v1\/[0-9a-f]+\/[0-9a-f]{32}-[0-9a-f]+$/;
const MODULE_URL = /^(?:sakura-character:\/\/localhost|http:\/\/sakura-character\.localhost)\/module\/[0-9a-f]+\/[0-9a-f]{32}\/(?:[A-Za-z0-9_-][A-Za-z0-9_.-]*\/)*[A-Za-z0-9_-][A-Za-z0-9_.-]*\.m?js$/;
export function validateCharacterPresentation(value) {
  if (!value || value.schemaVersion !== 2) throw new Error("CHARACTER_PRESENTATION_SCHEMA_UNSUPPORTED");
  for (const key of ["generationId", "characterId", "displayName", "initialMessage", "visualReasonCode"]) {
    if (typeof value[key] !== "string" || !value[key] || value[key].length > 16384) throw new Error("CHARACTER_PRESENTATION_INVALID");
  }
  const visual = value.visual;
  if (visual != null && (!/^[0-9a-f]{32}$/.test(visual.bindingId || "") || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(visual.resourceId || "")
    || !MODULE_URL.test(visual.renderer || "") || (visual.editor && !MODULE_URL.test(visual.editor))
    || !visual.assets || typeof visual.assets !== "object" || Object.values(visual.assets).some((url) => !ASSET_URL.test(url)))) throw new Error("VISUAL_PRESENTATION_INVALID");
  return Object.freeze({ ...structuredClone(value), themeTokens: normalizeThemeTokens(value.themeTokens) });
}

export async function loadCurrentCharacterPresentation({
  invoke,
  attempts = 160,
  delayMs = 100,
  setTimer = (callback, delay) => window.setTimeout(callback, delay),
  expectedGenerationId = "",
} = {}) {
  if (typeof invoke !== "function") throw new Error("CHARACTER_PRESENTATION_INVOKE_REQUIRED");
  let lastError = null;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      const presentation = validateCharacterPresentation(await invoke("current_character_presentation"));
      if (expectedGenerationId && presentation.generationId !== expectedGenerationId) {
        // The native command returns the current Core's publication. The requested
        // generation has been replaced; waiting cannot bring its resources back.
        return null;
      }
      if (!presentation.visual && presentation.visualReasonCode === "VISUAL_NOT_BOUND") {
        // Core publishes character identity before the plugin finishes binding.
        // This is startup progress, not an unavailable renderer.
        throw new Error("CHARACTER_PRESENTATION_NOT_READY");
      }
      return presentation;
    } catch (error) {
      lastError = error;
      const message = String(error?.message || error || "");
      if (!/NOT_READY|UNAVAILABLE|LIFECYCLE|GENERATION_STALE/i.test(message)) throw error;
      if (attempt + 1 < attempts) await new Promise((resolve) => setTimer(resolve, delayMs));
    }
  }
  throw lastError || new Error("CHARACTER_PRESENTATION_NOT_READY");
}
