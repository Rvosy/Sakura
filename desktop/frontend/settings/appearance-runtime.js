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

export function createRuntimeAppearanceController({ document, invoke, onDirty, onError, prepare, fillTheme }) {
  let snapshot = null;
  let baseline = null;
  let draft = null;
  let previewQueued = false;
  let previewRunning = false;
  let previewDrainPromise = Promise.resolve();
  let previewFrame = null;
  let disposed = false;
  let generationTimer = null;

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
        await invoke("settings_character_appearance_preview", { values });
      }
    } catch (error) {
      onError(String(error));
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
    if (previewFrame !== null || disposed) return;
    previewFrame = window.requestAnimationFrame(() => {
      previewFrame = null;
      if (!previewRunning) previewDrainPromise = drainPreview();
    });
  }

  function changed() {
    try {
      draft = read();
      previewQueued = true;
      onDirty();
      schedulePreview();
    } catch (error) {
      onError(String(error));
    }
  }

  async function initialize(input) {
    snapshot = validateAppearanceSnapshot(input);
    baseline = clone(snapshot.appearance.values);
    draft = clone(baseline);
    prepare(snapshot, THEME_FIELDS);
    fill(draft);
    for (const inputId of Object.values(scalarControls)) {
      document.getElementById(inputId).addEventListener("input", changed);
    }
    document.getElementById("themeColors").addEventListener("input", changed);
    document.getElementById("resetThemeButton").addEventListener("click", () => {
      draft.themeTokens = clone(snapshot.presentation.themeTokens);
      fill(draft);
      changed();
    });
    generationTimer = window.setInterval(async () => {
      if (disposed) return;
      try {
        const lifecycle = await invoke("runtime_lifecycle_snapshot");
        if (lifecycle?.supervisor?.generationId === snapshot.presentation.generationId) return;
        previewQueued = false;
        cancelPreviewFrame();
        await invoke("settings_character_appearance_cancel_preview");
        disposed = true;
        for (const control of document.querySelectorAll(
          "#page-character input, #page-character select, #page-character button, #page-appearance input, #page-appearance select, #page-appearance button, #applyButton, #saveButton",
        )) {
          control.disabled = true;
        }
        onError("Core 已更新；未提交预览已恢复。请关闭并重新打开设置。");
        window.clearInterval(generationTimer);
      } catch {
        // A transient lifecycle read must not mutate the draft or persisted baseline.
      }
    }, 500);
    onDirty();
  }

  return Object.freeze({
    initialize,
    isDirty: () => Boolean(baseline && stable(draft) !== stable(baseline)),
    async save() {
      if (!snapshot) throw new Error("角色外观设置尚未加载");
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
      disposed = true;
      previewQueued = false;
      cancelPreviewFrame();
      window.clearInterval(generationTimer);
    },
  });
}
