import { normalizeThemeTokens } from "../core/theme.js";

const RESOURCE_ID = /^character-v1-[0-9a-f]+-portrait-[0-9a-f]+$/;
const SAFE_PROTOCOL_URL = /^(?:sakura-character:\/\/localhost|http:\/\/sakura-character\.localhost)\/v1\/[0-9a-f]+\/character-v1-[0-9a-f]+-portrait-[0-9a-f]+$/;

function validText(value, max) {
  return typeof value === "string" && value.trim().length > 0 && value.length <= max && !/[\u0000-\u001f\u007f]/.test(value);
}

export function validateCharacterPresentation(value) {
  if (!value || typeof value !== "object" || value.schemaVersion !== 1) {
    throw new Error("CHARACTER_PRESENTATION_SCHEMA_UNSUPPORTED");
  }
  for (const [key, max] of [
    ["generationId", 256],
    ["characterId", 128],
    ["displayName", 128],
    ["initialMessage", 16384],
    ["defaultPortraitKey", 256],
  ]) {
    if (!validText(value[key], max)) throw new Error("CHARACTER_PRESENTATION_INVALID");
  }
  if (!/^[A-Za-z0-9._-]+$/.test(value.characterId)) throw new Error("CHARACTER_PRESENTATION_INVALID");
  if (!Array.isArray(value.portraitKeys) || value.portraitKeys.length < 1 || value.portraitKeys.length > 64) {
    throw new Error("CHARACTER_PRESENTATION_PORTRAITS_INVALID");
  }
  const keys = new Set(value.portraitKeys);
  if (keys.size !== value.portraitKeys.length || !keys.has(value.defaultPortraitKey)) {
    throw new Error("CHARACTER_PRESENTATION_PORTRAITS_INVALID");
  }
  for (const mappingName of ["portraitResourceIds", "portraitResourceUrls", "portraitMetadata"]) {
    const mapping = value[mappingName];
    if (!mapping || typeof mapping !== "object" || Object.keys(mapping).length !== keys.size) {
      throw new Error("CHARACTER_PRESENTATION_MAPPING_INVALID");
    }
    if ([...keys].some((key) => !Object.hasOwn(mapping, key))) {
      throw new Error("CHARACTER_PRESENTATION_MAPPING_INVALID");
    }
  }
  for (const key of keys) {
    if (!validText(key, 256) || !RESOURCE_ID.test(value.portraitResourceIds[key])) {
      throw new Error("CHARACTER_PRESENTATION_RESOURCE_ID_INVALID");
    }
    if (!SAFE_PROTOCOL_URL.test(value.portraitResourceUrls[key])) {
      throw new Error("CHARACTER_PRESENTATION_RESOURCE_URL_INVALID");
    }
    const metadata = value.portraitMetadata[key];
    if (
      !metadata ||
      !Number.isSafeInteger(metadata.width) ||
      !Number.isSafeInteger(metadata.height) ||
      !Number.isSafeInteger(metadata.byteLength) ||
      metadata.width < 1 ||
      metadata.height < 1 ||
      metadata.byteLength < 1
    ) {
      throw new Error("CHARACTER_PRESENTATION_METADATA_INVALID");
    }
  }
  return Object.freeze({
    ...value,
    themeTokens: normalizeThemeTokens(value.themeTokens),
    portraitKeys: Object.freeze([...value.portraitKeys]),
    portraitResourceIds: Object.freeze({ ...value.portraitResourceIds }),
    portraitResourceUrls: Object.freeze({ ...value.portraitResourceUrls }),
    portraitMetadata: Object.freeze(
      Object.fromEntries(Object.entries(value.portraitMetadata).map(([key, metadata]) => [key, Object.freeze({ ...metadata })])),
    ),
  });
}

export function portraitSequence(presentation) {
  const others = presentation.portraitKeys.filter((key) => key !== presentation.defaultPortraitKey);
  const choose = (pattern, fallbackIndex) =>
    others.find((key) => pattern.test(key)) || others[fallbackIndex % Math.max(1, others.length)] || presentation.defaultPortraitKey;
  return Object.freeze({
    default: presentation.defaultPortraitKey,
    thinking: choose(/思考|疑问|不知所措|無奈|无奈/u, 0),
    positive: choose(/开心|高兴|满足|脸红|坚定/u, 1),
    concerned: choose(/难过|不满|无语|无奈|不知所措/u, 2),
    multi: Object.freeze((others.length ? others : [presentation.defaultPortraitKey]).slice(0, 4)),
  });
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
        throw new Error("CHARACTER_PRESENTATION_GENERATION_STALE");
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
