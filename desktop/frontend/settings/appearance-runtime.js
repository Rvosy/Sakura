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
const LAYOUT_FIELDS = Object.freeze([
  "controlPanelWidth",
  "bubbleMaxHeight",
  "controlPanelVerticalOffset",
  "inputBarOffset",
]);
const LAYOUT_TRACE_KINDS = Object.freeze({
  controlPanelWidth: "layout-control-panel-width",
  bubbleMaxHeight: "layout-bubble-max-height",
  controlPanelVerticalOffset: "layout-control-panel-vertical-offset",
  inputBarOffset: "layout-input-bar-offset",
});
const HEX = /^#[0-9a-f]{6}$/i;
const NOOP_INTERACTION_TRACE = Object.freeze({
  enabled: false,
  createGesture: () => null,
  atRevision: () => null,
  mark: () => null,
  flush: async () => {},
});

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function stable(value) {
  return JSON.stringify(value);
}

function transientCharacterPresentationError(error) {
  return /CHARACTER_PRESENTATION_(?:NOT_READY|UNAVAILABLE)|LIFECYCLE_STATE_UNAVAILABLE/i
    .test(String(error?.message || error || ""));
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
  trace = NOOP_INTERACTION_TRACE,
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
  let portraitScaleGestureBackendActive = false;
  let portraitScaleGestureStartPromise = Promise.resolve();
  let portraitScaleGestureTransition = Promise.resolve();
  let portraitScaleFrame = null;
  let portraitScaleFrameQueued = false;
  let portraitScaleFrameRunning = false;
  let portraitScaleFramePercent = null;
  let portraitScaleFrameTrace = null;
  let portraitScaleFrameDrainPromise = Promise.resolve();
  let portraitScaleGestureTrace = null;
  let portraitScaleGestureRevision = 0;
  let layoutGestureActive = false;
  let layoutGestureBackendActive = false;
  let layoutGestureStartPromise = Promise.resolve();
  let layoutGestureTransition = Promise.resolve();
  let layoutFrame = null;
  let layoutFrameQueued = false;
  let layoutFrameRunning = false;
  let layoutFrameValues = null;
  let layoutFrameTrace = null;
  let layoutFrameDrainPromise = Promise.resolve();
  let layoutGestureTrace = null;
  let layoutGestureRevision = 0;
  let previewTrace = null;

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

  function tracedInvoke(command, args, context, stage) {
    if (trace.enabled && context) return trace.tracedInvoke(command, args, context, stage);
    return invoke(command, args);
  }

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
    let transientFailures = 0;
    try {
      while (previewQueued && !disposed) {
        previewQueued = false;
        const values = clone(draft);
        const context = previewTrace;
        if (portraitScaleGestureActive) await portraitScaleGestureStartPromise;
        if (layoutGestureActive) await layoutGestureStartPromise;
        try {
          await tracedInvoke(
            "settings_character_appearance_preview",
            { values },
            context,
            "appearance.preview",
          );
          transientFailures = 0;
        } catch (error) {
          if (transientCharacterPresentationError(error) && transientFailures < 3) {
            transientFailures += 1;
            previewQueued = true;
            await wait(50 * transientFailures);
            continue;
          }
          throw error;
        }
      }
    } catch (error) {
      if (!rebinding) {
        onError(transientCharacterPresentationError(error)
          ? "角色正在重新连接，请稍后再调整外观；当前改动已保留。"
          : String(error));
      }
    } finally {
      previewRunning = false;
    }
  }

  function cancelPreviewFrame() {
    if (previewFrame === null) return;
    window.cancelAnimationFrame(previewFrame);
    previewFrame = null;
  }

  function cancelPortraitScaleFrame() {
    if (portraitScaleFrame === null) return;
    window.cancelAnimationFrame(portraitScaleFrame);
    portraitScaleFrame = null;
  }

  async function drainPortraitScaleFrames() {
    if (portraitScaleFrameRunning || disposed) return;
    portraitScaleFrameRunning = true;
    let transientFailures = 0;
    try {
      while (portraitScaleFrameQueued && !disposed) {
        portraitScaleFrameQueued = false;
        const portraitScalePercent = portraitScaleFramePercent;
        const context = portraitScaleFrameTrace;
        await portraitScaleGestureStartPromise;
        try {
          await tracedInvoke(
            "settings_character_appearance_scale_frame",
            { portraitScalePercent },
            context,
            "portrait.frame",
          );
          transientFailures = 0;
        } catch {
          // Scale frames are ephemeral and latest-wins. A dropped bridge frame must not surface as
          // a settings connection error; briefly retry the newest value and let the final full
          // appearance preview remain the reliable commit path.
          if (transientFailures >= 2) break;
          transientFailures += 1;
          portraitScaleFrameQueued = true;
          await wait(16 * transientFailures);
        }
      }
    } finally {
      portraitScaleFrameQueued = false;
      portraitScaleFrameRunning = false;
    }
  }

  function schedulePortraitScaleFrame(portraitScalePercent, context) {
    portraitScaleFramePercent = portraitScalePercent;
    portraitScaleFrameTrace = context;
    portraitScaleFrameQueued = true;
    trace.mark("portrait.raf-scheduled", context);
    if (portraitScaleFrame !== null || disposed || rebinding) return;
    portraitScaleFrame = window.requestAnimationFrame(() => {
      portraitScaleFrame = null;
      trace.mark("portrait.raf-callback", portraitScaleFrameTrace);
      if (!portraitScaleFrameRunning) portraitScaleFrameDrainPromise = drainPortraitScaleFrames();
    });
  }

  async function flushPortraitScaleFrames() {
    cancelPortraitScaleFrame();
    if (!portraitScaleFrameRunning && portraitScaleFrameQueued) {
      portraitScaleFrameDrainPromise = drainPortraitScaleFrames();
    }
    await portraitScaleFrameDrainPromise;
  }

  function currentLayoutFrameValues() {
    return Object.freeze(Object.fromEntries(LAYOUT_FIELDS.map((field) => [field, draft[field]])));
  }

  function cancelLayoutFrame() {
    if (layoutFrame === null) return;
    window.cancelAnimationFrame(layoutFrame);
    layoutFrame = null;
  }

  async function drainLayoutFrames() {
    if (layoutFrameRunning || disposed) return;
    layoutFrameRunning = true;
    let transientFailures = 0;
    try {
      while (layoutFrameQueued && !disposed) {
        layoutFrameQueued = false;
        const values = layoutFrameValues;
        const context = layoutFrameTrace;
        await layoutGestureStartPromise;
        try {
          await tracedInvoke(
            "settings_character_appearance_layout_frame",
            { values },
            context,
            "layout.frame",
          );
          transientFailures = 0;
        } catch {
          if (transientFailures >= 2) break;
          transientFailures += 1;
          layoutFrameQueued = true;
          await wait(16 * transientFailures);
        }
      }
    } finally {
      layoutFrameQueued = false;
      layoutFrameRunning = false;
    }
  }

  function scheduleLayoutFrame(context) {
    layoutFrameValues = currentLayoutFrameValues();
    layoutFrameTrace = context;
    layoutFrameQueued = true;
    trace.mark("layout.raf-scheduled", context);
    if (layoutFrame !== null || disposed || rebinding) return;
    layoutFrame = window.requestAnimationFrame(() => {
      layoutFrame = null;
      trace.mark("layout.raf-callback", layoutFrameTrace);
      if (!layoutFrameRunning) layoutFrameDrainPromise = drainLayoutFrames();
    });
  }

  async function flushLayoutFrames() {
    cancelLayoutFrame();
    if (!layoutFrameRunning && layoutFrameQueued) layoutFrameDrainPromise = drainLayoutFrames();
    await layoutFrameDrainPromise;
  }

  function schedulePreview() {
    if (previewFrame !== null || disposed || rebinding) return;
    trace.mark("appearance.raf-scheduled", previewTrace);
    previewFrame = window.requestAnimationFrame(() => {
      previewFrame = null;
      trace.mark("appearance.raf-callback", previewTrace);
      if (!previewRunning) previewDrainPromise = drainPreview();
    });
  }

  function beginPortraitScaleGesture(event = undefined) {
    if (portraitScaleGestureActive || disposed || rebinding) return;
    portraitScaleGestureActive = true;
    portraitScaleGestureRevision = 0;
    portraitScaleGestureTrace = trace.createGesture("portrait-scale");
    trace.mark(
      event?.type === "pointerdown" ? "portrait.pointerdown" : "portrait.keydown",
      portraitScaleGestureTrace,
      { event },
    );
    if (portraitScaleGestureBackendActive) return;
    portraitScaleGestureBackendActive = true;
    const start = portraitScaleGestureTransition.then(() => tracedInvoke(
      "settings_character_appearance_scale_gesture",
      { active: true },
      portraitScaleGestureTrace,
      "portrait.gesture-start",
    ));
    portraitScaleGestureTransition = start.catch(() => {});
    portraitScaleGestureStartPromise = start.catch((error) => {
      portraitScaleGestureBackendActive = false;
      portraitScaleGestureActive = false;
      if (!rebinding) onError(String(error));
    });
  }

  async function flushPreview() {
    cancelPreviewFrame();
    if (!previewRunning && previewQueued) previewDrainPromise = drainPreview();
    await previewDrainPromise;
  }

  async function endPortraitScaleGesture(event = undefined) {
    if (!portraitScaleGestureActive) return;
    const context = trace.atRevision(portraitScaleGestureTrace, portraitScaleGestureRevision)
      || portraitScaleGestureTrace;
    trace.mark(
      event?.type === "pointerup" ? "portrait.pointerup" : "portrait.gesture-end",
      context,
      { event },
    );
    portraitScaleGestureActive = false;
    await portraitScaleGestureStartPromise;
    await flushPortraitScaleFrames();
    await flushPreview();
    // A new pointer/key gesture may begin while the old preview queue is draining. Keep the
    // backend guard open across that overlap; closing it here would expose one unguarded tick.
    if (portraitScaleGestureActive || !portraitScaleGestureBackendActive) return;
    portraitScaleGestureBackendActive = false;
    const stop = portraitScaleGestureTransition.then(() => tracedInvoke(
      "settings_character_appearance_scale_gesture",
      { active: false },
      context,
      "portrait.gesture-stop",
    ));
    portraitScaleGestureTransition = stop.catch(() => {});
    await stop;
    void trace.flush();
  }

  function finishPortraitScaleGesture(event = undefined) {
    return endPortraitScaleGesture(event).catch((error) => {
      if (!rebinding) onError(String(error));
    });
  }

  function beginLayoutGesture(event = undefined) {
    if (layoutGestureActive || disposed || rebinding) return;
    layoutGestureActive = true;
    layoutGestureRevision = 0;
    const field = Object.entries(scalarControls)
      .find(([, inputId]) => inputId === event?.currentTarget?.id)?.[0];
    layoutGestureTrace = trace.createGesture(LAYOUT_TRACE_KINDS[field] || "layout");
    trace.mark(
      event?.type === "pointerdown" ? "layout.pointerdown" : "layout.keydown",
      layoutGestureTrace,
      { event },
    );
    if (layoutGestureBackendActive) return;
    layoutGestureBackendActive = true;
    const start = layoutGestureTransition.then(() => tracedInvoke(
      "settings_character_appearance_layout_gesture",
      { active: true },
      layoutGestureTrace,
      "layout.gesture-start",
    ));
    layoutGestureTransition = start.catch(() => {});
    layoutGestureStartPromise = start.catch((error) => {
      layoutGestureBackendActive = false;
      layoutGestureActive = false;
      if (!rebinding) onError(String(error));
    });
  }

  async function endLayoutGesture(event = undefined) {
    if (!layoutGestureActive) return;
    const context = trace.atRevision(layoutGestureTrace, layoutGestureRevision) || layoutGestureTrace;
    trace.mark(
      event?.type === "pointerup" ? "layout.pointerup" : "layout.gesture-end",
      context,
      { event },
    );
    layoutGestureActive = false;
    await layoutGestureStartPromise;
    await flushLayoutFrames();
    await flushPreview();
    if (layoutGestureActive || !layoutGestureBackendActive) return;
    layoutGestureBackendActive = false;
    const stop = layoutGestureTransition.then(() => tracedInvoke(
      "settings_character_appearance_layout_gesture",
      { active: false },
      context,
      "layout.gesture-stop",
    ));
    layoutGestureTransition = stop.catch(() => {});
    await stop;
    void trace.flush();
  }

  function finishLayoutGesture(event = undefined) {
    return endLayoutGesture(event).catch((error) => {
      if (!rebinding) onError(String(error));
    });
  }

  function changed(event = undefined, field = undefined) {
    try {
      let context = null;
      let tracePrefix = null;
      if (field === "portraitScalePercent" && portraitScaleGestureActive) {
        portraitScaleGestureRevision += 1;
        context = trace.atRevision(portraitScaleGestureTrace, portraitScaleGestureRevision);
        tracePrefix = "portrait";
      } else if (LAYOUT_FIELDS.includes(field) && layoutGestureActive) {
        layoutGestureRevision += 1;
        context = trace.atRevision(layoutGestureTrace, layoutGestureRevision);
        tracePrefix = "layout";
      }
      if (tracePrefix) trace.mark(`${tracePrefix}.input`, context, { event });
      draft = read();
      previewQueued = true;
      previewTrace = context;
      onDirty();
      if (tracePrefix) trace.mark(`${tracePrefix}.value-committed`, context);
      if (field === "portraitScalePercent" && portraitScaleGestureActive) {
        schedulePortraitScaleFrame(draft.portraitScalePercent, context);
        return;
      }
      if (LAYOUT_FIELDS.includes(field) && layoutGestureActive) {
        scheduleLayoutFrame(context);
        return;
      }
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
      portraitScaleFrameQueued = false;
      layoutFrameQueued = false;
      cancelPortraitScaleFrame();
      cancelLayoutFrame();
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
    for (const [field, inputId] of Object.entries(scalarControls)) {
      document.getElementById(inputId).addEventListener("input", (event) => changed(event, field));
    }
    const portraitScale = document.getElementById("portraitScale");
    portraitScale.addEventListener("pointerdown", beginPortraitScaleGesture);
    for (const eventName of ["pointerup", "pointercancel", "lostpointercapture", "blur"]) {
      portraitScale.addEventListener(eventName, finishPortraitScaleGesture);
    }
    window.addEventListener?.("blur", finishPortraitScaleGesture);
    portraitScale.addEventListener("keydown", (event) => {
      if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "PageUp", "PageDown"]
        .includes(event.key)) beginPortraitScaleGesture(event);
    });
    portraitScale.addEventListener("keyup", finishPortraitScaleGesture);
    for (const field of LAYOUT_FIELDS) {
      const control = document.getElementById(scalarControls[field]);
      control.addEventListener("pointerdown", beginLayoutGesture);
      for (const eventName of ["pointerup", "pointercancel", "lostpointercapture", "blur"]) {
        control.addEventListener(eventName, finishLayoutGesture);
      }
      control.addEventListener("keydown", (event) => {
        if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "PageUp", "PageDown"]
          .includes(event.key)) beginLayoutGesture(event);
      });
      control.addEventListener("keyup", finishLayoutGesture);
    }
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
      await endLayoutGesture();
      draft = read();
      previewQueued = false;
      cancelPreviewFrame();
      portraitScaleFrameQueued = false;
      cancelPortraitScaleFrame();
      layoutFrameQueued = false;
      cancelLayoutFrame();
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
      await endLayoutGesture();
      previewQueued = false;
      cancelPreviewFrame();
      portraitScaleFrameQueued = false;
      cancelPortraitScaleFrame();
      layoutFrameQueued = false;
      cancelLayoutFrame();
      await previewDrainPromise;
      await invoke("settings_character_appearance_cancel_preview");
      if (baseline) {
        draft = clone(baseline);
        fill(draft);
      }
      onDirty();
    },
    dispose() {
      if (portraitScaleGestureBackendActive) {
        portraitScaleGestureActive = false;
        portraitScaleGestureBackendActive = false;
        void portraitScaleGestureTransition
          .then(() => invoke("settings_character_appearance_scale_gesture", { active: false }))
          .catch(() => {});
      }
      if (layoutGestureBackendActive) {
        layoutGestureActive = false;
        layoutGestureBackendActive = false;
        void layoutGestureTransition
          .then(() => invoke("settings_character_appearance_layout_gesture", { active: false }))
          .catch(() => {});
      }
      disposed = true;
      previewQueued = false;
      portraitScaleFrameQueued = false;
      layoutFrameQueued = false;
      cancelPreviewFrame();
      cancelPortraitScaleFrame();
      cancelLayoutFrame();
      window.clearInterval(generationTimer);
      rebindPromise = null;
      rebinding = false;
    },
  });
}
