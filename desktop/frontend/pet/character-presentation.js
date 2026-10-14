import { normalizeThemeTokens } from "../core/theme.js";
import { settleOnAbort } from "../core/settle-on-abort.js";
import { projectLifecycle } from "../lifecycle.js";

export function normalizeCharacterPresentation(value) {
  return Object.freeze({ ...structuredClone(value), themeTokens: normalizeThemeTokens(value.themeTokens) });
}

export async function loadCurrentCharacterPresentation({
  invoke,
  delayMs = 100,
  setTimer = (callback, delay) => window.setTimeout(callback, delay),
  clearTimer = (timer) => window.clearTimeout(timer),
  expectedGenerationId = "",
  signal,
} = {}) {
  const wait = (promise) => signal ? settleOnAbort(promise, signal) : promise;
  while (!signal?.aborted) {
    try {
      const value = await wait(invoke("current_character_presentation"));
      if (signal?.aborted) return null;
      const presentation = normalizeCharacterPresentation(value);
      if (expectedGenerationId && presentation.generationId !== expectedGenerationId) {
        // 旧代资源已撤销，由新代重新加载。
        return null;
      }
      // Core 先发布角色身份，再由插件完成表现资源绑定。
      if (presentation.visual || presentation.visualReasonCode !== "VISUAL_NOT_BOUND") return presentation;
    } catch (error) {
      const code = String(error?.message || error).split(":", 1)[0];
      if (!["CHARACTER_PRESENTATION_NOT_READY", "CHARACTER_PRESENTATION_UNAVAILABLE",
        "CHARACTER_PRESENTATION_GENERATION_STALE", "CHARACTER_RESOURCE_GENERATION_STALE"].includes(code)) throw error;
    }
    const publication = await wait(invoke("runtime_lifecycle_snapshot"));
    if (signal?.aborted) return null;
    const { supervisor } = publication;
    if (supervisor.appShutdown || (expectedGenerationId && supervisor.generationId
      && supervisor.generationId !== expectedGenerationId)) return null;
    if (["failed", "setup_required"].includes(projectLifecycle(publication).status)) return null;
    let timer;
    try {
      await wait(new Promise(resolve => { timer = setTimer(resolve, delayMs); }));
    } finally {
      if (timer !== undefined) clearTimer(timer);
    }
  }
  return null;
}
