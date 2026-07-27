const THEME_FIELDS = Object.freeze([
  ["primary", "主色"],
  ["primaryHover", "主色悬停"],
  ["accent", "强调色"],
  ["text", "正文"],
  ["secondaryText", "次要文字"],
  ["mutedText", "弱化文字"],
  ["pageBackground", "页面背景"],
  ["panelBackground", "面板背景"],
  ["inputBackground", "输入框背景"],
  ["bubbleBackground", "气泡背景"],
  ["border", "边框"],
]);
const VALUE_FIELDS = Object.freeze([
  "portraitScalePercent",
  "speechFontSize",
  "nameFontSize",
  "inputFontSize",
  "buttonFontSize",
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
    || publication.schemaVersion !== 1
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

function setRange(input, output, limit, value) {
  [input.min, input.max, input.value] = [String(limit[0]), String(limit[1]), String(value)];
  output.value = `${value}${input.dataset.unit || ""}`;
}

function renderPresentation(document, snapshot) {
  const presentation = snapshot.presentation;
  document.getElementById("runtimeCharacterName").textContent = presentation.displayName;
  document.getElementById("runtimeCharacterId").textContent = presentation.characterId;
  document.getElementById("runtimeInitialMessage").textContent = presentation.initialMessage;
  const select = document.getElementById("runtimeCharacterSelect");
  select.textContent = "";
  const option = document.createElement("option");
  option.value = presentation.characterId;
  option.textContent = presentation.displayName;
  select.append(option);
  select.disabled = true;

  const portraits = document.getElementById("runtimePortraits");
  portraits.textContent = "";
  for (const key of presentation.portraitKeys) {
    const item = document.createElement("figure");
    item.className = "runtime-portrait-card";
    const image = document.createElement("img");
    image.src = presentation.portraitResourceUrls[key];
    image.alt = `${presentation.displayName}：${key}`;
    image.decoding = "async";
    const caption = document.createElement("figcaption");
    caption.textContent = key === presentation.defaultPortraitKey ? `${key}（默认）` : key;
    item.append(image, caption);
    portraits.append(item);
  }
}

function renderThemeInputs(document) {
  const host = document.getElementById("runtimeThemeFields");
  host.textContent = "";
  for (const [field, label] of THEME_FIELDS) {
    const row = document.createElement("label");
    row.className = "runtime-theme-field";
    row.innerHTML = `<span>${label}</span><input type="color" data-runtime-theme="${field}" /><code></code>`;
    host.append(row);
  }
}

export function createRuntimeAppearanceController({ document, invoke, onDirty, onError }) {
  let snapshot = null;
  let baseline = null;
  let draft = null;
  let previewQueued = false;
  let previewRunning = false;
  let previewDrainPromise = Promise.resolve();
  let disposed = false;
  let generationTimer = null;

  const scalarControls = Object.freeze({
    portraitScalePercent: ["runtimePortraitScale", "runtimePortraitScaleValue"],
    speechFontSize: ["runtimeSpeechFont", "runtimeSpeechFontValue"],
    nameFontSize: ["runtimeNameFont", "runtimeNameFontValue"],
    inputFontSize: ["runtimeInputFont", "runtimeInputFontValue"],
    buttonFontSize: ["runtimeButtonFont", "runtimeButtonFontValue"],
  });

  function fill(values) {
    for (const [field, [inputId, outputId]] of Object.entries(scalarControls)) {
      setRange(
        document.getElementById(inputId),
        document.getElementById(outputId),
        snapshot.limits[field],
        values[field],
      );
    }
    for (const [field] of THEME_FIELDS) {
      const input = document.querySelector(`[data-runtime-theme="${field}"]`);
      input.value = values.themeTokens[field];
      input.nextElementSibling.textContent = values.themeTokens[field];
    }
  }

  function read() {
    const values = { themeTokens: {} };
    for (const [field, [inputId]] of Object.entries(scalarControls)) {
      values[field] = Number.parseInt(document.getElementById(inputId).value, 10);
    }
    for (const [field] of THEME_FIELDS) {
      values.themeTokens[field] = document.querySelector(`[data-runtime-theme="${field}"]`).value;
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

  function changed() {
    try {
      draft = read();
      fill(draft);
      previewQueued = true;
      onDirty();
      if (!previewRunning) previewDrainPromise = drainPreview();
    } catch (error) {
      onError(String(error));
    }
  }

  async function initialize(input) {
    snapshot = validateAppearanceSnapshot(input);
    baseline = clone(snapshot.appearance.values);
    draft = clone(baseline);
    renderPresentation(document, snapshot);
    renderThemeInputs(document);
    fill(draft);
    for (const [inputId] of Object.values(scalarControls)) {
      document.getElementById(inputId).addEventListener("input", changed);
    }
    document.getElementById("runtimeThemeFields").addEventListener("input", changed);
    document.getElementById("runtimeResetTheme").addEventListener("click", () => {
      draft.themeTokens = clone(snapshot.presentation.themeTokens);
      fill(draft);
      changed();
    });
    document.getElementById("runtimeCharacterPanel").hidden = false;
    document.getElementById("runtimeAppearancePanel").hidden = false;
    generationTimer = window.setInterval(async () => {
      if (disposed) return;
      try {
        const lifecycle = await invoke("runtime_lifecycle_snapshot");
        if (lifecycle?.supervisor?.generationId === snapshot.presentation.generationId) return;
        previewQueued = false;
        await invoke("settings_character_appearance_cancel_preview");
        disposed = true;
        for (const control of document.querySelectorAll(".runtime-settings-panel input, .runtime-settings-panel button")) {
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
      window.clearInterval(generationTimer);
    },
  });
}
