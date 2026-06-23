const invoke = window.__TAURI__.core.invoke;

const fields = {
  characterSelect: document.getElementById("characterSelect"),
  portraitScale: document.getElementById("portraitScale"),
  controlPanelWidth: document.getElementById("controlPanelWidth"),
  bubbleHeight: document.getElementById("bubbleHeight"),
  controlPanelOffset: document.getElementById("controlPanelOffset"),
  inputBarOffset: document.getElementById("inputBarOffset"),
  enabled: document.getElementById("enabled"),
  checkInterval: document.getElementById("checkInterval"),
  cooldown: document.getElementById("cooldown"),
  batchLimit: document.getElementById("batchLimit"),
  windowsMcp: document.getElementById("windowsMcp"),
  agentSteps: document.getElementById("agentSteps"),
  toolCallsPerStep: document.getElementById("toolCallsPerStep"),
  toolCallsPerTurn: document.getElementById("toolCallsPerTurn"),
  apiProfiles: document.getElementById("apiProfiles"),
  addApiProfileButton: document.getElementById("addApiProfileButton"),
  modelSlots: document.getElementById("modelSlots"),
  apiTimeout: document.getElementById("apiTimeout"),
  apiTemperature: document.getElementById("apiTemperature"),
  apiTopPEnabled: document.getElementById("apiTopPEnabled"),
  apiTopP: document.getElementById("apiTopP"),
  apiMaxTokensEnabled: document.getElementById("apiMaxTokensEnabled"),
  apiMaxTokens: document.getElementById("apiMaxTokens"),
  ttsEnabled: document.getElementById("ttsEnabled"),
  ttsProvider: document.getElementById("ttsProvider"),
  ttsApiUrl: document.getElementById("ttsApiUrl"),
  ttsWorkDir: document.getElementById("ttsWorkDir"),
  ttsPythonPath: document.getElementById("ttsPythonPath"),
  ttsConfigPath: document.getElementById("ttsConfigPath"),
  ttsTimeout: document.getElementById("ttsTimeout"),
  themeColors: document.getElementById("themeColors"),
  visualEffectMode: document.getElementById("visualEffectMode"),
  resetThemeButton: document.getElementById("resetThemeButton"),
  launchAtLogin: document.getElementById("launchAtLogin"),
  debugLogEnabled: document.getElementById("debugLogEnabled"),
  debugBodyEnabled: document.getElementById("debugBodyEnabled"),
  debugFileEnabled: document.getElementById("debugFileEnabled"),
  stageDebugOverlay: document.getElementById("stageDebugOverlay"),
  stageCollisionMask: document.getElementById("stageCollisionMask"),
  subtitleTypingInterval: document.getElementById("subtitleTypingInterval"),
  replySegmentPause: document.getElementById("replySegmentPause"),
  bubbleAutoHide: document.getElementById("bubbleAutoHide"),
  bubbleAutoHideDelay: document.getElementById("bubbleAutoHideDelay"),
  backchannelEnabled: document.getElementById("backchannelEnabled"),
  backchannelMode: document.getElementById("backchannelMode"),
  backchannelDelay: document.getElementById("backchannelDelay"),
  backchannelProbability: document.getElementById("backchannelProbability"),
  backchannelTtsEnabled: document.getElementById("backchannelTtsEnabled"),
  memoryCurationEnabled: document.getElementById("memoryCurationEnabled"),
  memoryTriggerTurns: document.getElementById("memoryTriggerTurns"),
  tokenEstimate: document.getElementById("tokenEstimate"),
  errorText: document.getElementById("errorText"),
  saveButton: document.getElementById("saveButton"),
  cancelButton: document.getElementById("cancelButton"),
  pageTitle: document.getElementById("pageTitle"),
  pageSubtitle: document.getElementById("pageSubtitle"),
  navItems: Array.from(document.querySelectorAll(".nav-item[data-page]")),
  pages: {
    character: document.getElementById("page-character"),
    privacy: document.getElementById("page-privacy"),
    appearance: document.getElementById("page-appearance"),
    model: document.getElementById("page-model"),
    voice: document.getElementById("page-voice"),
    tools: document.getElementById("page-tools"),
    system: document.getElementById("page-system"),
    memory: document.getElementById("page-memory"),
  },
};

let request = null;
let themeChanged = false;

const themeVars = {
  primary_color: "--sakura-primary",
  primary_hover_color: "--sakura-primary-hover",
  accent_color: "--sakura-accent",
  text_color: "--sakura-text",
  secondary_text_color: "--sakura-secondary-text",
  muted_text_color: "--sakura-muted-text",
  page_background_color: "--sakura-page-bg",
  panel_background_color: "--sakura-panel-bg",
  input_background_color: "--sakura-input-bg",
  bubble_background_color: "--sakura-bubble-bg",
  border_color: "--sakura-border",
};

function setError(message) {
  fields.errorText.textContent = message || "";
}

function isHexColor(value) {
  return typeof value === "string" && /^#[0-9a-fA-F]{6}$/.test(value);
}

function applyTheme(theme) {
  const style = document.documentElement.style;
  Object.entries(themeVars).forEach(([key, cssVar]) => {
    const value = theme?.[key];
    if (isHexColor(value)) {
      style.setProperty(cssVar, value);
    }
  });
}

function markThemeChanged() {
  themeChanged = true;
  applyTheme(collectThemeSettings());
}

// 自定义下拉框：WebView2 在 Windows 上的原生 <select> 弹层无法被 CSS 主题化，
// 这里保留原生 <select>（隐藏）承载取值与 change 事件，只把视觉换成可控弹层。
// 弹层用 position:fixed + getBoundingClientRect 定位，避开 .page-scroll 的 overflow 裁剪。
function enhanceSelect(select) {
  if (!select || select.__customSelect) {
    return;
  }
  const wrapper = document.createElement("div");
  wrapper.className = "custom-select";
  const trigger = document.createElement("button");
  trigger.type = "button";
  trigger.className = "custom-select__trigger";
  const label = document.createElement("span");
  label.className = "custom-select__label";
  const caret = document.createElement("span");
  caret.className = "custom-select__caret";
  caret.setAttribute("aria-hidden", "true");
  trigger.append(label, caret);
  const menu = document.createElement("div");
  menu.className = "custom-select__menu";
  menu.setAttribute("role", "listbox");

  select.parentNode.insertBefore(wrapper, select);
  // menu 不挂在 wrapper 内：打开时才挂到 <body>（见 openMenu），避免被祖先的
  // transform 包含块推偏定位。
  wrapper.append(trigger, select);

  function syncTrigger() {
    const option = select.options[select.selectedIndex];
    label.textContent = option ? option.textContent : "";
    trigger.disabled = select.disabled;
  }

  function buildMenu() {
    menu.textContent = "";
    Array.from(select.options).forEach((option) => {
      const item = document.createElement("div");
      item.className = "custom-select__option";
      item.setAttribute("role", "option");
      item.textContent = option.textContent;
      if (option.value === select.value) {
        item.classList.add("is-selected");
        item.setAttribute("aria-selected", "true");
      }
      item.addEventListener("click", () => {
        if (select.value !== option.value) {
          select.value = option.value;
          select.dispatchEvent(new Event("change", { bubbles: true }));
        }
        syncTrigger();
        closeMenu();
      });
      menu.append(item);
    });
  }

  // 弹层挂在 <body> 上，按视口坐标定位；下方空间不足且上方更宽裕时向上弹出。
  function positionMenu() {
    const rect = trigger.getBoundingClientRect();
    menu.style.width = `${rect.width}px`;
    menu.style.left = `${rect.left}px`;
    const menuHeight = menu.offsetHeight;
    const spaceBelow = window.innerHeight - rect.bottom;
    if (spaceBelow < menuHeight + 12 && rect.top > spaceBelow) {
      menu.style.top = `${Math.max(8, rect.top - 6 - menuHeight)}px`;
    } else {
      menu.style.top = `${rect.bottom + 6}px`;
    }
  }

  function onDocPointer(event) {
    if (!wrapper.contains(event.target) && !menu.contains(event.target)) {
      closeMenu();
    }
  }
  function onKey(event) {
    if (event.key === "Escape") {
      closeMenu();
    }
  }
  function openMenu() {
    if (select.disabled) {
      return;
    }
    buildMenu();
    document.body.appendChild(menu);
    menu.classList.add("is-open");
    positionMenu();
    wrapper.classList.add("is-open");
    document.addEventListener("pointerdown", onDocPointer, true);
    document.addEventListener("keydown", onKey, true);
    window.addEventListener("scroll", closeMenu, true);
    window.addEventListener("resize", closeMenu, true);
  }
  function closeMenu() {
    wrapper.classList.remove("is-open");
    menu.classList.remove("is-open");
    menu.remove();
    document.removeEventListener("pointerdown", onDocPointer, true);
    document.removeEventListener("keydown", onKey, true);
    window.removeEventListener("scroll", closeMenu, true);
    window.removeEventListener("resize", closeMenu, true);
  }

  trigger.addEventListener("click", () => {
    wrapper.classList.contains("is-open") ? closeMenu() : openMenu();
  });
  select.addEventListener("change", syncTrigger);

  select.__customSelect = { refresh: syncTrigger };
  syncTrigger();
}

function refreshSelect(select) {
  if (select && select.__customSelect) {
    select.__customSelect.refresh();
  }
}

function setNumericBounds(input, bounds) {
  input.min = String(bounds[0]);
  input.max = String(bounds[1]);
}

function clampInt(value, bounds) {
  const number = Number.parseInt(value, 10);
  if (!Number.isFinite(number)) {
    return bounds[0];
  }
  return Math.min(bounds[1], Math.max(bounds[0], number));
}

function clampFloat(value, bounds) {
  const number = Number.parseFloat(value);
  if (!Number.isFinite(number)) {
    return bounds[0];
  }
  return Math.min(bounds[1], Math.max(bounds[0], number));
}

function normalizeColorText(value, fallback) {
  const text = String(value || "").trim();
  const prefixed = text.startsWith("#") ? text : `#${text}`;
  return isHexColor(prefixed) ? prefixed.toLowerCase() : fallback;
}

const pageMeta = {
  character: { title: "角色", subtitle: "选择陪伴角色与立绘布局" },
  appearance: { title: "外观", subtitle: "配色与输入栏视觉效果" },
  model: { title: "模型", subtitle: "供应商、模型槽位与高级参数" },
  voice: { title: "语音", subtitle: "TTS 提供器与语音参数" },
  privacy: { title: "隐私", subtitle: "主动屏幕感知与截图预算" },
  tools: { title: "工具", subtitle: "桌面控制与工具循环上限" },
  system: { title: "系统", subtitle: "启动、日志、字幕、气泡与接话" },
  memory: { title: "记忆", subtitle: "记忆自动整理" },
};

function showPage(page) {
  Object.entries(fields.pages).forEach(([key, element]) => {
    element.classList.toggle("is-active", key === page);
  });
  fields.navItems.forEach((item) => {
    item.classList.toggle("is-active", item.dataset.page === page);
  });
  const meta = pageMeta[page];
  if (meta) {
    fields.pageTitle.textContent = meta.title;
    fields.pageSubtitle.textContent = meta.subtitle;
  }
}

function syncEnabledState() {
  const enabled = fields.enabled.checked;
  fields.checkInterval.disabled = !enabled;
  fields.cooldown.disabled = !enabled;
  fields.batchLimit.disabled = !enabled;
}

function syncRuntimeLoopState() {
  if (!request) {
    return;
  }
  const perStep = clampInt(fields.toolCallsPerStep.value, request.limits.max_tool_calls_per_step);
  fields.toolCallsPerTurn.min = String(perStep);
}

function syncDebugLogState() {
  fields.debugBodyEnabled.disabled = !fields.debugLogEnabled.checked;
}

function syncBubbleState() {
  fields.bubbleAutoHideDelay.disabled = !fields.bubbleAutoHide.checked;
}

function selectedCharacter() {
  const id = fields.characterSelect.value;
  return request.character.characters.find((item) => item.id === id) || null;
}

// 切换角色时跟随载入该角色自带的配色（仅配色，输入栏视觉效果等用户级偏好保留）。
function applySelectedCharacterTheme() {
  const character = selectedCharacter();
  if (!character || !character.theme) {
    return;
  }
  request.theme_fields.forEach(({ id }) => {
    const textInput = fields.themeColors.querySelector(`[data-theme-field="${id}"]`);
    const colorInput = fields.themeColors.querySelector(`[data-theme-swatch="${id}"]`);
    if (!textInput || !colorInput) {
      return;
    }
    const color = normalizeColorText(character.theme[id], request.theme_defaults[id]);
    textInput.value = color;
    colorInput.value = color;
  });
  markThemeChanged();
}

function syncTtsState() {
  const character = selectedCharacter();
  const hasVoice = character ? Boolean(character.has_voice) : true;
  if (!hasVoice) {
    fields.ttsEnabled.checked = false;
  }
  fields.ttsEnabled.disabled = !hasVoice;
  const active = fields.ttsEnabled.checked && fields.ttsProvider.value !== "none";
  [fields.ttsApiUrl, fields.ttsWorkDir, fields.ttsTimeout].forEach((input) => {
    input.disabled = !active;
  });
  const customProvider = fields.ttsProvider.value === "custom-gpt-sovits";
  fields.ttsPythonPath.disabled = !active || !customProvider;
  fields.ttsConfigPath.disabled = !active || !customProvider;
}

function syncApiAdvancedState() {
  fields.apiTopP.disabled = !fields.apiTopPEnabled.checked;
  fields.apiMaxTokens.disabled = !fields.apiMaxTokensEnabled.checked;
}

function renderCharacters() {
  fields.characterSelect.textContent = "";
  request.character.characters.forEach((character) => {
    const option = document.createElement("option");
    option.value = character.id;
    option.textContent = character.display_name || character.id;
    fields.characterSelect.append(option);
  });
  fields.characterSelect.value = request.character.current_character_id;
}

function renderThemeControls() {
  fields.themeColors.textContent = "";
  request.theme_fields.forEach(({ id, label }) => {
    const row = document.createElement("div");
    row.className = "form-row";
    const rowLabel = document.createElement("label");
    rowLabel.htmlFor = `theme-${id}`;
    rowLabel.textContent = label;
    const controls = document.createElement("div");
    controls.className = "theme-color-control";
    const textInput = document.createElement("input");
    textInput.id = `theme-${id}`;
    textInput.type = "text";
    textInput.maxLength = 7;
    textInput.placeholder = "#RRGGBB";
    textInput.dataset.themeField = id;
    const colorInput = document.createElement("input");
    colorInput.type = "color";
    colorInput.dataset.themeSwatch = id;
    textInput.addEventListener("input", () => {
      const fallback = request.theme_defaults[id];
      const normalized = normalizeColorText(textInput.value, fallback);
      if (isHexColor(normalized)) {
        colorInput.value = normalized;
      }
      markThemeChanged();
    });
    colorInput.addEventListener("input", () => {
      textInput.value = colorInput.value;
      markThemeChanged();
    });
    controls.append(textInput, colorInput);
    row.append(rowLabel, controls);
    fields.themeColors.append(row);
  });
  fields.visualEffectMode.textContent = "";
  const currentMode = request.theme.visual_effect_mode;
  const modes = [...request.visual_effect_modes];
  if (!modes.some((mode) => mode.id === currentMode)) {
    modes.push({ id: currentMode, label: currentMode });
  }
  modes.forEach((mode) => {
    const option = document.createElement("option");
    option.value = mode.id;
    option.textContent = mode.label;
    fields.visualEffectMode.append(option);
  });
}

function setThemeValues(theme) {
  request.theme_fields.forEach(({ id }) => {
    const textInput = fields.themeColors.querySelector(`[data-theme-field="${id}"]`);
    const colorInput = fields.themeColors.querySelector(`[data-theme-swatch="${id}"]`);
    const color = normalizeColorText(theme[id], request.theme_defaults[id]);
    textInput.value = color;
    colorInput.value = color;
  });
  fields.visualEffectMode.value = theme.visual_effect_mode;
  refreshSelect(fields.visualEffectMode);
  applyTheme(theme);
}

function makeProfileId() {
  if (window.crypto?.randomUUID) {
    return window.crypto.randomUUID();
  }
  return `profile-${Date.now()}`;
}

function textRow(labelText, field, value) {
  const row = document.createElement("div");
  row.className = "form-row";
  const label = document.createElement("label");
  label.textContent = labelText;
  const input = document.createElement("input");
  input.type = "text";
  input.className = "wide-input";
  input.dataset.profileField = field;
  input.value = value;
  row.append(label, input);
  return row;
}

function textareaRow(labelText, field, value) {
  const row = document.createElement("div");
  row.className = "form-row align-start";
  const label = document.createElement("label");
  label.textContent = labelText;
  const textarea = document.createElement("textarea");
  textarea.dataset.profileField = field;
  textarea.value = value;
  row.append(label, textarea);
  return row;
}

function renderApiProfiles(profiles) {
  fields.apiProfiles.textContent = "";
  profiles.forEach((profile) => {
    const group = document.createElement("fieldset");
    group.className = "nested-group";
    group.dataset.profileId = profile.id;
    const legend = document.createElement("legend");
    legend.textContent = profile.alias || profile.id;
    const alias = textRow("名称", "alias", profile.alias || profile.id);
    const baseUrl = textRow("Base URL", "base_url", profile.base_url || "");
    const apiKey = textRow("API Key", "api_key", profile.api_key || "");
    const models = textareaRow("模型列表", "models", (profile.models || []).join("\n"));
    const actions = document.createElement("div");
    actions.className = "form-row";
    const label = document.createElement("label");
    label.textContent = "供应商";
    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.className = "secondary-button";
    removeButton.textContent = "删除";
    removeButton.addEventListener("click", () => {
      if (fields.apiProfiles.querySelectorAll(".nested-group").length <= 1) {
        return;
      }
      group.remove();
      refreshModelSlots();
    });
    actions.append(label, removeButton);
    [alias, baseUrl, apiKey, models].forEach((row) => {
      const input = row.querySelector("[data-profile-field]");
      input.addEventListener("input", () => {
        legend.textContent = alias.querySelector("input").value.trim() || profile.id;
        if (input.dataset.profileField === "models") {
          refreshModelSlots();
        }
      });
    });
    group.append(legend, alias, baseUrl, apiKey, models, actions);
    fields.apiProfiles.append(group);
  });
}

function apiProfilesFromDom() {
  return Array.from(fields.apiProfiles.querySelectorAll(".nested-group")).map((group) => {
    const value = (field) => group.querySelector(`[data-profile-field="${field}"]`).value.trim();
    return {
      id: group.dataset.profileId,
      alias: value("alias") || group.dataset.profileId,
      base_url: value("base_url"),
      api_key: value("api_key"),
      models: value("models")
        .split(/\r?\n|,/)
        .map((item) => item.trim())
        .filter(Boolean),
    };
  });
}

function renderModelSlots(selection) {
  fields.modelSlots.textContent = "";
  request.api.slot_fields.forEach((slot) => {
    const row = document.createElement("div");
    row.className = "form-row model-slot-row";
    row.dataset.slot = slot.id;
    const label = document.createElement("label");
    label.textContent = slot.label;
    const controls = document.createElement("div");
    controls.className = "slot-controls";
    const profileSelect = document.createElement("select");
    profileSelect.dataset.slotProfile = slot.id;
    const modelSelect = document.createElement("select");
    modelSelect.dataset.slotModel = slot.id;
    controls.append(profileSelect, modelSelect);
    row.append(label, controls);
    fields.modelSlots.append(row);
    enhanceSelect(profileSelect);
    enhanceSelect(modelSelect);
    profileSelect.addEventListener("change", () => syncModelOptions(slot.id));
    const selected = selection?.slots?.[slot.id] || { profile_id: "", model: "" };
    fillProfileOptions(profileSelect, selected.profile_id, slot.required);
    syncModelOptions(slot.id, selected.model);
  });
}

function fillProfileOptions(select, selectedId, required) {
  const profiles = apiProfilesFromDom();
  select.textContent = "";
  if (!required) {
    const inherit = document.createElement("option");
    inherit.value = "";
    inherit.textContent = "继承";
    select.append(inherit);
  }
  profiles.forEach((profile) => {
    const option = document.createElement("option");
    option.value = profile.id;
    option.textContent = profile.alias || profile.id;
    select.append(option);
  });
  select.value = selectedId || (required && profiles[0] ? profiles[0].id : "");
  refreshSelect(select);
}

function syncModelOptions(slot, selectedModel) {
  const profileSelect = fields.modelSlots.querySelector(`[data-slot-profile="${slot}"]`);
  const modelSelect = fields.modelSlots.querySelector(`[data-slot-model="${slot}"]`);
  const profile = apiProfilesFromDom().find((item) => item.id === profileSelect.value);
  const models = profile?.models || [];
  const current = selectedModel ?? modelSelect.value;
  modelSelect.textContent = "";
  if (!profileSelect.value) {
    const inherit = document.createElement("option");
    inherit.value = "";
    inherit.textContent = "继承";
    modelSelect.append(inherit);
    refreshSelect(modelSelect);
    return;
  }
  models.forEach((model) => {
    const option = document.createElement("option");
    option.value = model;
    option.textContent = model;
    modelSelect.append(option);
  });
  if (current && !models.includes(current)) {
    const option = document.createElement("option");
    option.value = current;
    option.textContent = current;
    modelSelect.append(option);
  }
  modelSelect.value = current || models[0] || "";
  refreshSelect(modelSelect);
}

function refreshModelSlots() {
  renderModelSlots(collectModelSelection());
}

function collectModelSelection() {
  const slots = {};
  request.api.slot_fields.forEach((slot) => {
    slots[slot.id] = {
      profile_id: fields.modelSlots.querySelector(`[data-slot-profile="${slot.id}"]`)?.value || "",
      model: fields.modelSlots.querySelector(`[data-slot-model="${slot.id}"]`)?.value || "",
    };
  });
  return { slots };
}

function renderTtsProviders() {
  fields.ttsProvider.textContent = "";
  request.tts.providers.forEach((provider) => {
    const option = document.createElement("option");
    option.value = provider.id;
    option.textContent = provider.label;
    fields.ttsProvider.append(option);
  });
}

function collectCharacterSettings() {
  const limits = request.limits;
  return {
    current_character_id: fields.characterSelect.value,
    layout: {
      portrait_scale_percent: clampInt(fields.portraitScale.value, limits.portrait_scale_percent),
      control_panel_width: clampInt(fields.controlPanelWidth.value, limits.control_panel_width),
      bubble_height: clampInt(fields.bubbleHeight.value, limits.bubble_height),
      control_panel_vertical_offset: clampInt(
        fields.controlPanelOffset.value,
        limits.control_panel_vertical_offset,
      ),
      input_bar_offset: clampInt(fields.inputBarOffset.value, limits.input_bar_offset),
    },
  };
}

// 角色页的布局滑块：拖动时把数值实时回写到桌宠（preview_layout），保存时才落盘。
const layoutSliders = [
  "portraitScale",
  "controlPanelWidth",
  "bubbleHeight",
  "controlPanelOffset",
  "inputBarOffset",
];

function updateSliderOutput(fieldKey) {
  const input = fields[fieldKey];
  const output = input?.parentElement?.querySelector(".slider-value");
  if (output) {
    output.textContent = input.value;
  }
}

let layoutPreviewPending = false;
function requestLayoutPreview() {
  if (!request || layoutPreviewPending) {
    return;
  }
  layoutPreviewPending = true;
  requestAnimationFrame(async () => {
    layoutPreviewPending = false;
    try {
      await invoke("preview_layout", { layout: collectCharacterSettings().layout });
    } catch (error) {
      // 实时预览失败不应打断编辑
    }
  });
}

function collectScreenAwarenessSettings() {
  const limits = request.limits;
  const enabled = fields.enabled.checked;
  return {
    enabled,
    screen_context_enabled: enabled,
    check_interval_minutes: clampInt(fields.checkInterval.value, limits.check_interval_minutes),
    cooldown_minutes: clampInt(fields.cooldown.value, limits.cooldown_minutes),
    screen_context_batch_limit: clampInt(fields.batchLimit.value, limits.screen_context_batch_limit),
  };
}

function collectRuntimeLoopSettings() {
  const limits = request.limits;
  const perStep = clampInt(fields.toolCallsPerStep.value, limits.max_tool_calls_per_step);
  const perTurn = clampInt(fields.toolCallsPerTurn.value, limits.max_tool_calls_per_turn);
  return {
    max_agent_steps_per_turn: clampInt(fields.agentSteps.value, limits.max_agent_steps_per_turn),
    max_tool_calls_per_step: perStep,
    max_tool_calls_per_turn: Math.max(perStep, perTurn),
  };
}

function collectApiSettings() {
  const limits = request.limits;
  const temperature = clampFloat(fields.apiTemperature.value, limits.api_temperature);
  const initialTemperature = request.api.settings.temperature;
  return {
    settings: {
      timeout_seconds: clampInt(fields.apiTimeout.value, limits.api_timeout_seconds),
      temperature:
        initialTemperature === null && Math.abs(temperature - 0.8) < 0.005
          ? null
          : temperature,
      top_p: fields.apiTopPEnabled.checked
        ? clampFloat(fields.apiTopP.value, limits.api_top_p)
        : null,
      max_tokens: fields.apiMaxTokensEnabled.checked
        ? clampInt(fields.apiMaxTokens.value, limits.api_max_tokens)
        : null,
    },
    profiles: apiProfilesFromDom(),
    model_selection: collectModelSelection(),
  };
}

function collectTtsSettings() {
  const enabled = fields.ttsEnabled.checked && fields.ttsProvider.value !== "none";
  return {
    enabled,
    provider: enabled ? fields.ttsProvider.value : "none",
    api_url: fields.ttsApiUrl.value.trim(),
    work_dir: fields.ttsWorkDir.value.trim(),
    python_path: fields.ttsPythonPath.value.trim(),
    tts_config_path: fields.ttsConfigPath.value.trim(),
    timeout_seconds: clampInt(fields.ttsTimeout.value, request.limits.tts_timeout_seconds),
  };
}

function collectSystemBasicSettings() {
  const limits = request.limits;
  const debugLogEnabled = fields.debugLogEnabled.checked;
  return {
    debug_log: {
      enabled: debugLogEnabled,
      body_enabled: debugLogEnabled && fields.debugBodyEnabled.checked,
      file_enabled: fields.debugFileEnabled.checked,
      stage_debug_overlay: fields.stageDebugOverlay.checked,
      stage_collision_mask: fields.stageCollisionMask.checked,
    },
    ui: {
      subtitle_typing_interval_ms: clampInt(
        fields.subtitleTypingInterval.value,
        limits.subtitle_typing_interval_ms,
      ),
      reply_segment_pause_ms: clampInt(
        fields.replySegmentPause.value,
        limits.reply_segment_pause_ms,
      ),
    },
    bubble: {
      auto_hide_enabled: fields.bubbleAutoHide.checked,
      auto_hide_delay_seconds: clampInt(
        fields.bubbleAutoHideDelay.value,
        limits.bubble_auto_hide_delay_seconds,
      ),
    },
  };
}

function collectSystemExtraSettings() {
  return {
    startup: {
      launch_at_login: fields.launchAtLogin.checked,
      launch_at_login_supported: Boolean(request.system_extra.startup.launch_at_login_supported),
    },
    backchannel: {
      enabled: fields.backchannelEnabled.checked,
      mode: fields.backchannelMode.value,
      delay_ms: clampInt(fields.backchannelDelay.value, request.limits.backchannel_delay_ms),
      probability: clampFloat(
        fields.backchannelProbability.value,
        request.limits.backchannel_probability,
      ),
      tts_enabled: fields.backchannelTtsEnabled.checked,
      timeout_ms: request.system_extra.backchannel.timeout_ms,
    },
  };
}

function collectMemorySettings() {
  return {
    curation: {
      enabled: fields.memoryCurationEnabled.checked,
      trigger_turns: clampInt(fields.memoryTriggerTurns.value, request.limits.memory_trigger_turns),
      backfill_limit: request.memory.curation.backfill_limit,
    },
  };
}

function collectThemeSettings() {
  const theme = {};
  request.theme_fields.forEach(({ id }) => {
    const input = fields.themeColors.querySelector(`[data-theme-field="${id}"]`);
    theme[id] = input.value;
  });
  theme.ai_enabled = Boolean(request.theme.ai_enabled && !themeChanged);
  theme.visual_effect_mode = fields.visualEffectMode.value || request.theme.visual_effect_mode;
  return theme;
}

function collectSettings() {
  return {
    screen_awareness: collectScreenAwarenessSettings(),
    mcp: {
      windows_enabled: fields.windowsMcp.checked,
    },
    runtime_loop: collectRuntimeLoopSettings(),
    system_basic: collectSystemBasicSettings(),
    theme: collectThemeSettings(),
    character: collectCharacterSettings(),
    api: collectApiSettings(),
    tts: collectTtsSettings(),
    system_extra: collectSystemExtraSettings(),
    memory: collectMemorySettings(),
  };
}

async function load() {
  request = await invoke("load_request");
  applyTheme(request.theme);
  renderCharacters();
  renderThemeControls();
  renderApiProfiles(request.api.profiles);
  renderModelSlots(request.api.model_selection);
  renderTtsProviders();
  enhanceSelect(fields.characterSelect);
  enhanceSelect(fields.visualEffectMode);
  enhanceSelect(fields.ttsProvider);
  enhanceSelect(fields.backchannelMode);

  setNumericBounds(fields.checkInterval, request.limits.check_interval_minutes);
  setNumericBounds(fields.cooldown, request.limits.cooldown_minutes);
  setNumericBounds(fields.batchLimit, request.limits.screen_context_batch_limit);
  setNumericBounds(fields.agentSteps, request.limits.max_agent_steps_per_turn);
  setNumericBounds(fields.toolCallsPerStep, request.limits.max_tool_calls_per_step);
  setNumericBounds(fields.toolCallsPerTurn, request.limits.max_tool_calls_per_turn);
  setNumericBounds(fields.subtitleTypingInterval, request.limits.subtitle_typing_interval_ms);
  setNumericBounds(fields.replySegmentPause, request.limits.reply_segment_pause_ms);
  setNumericBounds(fields.bubbleAutoHideDelay, request.limits.bubble_auto_hide_delay_seconds);
  setNumericBounds(fields.portraitScale, request.limits.portrait_scale_percent);
  setNumericBounds(fields.controlPanelWidth, request.limits.control_panel_width);
  setNumericBounds(fields.bubbleHeight, request.limits.bubble_height);
  setNumericBounds(fields.controlPanelOffset, request.limits.control_panel_vertical_offset);
  setNumericBounds(fields.inputBarOffset, request.limits.input_bar_offset);
  setNumericBounds(fields.apiTimeout, request.limits.api_timeout_seconds);
  setNumericBounds(fields.apiMaxTokens, request.limits.api_max_tokens);
  setNumericBounds(fields.ttsTimeout, request.limits.tts_timeout_seconds);
  setNumericBounds(fields.backchannelDelay, request.limits.backchannel_delay_ms);
  setNumericBounds(fields.memoryTriggerTurns, request.limits.memory_trigger_turns);

  const layout = request.character.layout;
  fields.portraitScale.value = layout.portrait_scale_percent;
  fields.controlPanelWidth.value = layout.control_panel_width;
  fields.bubbleHeight.value = layout.bubble_height;
  fields.controlPanelOffset.value = layout.control_panel_vertical_offset;
  fields.inputBarOffset.value = layout.input_bar_offset;
  layoutSliders.forEach(updateSliderOutput);

  const settings = request.screen_awareness;
  fields.enabled.checked = settings.enabled && settings.screen_context_enabled;
  fields.checkInterval.value = settings.check_interval_minutes;
  fields.cooldown.value = settings.cooldown_minutes;
  fields.batchLimit.value = settings.screen_context_batch_limit;
  fields.windowsMcp.checked = request.mcp.windows_enabled;
  fields.agentSteps.value = request.runtime_loop.max_agent_steps_per_turn;
  fields.toolCallsPerStep.value = request.runtime_loop.max_tool_calls_per_step;
  fields.toolCallsPerTurn.value = request.runtime_loop.max_tool_calls_per_turn;

  fields.apiTimeout.value = request.api.settings.timeout_seconds;
  fields.apiTemperature.value = request.api.settings.temperature ?? 0.8;
  fields.apiTopPEnabled.checked = request.api.settings.top_p !== null;
  fields.apiTopP.value = request.api.settings.top_p ?? 1;
  fields.apiMaxTokensEnabled.checked = request.api.settings.max_tokens !== null;
  fields.apiMaxTokens.value = request.api.settings.max_tokens ?? 2048;

  fields.ttsEnabled.checked = request.tts.enabled;
  fields.ttsProvider.value = request.tts.provider;
  fields.ttsApiUrl.value = request.tts.api_url;
  fields.ttsWorkDir.value = request.tts.work_dir;
  fields.ttsPythonPath.value = request.tts.python_path;
  fields.ttsConfigPath.value = request.tts.tts_config_path;
  fields.ttsTimeout.value = request.tts.timeout_seconds;

  fields.launchAtLogin.checked = request.system_extra.startup.launch_at_login;
  fields.launchAtLogin.disabled = !request.system_extra.startup.launch_at_login_supported;
  fields.debugLogEnabled.checked = request.system_basic.debug_log.enabled;
  fields.debugBodyEnabled.checked = request.system_basic.debug_log.body_enabled;
  fields.debugFileEnabled.checked = request.system_basic.debug_log.file_enabled;
  fields.stageDebugOverlay.checked = request.system_basic.debug_log.stage_debug_overlay;
  fields.stageCollisionMask.checked = request.system_basic.debug_log.stage_collision_mask;
  fields.subtitleTypingInterval.value = request.system_basic.ui.subtitle_typing_interval_ms;
  fields.replySegmentPause.value = request.system_basic.ui.reply_segment_pause_ms;
  fields.bubbleAutoHide.checked = request.system_basic.bubble.auto_hide_enabled;
  fields.bubbleAutoHideDelay.value = request.system_basic.bubble.auto_hide_delay_seconds;
  fields.backchannelEnabled.checked = request.system_extra.backchannel.enabled;
  fields.backchannelMode.value = request.system_extra.backchannel.mode;
  fields.backchannelDelay.value = request.system_extra.backchannel.delay_ms;
  fields.backchannelProbability.value = request.system_extra.backchannel.probability;
  fields.backchannelTtsEnabled.checked = request.system_extra.backchannel.tts_enabled;
  fields.memoryCurationEnabled.checked = request.memory.curation.enabled;
  fields.memoryTriggerTurns.value = request.memory.curation.trigger_turns;

  setThemeValues(request.theme);
  themeChanged = false;
  fields.tokenEstimate.textContent =
    `按当前屏幕估算：约 ${request.estimated_tokens_per_image.toLocaleString("zh-CN")} tokens/张。`;
  syncEnabledState();
  syncRuntimeLoopState();
  syncDebugLogState();
  syncBubbleState();
  syncApiAdvancedState();
  syncTtsState();
  refreshSelect(fields.characterSelect);
  refreshSelect(fields.ttsProvider);
  refreshSelect(fields.backchannelMode);
}

fields.navItems.forEach((item) => {
  item.addEventListener("click", () => showPage(item.dataset.page));
});
layoutSliders.forEach((fieldKey) => {
  fields[fieldKey].addEventListener("input", () => {
    updateSliderOutput(fieldKey);
    requestLayoutPreview();
  });
});
fields.characterSelect.addEventListener("change", syncTtsState);
fields.characterSelect.addEventListener("change", applySelectedCharacterTheme);
fields.enabled.addEventListener("change", syncEnabledState);
fields.toolCallsPerStep.addEventListener("input", syncRuntimeLoopState);
fields.addApiProfileButton.addEventListener("click", () => {
  const profile = {
    id: makeProfileId(),
    alias: "新供应商",
    base_url: "https://api.openai.com/v1",
    api_key: "",
    models: ["gpt-4.1-mini"],
  };
  renderApiProfiles([...apiProfilesFromDom(), profile]);
  refreshModelSlots();
});
fields.apiTopPEnabled.addEventListener("change", syncApiAdvancedState);
fields.apiMaxTokensEnabled.addEventListener("change", syncApiAdvancedState);
fields.ttsEnabled.addEventListener("change", syncTtsState);
fields.ttsProvider.addEventListener("change", syncTtsState);
fields.visualEffectMode.addEventListener("change", markThemeChanged);
fields.resetThemeButton.addEventListener("click", () => {
  setThemeValues(request.theme_defaults);
  themeChanged = true;
});
fields.debugLogEnabled.addEventListener("change", syncDebugLogState);
fields.bubbleAutoHide.addEventListener("change", syncBubbleState);
fields.saveButton.addEventListener("click", async () => {
  if (!request) {
    return;
  }
  setError("");
  try {
    await invoke("save_settings", { settings: collectSettings() });
  } catch (error) {
    setError(String(error));
  }
});

fields.cancelButton.addEventListener("click", async () => {
  await invoke("cancel_settings");
});

load().catch((error) => setError(String(error)));
