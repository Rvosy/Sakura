const THEME_FIELDS = Object.freeze([
  ["primary", "primary_color", "主题色"],
  ["primaryHover", "primary_hover_color", "按钮悬停色"],
  ["accent", "accent_color", "强调色"],
  ["text", "text_color", "主文字色"],
  ["secondaryText", "secondary_text_color", "次级文字色"],
  ["mutedText", "muted_text_color", "弱提示文字色"],
  ["pageBackground", "page_background_color", "页面背景色"],
  ["panelBackground", "panel_background_color", "面板背景色"],
  ["inputBackground", "input_background_color", "输入框背景色"],
  ["bubbleBackground", "bubble_background_color", "气泡背景色"],
  ["border", "border_color", "边框色"],
]);
const VALUE_FIELDS = Object.freeze([
  "portraitScalePercent",
  "controlPanelWidth",
  "bubbleMaxHeight",
  "controlPanelVerticalOffset",
  "inputBarOffset",
  "speechFontSize",
  "nameFontSize",
  "inputFontSize",
]);
const HEX = /^#[0-9a-f]{6}$/i;

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function stable(value) {
  return JSON.stringify(value);
}

export function validateAppearanceValues(values, limits) {
  if (!values || typeof values !== "object" || Array.isArray(values)) throw new Error("外观设置格式无效");
  const output = {};
  for (const field of VALUE_FIELDS) {
    const limit = limits?.[field];
    const value = values[field];
    if (
      !Array.isArray(limit)
      || limit.length !== 3
      || !limit.every(Number.isSafeInteger)
      || !Number.isSafeInteger(value)
      || value < limit[0]
      || value > limit[1]
    ) {
      throw new Error(`外观字段超出允许范围：${field}`);
    }
    output[field] = value;
  }
  const theme = values.themeTokens;
  if (
    !theme
    || typeof theme !== "object"
    || Array.isArray(theme)
    || Object.keys(theme).length !== THEME_FIELDS.length
  ) {
    throw new Error("角色主题字段不完整");
  }
  output.themeTokens = {};
  for (const [field] of THEME_FIELDS) {
    if (!HEX.test(theme[field] || "")) throw new Error(`主题颜色无效：${field}`);
    output.themeTokens[field] = theme[field].toLowerCase();
  }
  return Object.freeze(output);
}

export function validateAppearanceSnapshot(snapshot) {
  if (snapshot?.schemaVersion !== 1 || !Number.isSafeInteger(snapshot.windowGeneration)) {
    throw new Error("不支持的角色外观设置响应");
  }
  const presentation = snapshot.presentation;
  const publication = snapshot.appearance;
  if (
    !presentation
    || !publication
    || publication.schemaVersion !== 2
    || publication.coreGenerationId !== presentation.generationId
    || publication.characterId !== presentation.characterId
    || !Array.isArray(presentation.portraitKeys)
    || presentation.portraitKeys.length === 0
    || !presentation.portraitKeys.every((key) => typeof presentation.portraitResourceUrls?.[key] === "string")
  ) {
    throw new Error("角色外观 identity 不一致");
  }
  return Object.freeze({
    ...snapshot,
    appearance: Object.freeze({
      ...publication,
      values: validateAppearanceValues(publication.values, snapshot.limits),
    }),
  });
}

export function toLegacyTheme(themeTokens) {
  return Object.fromEntries(THEME_FIELDS.map(([field, legacyField]) => [legacyField, themeTokens[field]]));
}

function setRange(input, limit, value) {
  [input.min, input.max, input.value] = [String(limit[0]), String(limit[1]), String(value)];
  const output = input.parentElement?.querySelector(".slider-value");
  if (output) output.textContent = String(value);
  const progress = ((value - limit[0]) / (limit[1] - limit[0])) * 100;
  input.style.setProperty("--slider-progress", `${progress}%`);
}

export function createRuntimeAppearanceController({
  document,
  invoke,
  onDirty,
  onError,
  prepare,
  fillTheme,
  wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
}) {
  let snapshot = null;
  let baseline = null;
  let draft = null;
  let previewQueued = false;
  let previewRunning = false;
  let previewDrainPromise = Promise.resolve();
  let previewFrame = null;
  let disposed = false;
  let generationTimer = null;
  let generationPollRunning = false;
  let rebinding = false;
  let rebindPromise = null;
  let portraitScaleGestureActive = false;
  let portraitScaleGestureStartPromise = Promise.resolve();

  const scalarControls = Object.freeze({
    portraitScalePercent: "portraitScale",
    controlPanelWidth: "controlPanelWidth",
    bubbleMaxHeight: "bubbleHeight",
    controlPanelVerticalOffset: "controlPanelOffset",
    inputBarOffset: "inputBarOffset",
    speechFontSize: "speechFontSize",
    nameFontSize: "nameFontSize",
    inputFontSize: "inputFontSize",
  });

  function fill(values) {
    for (const [field, inputId] of Object.entries(scalarControls)) {
      setRange(document.getElementById(inputId), snapshot.limits[field], values[field]);
    }
    fillTheme(toLegacyTheme(values.themeTokens));
  }

  function read() {
    const values = { themeTokens: {} };
    for (const [field, inputId] of Object.entries(scalarControls)) {
      values[field] = Number.parseInt(document.getElementById(inputId).value, 10);
    }
    for (const [field, legacyField] of THEME_FIELDS) {
      values.themeTokens[field] = document.querySelector(`[data-theme-field="${legacyField}"]`).value;
    }
    return validateAppearanceValues(values, snapshot.limits);
  }

  async function drainPreview() {
    if (previewRunning || disposed) return;
    previewRunning = true;
    try {
      while (previewQueued && !disposed) {
        previewQueued = false;
        const values = clone(draft);
        if (portraitScaleGestureActive) await portraitScaleGestureStartPromise;
        await invoke("settings_character_appearance_preview", { values });
      }
    } catch (error) {
      if (!rebinding) onError(String(error));
    } finally {
      previewRunning = false;
    }
  }

  function cancelPreviewFrame() {
    if (previewFrame === null) return;
    window.cancelAnimationFrame(previewFrame);
    previewFrame = null;
  }

  function schedulePreview() {
    if (previewFrame !== null || disposed || rebinding) return;
    previewFrame = window.requestAnimationFrame(() => {
      previewFrame = null;
      if (!previewRunning) previewDrainPromise = drainPreview();
    });
  }

  function beginPortraitScaleGesture() {
    if (portraitScaleGestureActive || disposed || rebinding) return;
    portraitScaleGestureActive = true;
    portraitScaleGestureStartPromise = invoke("settings_character_appearance_scale_gesture", {
      active: true,
    }).catch((error) => {
      portraitScaleGestureActive = false;
      if (!rebinding) onError(String(error));
    });
  }

  async function flushPreview() {
    cancelPreviewFrame();
    if (!previewRunning && previewQueued) previewDrainPromise = drainPreview();
    await previewDrainPromise;
  }

  async function endPortraitScaleGesture() {
    if (!portraitScaleGestureActive) return;
    await portraitScaleGestureStartPromise;
    await flushPreview();
    portraitScaleGestureActive = false;
    await invoke("settings_character_appearance_scale_gesture", { active: false });
  }

  function finishPortraitScaleGesture() {
    return endPortraitScaleGesture().catch((error) => {
      if (!rebinding) onError(String(error));
    });
  }

  function changed() {
    try {
      draft = read();
      previewQueued = true;
      onDirty();
      if (!rebinding) schedulePreview();
    } catch (error) {
      onError(String(error));
    }
  }

  function applySnapshot(input, { preserveDraft = false } = {}) {
    const next = validateAppearanceSnapshot(input);
    const previousDraft = draft ? clone(draft) : null;
    const previousCharacterId = snapshot?.presentation?.characterId || "";
    snapshot = next;
    baseline = clone(snapshot.appearance.values);
    draft = clone(baseline);
    if (preserveDraft && previousDraft && previousCharacterId === snapshot.presentation.characterId) {
      try {
        draft = clone(validateAppearanceValues(previousDraft, snapshot.limits));
      } catch {
        draft = clone(baseline);
      }
    }
    prepare(snapshot, THEME_FIELDS);
    fill(draft);
    onDirty();
  }

  async function rebindGeneration(targetGeneration) {
    if (disposed || !targetGeneration || targetGeneration === snapshot?.presentation?.generationId) return;
    if (rebindPromise) return rebindPromise;
    rebinding = true;
    let restorePreview = false;
    rebindPromise = (async () => {
      previewQueued = false;
      cancelPreviewFrame();
      await previewDrainPromise;
      try {
        await invoke("settings_character_appearance_cancel_preview");
      } catch {
        // Supervisor generation replacement already rolls back an old preview session.
      }
      const deadline = Date.now() + 10_000;
      let lastError = null;
      while (!disposed && Date.now() < deadline) {
        try {
          const next = validateAppearanceSnapshot(await invoke("settings_character_appearance_get"));
          if (next.presentation.generationId === targetGeneration) {
            applySnapshot(next, { preserveDraft: true });
            restorePreview = Boolean(baseline && stable(draft) !== stable(baseline));
            return;
          }
        } catch (error) {
          lastError = error;
        }
        await wait(100);
      }
      throw new Error(`APPEARANCE_CORE_REBIND_NOT_READY${lastError ? `: ${String(lastError)}` : ""}`);
    })().catch((error) => {
      onError("Core 正在恢复外观设置，请稍后再试。已有改动仍会保留。");
      throw error;
    }).finally(() => {
      rebinding = false;
      rebindPromise = null;
      if (restorePreview && !disposed) {
        previewQueued = true;
        schedulePreview();
      }
    });
    return rebindPromise;
  }

  async function initialize(input) {
    applySnapshot(input);
    for (const inputId of Object.values(scalarControls)) {
      document.getElementById(inputId).addEventListener("input", changed);
    }
    const portraitScale = document.getElementById("portraitScale");
    portraitScale.addEventListener("pointerdown", beginPortraitScaleGesture);
    for (const eventName of ["pointerup", "pointercancel", "lostpointercapture", "blur"]) {
      portraitScale.addEventListener(eventName, finishPortraitScaleGesture);
    }
    portraitScale.addEventListener("keydown", (event) => {
      if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "PageUp", "PageDown"]
        .includes(event.key)) beginPortraitScaleGesture();
    });
    portraitScale.addEventListener("keyup", finishPortraitScaleGesture);
    document.getElementById("themeColors").addEventListener("input", changed);
    document.getElementById("resetThemeButton").addEventListener("click", () => {
      draft.themeTokens = clone(snapshot.presentation.themeTokens);
      fill(draft);
      changed();
    });
    generationTimer = window.setInterval(async () => {
      if (disposed || generationPollRunning) return;
      generationPollRunning = true;
      try {
        const lifecycle = await invoke("runtime_lifecycle_snapshot");
        const targetGeneration = lifecycle?.supervisor?.generationId;
        if (typeof targetGeneration === "string" && targetGeneration) {
          await rebindGeneration(targetGeneration);
        }
      } catch {
        // A transient lifecycle read must not mutate the draft or persisted baseline.
      } finally {
        generationPollRunning = false;
      }
    }, 500);
  }

  return Object.freeze({
    initialize,
    isDirty: () => Boolean(baseline && stable(draft) !== stable(baseline)),
    async save() {
      if (rebindPromise) await rebindPromise;
      if (!snapshot) throw new Error("角色外观设置尚未加载");
      await endPortraitScaleGesture();
      draft = read();
      previewQueued = false;
      cancelPreviewFrame();
      await previewDrainPromise;
      const result = await invoke("settings_character_appearance_save", { values: clone(draft) });
      if (
        result?.coreGenerationId !== snapshot.presentation.generationId
        || result?.characterId !== snapshot.presentation.characterId
      ) {
        throw new Error("保存响应 identity 不一致");
      }
      baseline = clone(validateAppearanceValues(result.values, snapshot.limits));
      draft = clone(baseline);
      fill(draft);
      onDirty();
    },
    async cancelPreview() {
      if (rebindPromise) await rebindPromise;
      await endPortraitScaleGesture();
      previewQueued = false;
      cancelPreviewFrame();
      await previewDrainPromise;
      await invoke("settings_character_appearance_cancel_preview");
      if (baseline) {
        draft = clone(baseline);
        fill(draft);
      }
      onDirty();
    },
    dispose() {
      if (portraitScaleGestureActive) {
        portraitScaleGestureActive = false;
        void portraitScaleGestureStartPromise
          .then(() => invoke("settings_character_appearance_scale_gesture", { active: false }))
          .catch(() => {});
      }
      disposed = true;
      previewQueued = false;
      cancelPreviewFrame();
      window.clearInterval(generationTimer);
      rebindPromise = null;
      rebinding = false;
    },
  });
}
