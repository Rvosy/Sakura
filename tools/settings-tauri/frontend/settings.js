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
  ttsBundleNoticeRow: document.getElementById("ttsBundleNoticeRow"),
  ttsBundleNotice: document.getElementById("ttsBundleNotice"),
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
  memoryStatusStrip: document.getElementById("memoryStatusStrip"),
  memorySearch: document.getElementById("memorySearch"),
  memoryLayerFilter: document.getElementById("memoryLayerFilter"),
  memorySort: document.getElementById("memorySort"),
  memoryAddButton: document.getElementById("memoryAddButton"),
  memoryRefreshButton: document.getElementById("memoryRefreshButton"),
  memoryList: document.getElementById("memoryList"),
  memoryContent: document.getElementById("memoryContent"),
  memoryLayer: document.getElementById("memoryLayer"),
  memoryCategory: document.getElementById("memoryCategory"),
  memorySource: document.getElementById("memorySource"),
  memoryImportance: document.getElementById("memoryImportance"),
  memoryConfidence: document.getElementById("memoryConfidence"),
  memoryMeta: document.getElementById("memoryMeta"),
  memorySaveButton: document.getElementById("memorySaveButton"),
  memoryRevertButton: document.getElementById("memoryRevertButton"),
  memoryDeleteButton: document.getElementById("memoryDeleteButton"),
  pluginStatusStrip: document.getElementById("pluginStatusStrip"),
  pluginSearch: document.getElementById("pluginSearch"),
  pluginStatusFilter: document.getElementById("pluginStatusFilter"),
  pluginPermissionFilter: document.getElementById("pluginPermissionFilter"),
  pluginList: document.getElementById("pluginList"),
  pluginDetail: document.getElementById("pluginDetail"),
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
    plugins: document.getElementById("page-plugins"),
    system: document.getElementById("page-system"),
    memory: document.getElementById("page-memory"),
  },
};

let request = null;
let lastTtsProvider = "";
let themeChanged = false;
let memoryRetryTimer = null;
const memoryState = {
  entries: [],
  selectedId: "",
  loading: false,
  loaded: false,
  status: "idle",
  message: "",
  draft: null,
};
const pluginState = {
  selectedId: "",
  enabledById: {},
  initialEnabledById: {},
  settingsValues: {},
  initialSettingsValues: {},
  actionBusyKey: "",
};

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

function clearMemoryRetry() {
  window.clearTimeout(memoryRetryTimer);
  memoryRetryTimer = null;
}

function scheduleMemoryRetry() {
  clearMemoryRetry();
  if (!fields.pages.memory.classList.contains("is-active")) {
    return;
  }
  memoryRetryTimer = window.setTimeout(loadMemories, 1500);
}

function confirmAction(
  message,
  { title = "确认操作", confirmText = "确认", cancelText = "取消", danger = false } = {},
) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "confirm-overlay";
    const dialog = document.createElement("section");
    dialog.className = "confirm-dialog";
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    const heading = document.createElement("h2");
    heading.textContent = title;
    const body = document.createElement("p");
    body.textContent = message;
    const actions = document.createElement("div");
    actions.className = "confirm-actions";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "secondary-button";
    cancel.textContent = cancelText;
    const confirm = document.createElement("button");
    confirm.type = "button";
    if (danger) {
      confirm.className = "danger-button";
    }
    confirm.textContent = confirmText;
    actions.append(cancel, confirm);
    dialog.append(heading, body, actions);
    overlay.append(dialog);

    function close(value) {
      document.removeEventListener("keydown", onKey, true);
      overlay.remove();
      resolve(value);
    }
    function onKey(event) {
      if (event.key === "Escape") {
        close(false);
      }
    }
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) {
        close(false);
      }
    });
    cancel.addEventListener("click", () => close(false));
    confirm.addEventListener("click", () => close(true));
    document.addEventListener("keydown", onKey, true);
    document.body.append(overlay);
    confirm.focus();
  });
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
  plugins: { title: "插件", subtitle: "启停状态、权限、来源与重启生效预览" },
  system: { title: "系统", subtitle: "启动、日志、字幕、气泡与接话" },
  memory: { title: "记忆", subtitle: "查看、编辑、删除长期记忆与常驻档案" },
};

function showPage(page) {
  Object.entries(fields.pages).forEach(([key, element]) => {
    element.classList.toggle("is-active", key === page);
  });
  fields.navItems.forEach((item) => {
    item.classList.toggle("is-active", item.dataset.page === page);
  });
  document.querySelector(".page-scroll")?.classList.toggle("is-admin-active", page === "memory" || page === "plugins");
  if (page !== "memory") {
    clearMemoryRetry();
  }
  const meta = pageMeta[page];
  if (meta) {
    fields.pageTitle.textContent = meta.title;
    fields.pageSubtitle.textContent = meta.subtitle;
  }
  if (
    page === "memory"
    && !memoryState.loading
    && (!memoryState.loaded || memoryState.status === "loading")
  ) {
    loadMemories();
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

function ttsProviderDefaults(provider) {
  return request?.tts?.provider_defaults?.[provider] || {};
}

function ttsDefaultValue(provider, key) {
  return String(ttsProviderDefaults(provider)[key] || "");
}

function isBundledTtsProvider(provider) {
  return provider === "gpt-sovits" || provider === "genie-tts";
}

function normalizeTtsPathText(value) {
  return String(value || "").trim().replaceAll("/", "\\").toLowerCase();
}

function isBundledTtsDefaultPath(value, key) {
  const normalized = normalizeTtsPathText(value);
  return Boolean(normalized) && ["gpt-sovits", "genie-tts"].some((provider) => (
    normalizeTtsPathText(ttsDefaultValue(provider, key)) === normalized
  ));
}

function isTtsDefaultApiUrl(value) {
  const apiUrl = String(value || "").trim();
  return Boolean(apiUrl) && ["gpt-sovits", "genie-tts", "custom-gpt-sovits"].some((provider) => (
    ttsDefaultValue(provider, "api_url") === apiUrl
  ));
}

function applyTtsProviderDefaults(previousProvider = lastTtsProvider) {
  const provider = fields.ttsProvider.value;
  const defaults = ttsProviderDefaults(provider);
  const apiUrl = fields.ttsApiUrl.value.trim();
  const oldApiUrl = ttsDefaultValue(previousProvider, "api_url");
  const newApiUrl = String(defaults.api_url || "");
  if (newApiUrl && (!apiUrl || apiUrl === oldApiUrl || isTtsDefaultApiUrl(apiUrl))) {
    fields.ttsApiUrl.value = newApiUrl;
  }
  if (isBundledTtsProvider(provider)) {
    fields.ttsWorkDir.value = String(defaults.work_dir || "");
    fields.ttsPythonPath.value = String(defaults.python_path || "");
    fields.ttsConfigPath.value = "";
  } else if (provider === "custom-gpt-sovits") {
    if (isBundledTtsDefaultPath(fields.ttsWorkDir.value, "work_dir")) {
      fields.ttsWorkDir.value = "";
    }
    if (isBundledTtsDefaultPath(fields.ttsPythonPath.value, "python_path")) {
      fields.ttsPythonPath.value = "";
    }
    fields.ttsConfigPath.value = "";
  }
  lastTtsProvider = provider;
}

function syncTtsBundleNotice() {
  const provider = fields.ttsProvider.value;
  const notice = isBundledTtsProvider(provider) ? String(ttsProviderDefaults(provider).notice || "") : "";
  fields.ttsBundleNotice.textContent = notice;
  fields.ttsBundleNoticeRow.hidden = !notice;
}

function syncTtsState() {
  const character = selectedCharacter();
  const hasVoice = character ? Boolean(character.has_voice) : true;
  if (!hasVoice) {
    fields.ttsEnabled.checked = false;
  }
  fields.ttsEnabled.disabled = !hasVoice;
  const active = fields.ttsEnabled.checked && fields.ttsProvider.value !== "none";
  const bundledProvider = isBundledTtsProvider(fields.ttsProvider.value);
  fields.ttsApiUrl.disabled = !active;
  fields.ttsTimeout.disabled = !active;
  fields.ttsWorkDir.disabled = !active || bundledProvider;
  fields.ttsPythonPath.disabled = !active || bundledProvider;
  fields.ttsWorkDir.readOnly = false;
  fields.ttsPythonPath.readOnly = false;
  fields.ttsConfigPath.disabled = true;
  syncTtsBundleNotice();
}

function handleTtsProviderChange() {
  applyTtsProviderDefaults(lastTtsProvider);
  syncTtsState();
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

async function hostCall(method, params = {}) {
  return invoke("host_call", { method, params });
}

function memoryLayers() {
  return request?.memory?.layers || [];
}

function memoryDefaults() {
  return request?.memory?.defaults || {
    layer: "semantic",
    source: "manual",
    importance: 0.5,
    confidence: 0.75,
  };
}

function memoryLayerLabel(layer) {
  return memoryLayers().find((item) => item.id === layer)?.label || layer || "未分层";
}

function memoryContent(record) {
  return String(record?.content || record?.memory || "");
}

function compactText(value, max = 110) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (text.length <= max) {
    return text;
  }
  return `${text.slice(0, max - 1)}…`;
}

function renderStrip(container, items) {
  container.textContent = "";
  items.forEach((item) => {
    const chip = document.createElement("span");
    chip.className = "status-chip";
    chip.textContent = `${item.label} ${item.value}`;
    container.append(chip);
  });
}

function renderMemoryControls() {
  fields.memoryLayerFilter.textContent = "";
  const all = document.createElement("option");
  all.value = "";
  all.textContent = "全部层级";
  fields.memoryLayerFilter.append(all);
  memoryLayers().forEach((layer) => {
    const option = document.createElement("option");
    option.value = layer.id;
    option.textContent = layer.label;
    fields.memoryLayerFilter.append(option);
  });

  fields.memoryLayer.textContent = "";
  memoryLayers().forEach((layer) => {
    const option = document.createElement("option");
    option.value = layer.id;
    option.textContent = layer.label;
    fields.memoryLayer.append(option);
  });
}

function selectedMemory() {
  if (memoryState.selectedId === "__draft__") {
    return memoryState.draft;
  }
  return memoryState.entries.find((entry) => entry.id === memoryState.selectedId) || null;
}

function sortedMemories() {
  const entries = [...memoryState.entries];
  const sort = fields.memorySort.value;
  entries.sort((a, b) => {
    if (a.layer === "core_profile" && b.layer !== "core_profile") {
      return -1;
    }
    if (b.layer === "core_profile" && a.layer !== "core_profile") {
      return 1;
    }
    if (sort === "importance_desc") {
      return Number(b.importance || 0) - Number(a.importance || 0);
    }
    if (sort === "confidence_desc") {
      return Number(b.confidence || 0) - Number(a.confidence || 0);
    }
    return String(b.updated_at || b.created_at || "").localeCompare(
      String(a.updated_at || a.created_at || ""),
    );
  });
  return entries;
}

function setMemoryEditorDisabled(disabled) {
  [
    fields.memoryContent,
    fields.memoryLayer,
    fields.memoryCategory,
    fields.memorySource,
    fields.memoryImportance,
    fields.memoryConfidence,
    fields.memorySaveButton,
    fields.memoryRevertButton,
    fields.memoryDeleteButton,
  ].forEach((field) => {
    field.disabled = disabled;
  });
  refreshSelect(fields.memoryLayer);
}

function fillMemoryEditor(record) {
  const readOnly = memoryState.status === "loading" || memoryState.status === "failed";
  if (!record) {
    fields.memoryContent.value = "";
    fields.memoryCategory.value = "";
    fields.memorySource.value = "";
    fields.memoryImportance.value = "";
    fields.memoryConfidence.value = "";
    fields.memoryMeta.textContent = "";
    setMemoryEditorDisabled(true);
    return;
  }
  fields.memoryContent.value = memoryContent(record);
  fields.memoryLayer.value = record.layer || memoryDefaults().layer;
  fields.memoryCategory.value = record.category || "";
  fields.memorySource.value = record.source || memoryDefaults().source;
  fields.memoryImportance.value = Number(record.importance ?? memoryDefaults().importance);
  fields.memoryConfidence.value = Number(record.confidence ?? memoryDefaults().confidence);
  refreshSelect(fields.memoryLayer);
  fields.memoryMeta.textContent = "";
  [
    ["ID", record.id || "新记忆"],
    ["创建", record.created_at || "未保存"],
    ["更新", record.updated_at || "未保存"],
  ].forEach(([label, value]) => {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = value;
    fields.memoryMeta.append(dt, dd);
  });
  setMemoryEditorDisabled(readOnly);
  fields.memoryDeleteButton.disabled = readOnly || memoryState.selectedId === "__draft__";
  fields.memoryRevertButton.disabled = readOnly || memoryState.selectedId === "__draft__";
}

function renderMemoryStatus() {
  const counts = {
    all: memoryState.entries.length,
    core_profile: 0,
    semantic: 0,
    episodic: 0,
    procedural: 0,
    session: 0,
  };
  memoryState.entries.forEach((entry) => {
    if (counts[entry.layer] !== undefined) {
      counts[entry.layer] += 1;
    }
  });
  renderStrip(fields.memoryStatusStrip, [
    { label: "总数", value: counts.all },
    { label: "常驻档案", value: counts.core_profile },
    { label: "长期事实", value: counts.semantic },
    { label: "事件总结", value: counts.episodic },
    { label: "协作规则", value: counts.procedural },
    { label: "当前任务", value: counts.session },
    {
      label: "自动整理",
      value: fields.memoryCurationEnabled.checked ? "启用" : "关闭",
    },
  ]);
}

function renderMemoryList() {
  fields.memoryList.textContent = "";
  if (memoryState.loading) {
    const item = document.createElement("p");
    item.className = "empty-state";
    item.textContent = "记忆系统正在加载。";
    fields.memoryList.append(item);
    return;
  }
  if (memoryState.status === "failed") {
    const item = document.createElement("p");
    item.className = "empty-state";
    item.textContent = memoryState.message || "记忆系统加载失败。";
    fields.memoryList.append(item);
    return;
  }
  const entries = sortedMemories();
  if (!entries.length) {
    const item = document.createElement("p");
    item.className = "empty-state";
    item.textContent = memoryState.message || "暂无记忆。";
    fields.memoryList.append(item);
    return;
  }
  entries.forEach((entry) => {
    const row = document.createElement("div");
    row.className = "memory-card";
    row.setAttribute("role", "button");
    row.tabIndex = 0;
    row.classList.toggle("is-selected", entry.id === memoryState.selectedId);
    row.classList.toggle("is-core", entry.layer === "core_profile");
    const selectRow = () => {
      memoryState.selectedId = entry.id;
      renderMemoryPage();
    };
    row.addEventListener("click", selectRow);
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectRow();
      }
    });
    const title = document.createElement("strong");
    title.textContent = compactText(memoryContent(entry) || "(空记忆)");
    const meta = document.createElement("span");
    meta.className = "card-meta";
    meta.textContent = [
      memoryLayerLabel(entry.layer),
      entry.category || "未分类",
      entry.source || "未知来源",
      entry.updated_at || entry.created_at || "",
    ]
      .filter(Boolean)
      .join(" · ");
    const chips = document.createElement("span");
    chips.className = "chip-row";
    [
      `重要 ${Number(entry.importance ?? 0).toFixed(2)}`,
      `置信 ${Number(entry.confidence ?? 0).toFixed(2)}`,
    ].forEach((text) => {
      const chip = document.createElement("span");
      chip.className = "permission-chip";
      chip.textContent = text;
      chips.append(chip);
    });
    row.append(title, meta, chips);
    fields.memoryList.append(row);
  });
}

function renderMemoryPage() {
  renderMemoryStatus();
  renderMemoryList();
  fillMemoryEditor(selectedMemory());
  fields.memoryAddButton.disabled = memoryState.status === "loading" || memoryState.status === "failed";
  fields.memoryRefreshButton.disabled = memoryState.loading;
}

async function loadMemories() {
  if (!request) {
    return;
  }
  clearMemoryRetry();
  memoryState.loading = true;
  memoryState.status = "loading";
  memoryState.message = "记忆系统正在加载。";
  let shouldRetry = false;
  renderMemoryPage();
  try {
    const params = {
      query: fields.memorySearch.value.trim(),
      limit: request.memory.page_size || 120,
    };
    if (fields.memoryLayerFilter.value) {
      params.layer = fields.memoryLayerFilter.value;
    }
    const result = await hostCall("memory.search", params);
    memoryState.status = result.status || "ready";
    memoryState.message = result.message || result.error || "";
    shouldRetry = memoryState.status === "loading";
    memoryState.entries = Array.isArray(result.memories)
      ? result.memories.filter((entry) => entry && entry.id)
      : [];
    memoryState.loaded = true;
    if (!memoryState.entries.some((entry) => entry.id === memoryState.selectedId)) {
      memoryState.selectedId = memoryState.entries[0]?.id || "";
    }
  } catch (error) {
    memoryState.status = "failed";
    memoryState.message = String(error);
    memoryState.entries = [];
  } finally {
    memoryState.loading = false;
    renderMemoryPage();
    if (shouldRetry) {
      scheduleMemoryRetry();
    }
  }
}

function newMemoryDraft() {
  const defaults = memoryDefaults();
  memoryState.draft = {
    id: "",
    content: "",
    layer: defaults.layer,
    category: "",
    source: defaults.source,
    importance: defaults.importance,
    confidence: defaults.confidence,
  };
  memoryState.selectedId = "__draft__";
  renderMemoryPage();
  fields.memoryContent.focus();
}

function collectMemoryEditor() {
  const payload = {
    content: fields.memoryContent.value.trim(),
    layer: fields.memoryLayer.value || memoryDefaults().layer,
    category: fields.memoryCategory.value.trim(),
    source: fields.memorySource.value.trim() || memoryDefaults().source,
    importance: clampFloat(fields.memoryImportance.value, [0, 1]),
    confidence: clampFloat(fields.memoryConfidence.value, [0, 1]),
  };
  if (memoryState.selectedId && memoryState.selectedId !== "__draft__") {
    payload.id = memoryState.selectedId;
  }
  return payload;
}

async function saveMemoryEditor() {
  const payload = collectMemoryEditor();
  if (!payload.content) {
    setError("记忆内容不能为空。");
    return;
  }
  setError("");
  try {
    const result = await hostCall("memory.upsert", payload);
    if (result.status === "loading" || result.status === "failed") {
      setError(result.error || result.message || "记忆系统暂不可用。");
      return;
    }
    const saved = result.memory || {};
    memoryState.selectedId = saved.id || payload.id || "";
    memoryState.draft = null;
    await loadMemories();
  } catch (error) {
    setError(String(error));
  }
}

async function deleteSelectedMemory() {
  const record = selectedMemory();
  if (!record || !record.id) {
    return;
  }
  const confirmed = await confirmAction("确认删除这条记忆？", {
    title: "删除记忆",
    confirmText: "删除",
    danger: true,
  });
  if (!confirmed) {
    return;
  }
  setError("");
  try {
    const result = await hostCall("memory.delete", { id: record.id });
    if (Array.isArray(result.failed) && result.failed.length) {
      setError(result.failed[0].error || "记忆删除失败。");
      return;
    }
    memoryState.selectedId = "";
    await loadMemories();
  } catch (error) {
    setError(String(error));
  }
}

function permissionInfo(permission) {
  return request?.plugins?.permission_labels?.[permission] || {
    group: "其他",
    label: permission,
  };
}

function clonePlain(value) {
  return JSON.parse(JSON.stringify(value || {}));
}

function pluginSettingsSections(plugin) {
  return Array.isArray(plugin?.settings) ? plugin.settings : [];
}

function pluginSectionValues(pluginId, sectionId) {
  pluginState.settingsValues[pluginId] = pluginState.settingsValues[pluginId] || {};
  pluginState.settingsValues[pluginId][sectionId] = pluginState.settingsValues[pluginId][sectionId] || {};
  return pluginState.settingsValues[pluginId][sectionId];
}

function pluginFieldValue(plugin, section, field) {
  const values = pluginSectionValues(plugin.id, section.section_id);
  if (!Object.prototype.hasOwnProperty.call(values, field.key)) {
    values[field.key] = field.value ?? field.default ?? "";
  }
  return values[field.key];
}

function setPluginFieldValue(plugin, section, field, value) {
  const values = pluginSectionValues(plugin.id, section.section_id);
  values[field.key] = value;
}

function initializePluginState() {
  pluginState.enabledById = {};
  pluginState.initialEnabledById = {};
  pluginState.settingsValues = {};
  pluginState.initialSettingsValues = {};
  (request.plugins?.items || []).forEach((plugin) => {
    pluginState.enabledById[plugin.id] = Boolean(plugin.enabled || plugin.required);
    pluginState.initialEnabledById[plugin.id] = Boolean(plugin.enabled || plugin.required);
    pluginState.settingsValues[plugin.id] = {};
    pluginSettingsSections(plugin).forEach((section) => {
      pluginState.settingsValues[plugin.id][section.section_id] = clonePlain(section.values);
    });
    pluginState.initialSettingsValues[plugin.id] = clonePlain(pluginState.settingsValues[plugin.id]);
  });
  pluginState.selectedId = request.plugins?.items?.[0]?.id || "";
}

function renderPluginPermissionFilter() {
  const current = fields.pluginPermissionFilter.value;
  fields.pluginPermissionFilter.textContent = "";
  const all = document.createElement("option");
  all.value = "";
  all.textContent = "全部权限";
  fields.pluginPermissionFilter.append(all);
  const permissions = new Set();
  (request.plugins?.items || []).forEach((plugin) => {
    (plugin.permissions || []).forEach((permission) => permissions.add(permission));
  });
  [...permissions].sort().forEach((permission) => {
    const option = document.createElement("option");
    option.value = permission;
    option.textContent = permissionInfo(permission).label;
    fields.pluginPermissionFilter.append(option);
  });
  fields.pluginPermissionFilter.value = current;
}

function pluginChanged(plugin) {
  return pluginState.enabledById[plugin.id] !== pluginState.initialEnabledById[plugin.id];
}

function filteredPlugins() {
  const query = fields.pluginSearch.value.trim().toLowerCase();
  const status = fields.pluginStatusFilter.value;
  const permission = fields.pluginPermissionFilter.value;
  return (request.plugins?.items || []).filter((plugin) => {
    const enabled = Boolean(pluginState.enabledById[plugin.id] || plugin.required);
    const text = [plugin.id, plugin.name, plugin.author, plugin.description]
      .join(" ")
      .toLowerCase();
    if (query && !text.includes(query)) {
      return false;
    }
    if (permission && !(plugin.permissions || []).includes(permission)) {
      return false;
    }
    if (status === "enabled" && !enabled) {
      return false;
    }
    if (status === "disabled" && enabled) {
      return false;
    }
    if (status === "required" && !plugin.required) {
      return false;
    }
    if (status === "changed" && !pluginChanged(plugin)) {
      return false;
    }
    return true;
  });
}

function renderPluginStatus() {
  const items = request.plugins?.items || [];
  const enabled = items.filter((plugin) => pluginState.enabledById[plugin.id] || plugin.required).length;
  const changed = items.filter(pluginChanged).length;
  renderStrip(fields.pluginStatusStrip, [
    { label: "全部", value: items.length },
    { label: "已启用", value: enabled },
    { label: "已禁用", value: Math.max(0, items.length - enabled) },
    { label: "必需", value: items.filter((plugin) => plugin.required).length },
    { label: "有改动", value: changed },
  ]);
}

function setPluginEnabled(plugin, enabled) {
  pluginState.enabledById[plugin.id] = plugin.required ? true : Boolean(enabled);
  renderPluginPage();
}

function renderPluginList() {
  fields.pluginList.textContent = "";
  const plugins = filteredPlugins();
  if (!plugins.length) {
    const item = document.createElement("p");
    item.className = "empty-state";
    item.textContent = "没有匹配的插件。";
    fields.pluginList.append(item);
    return;
  }
  plugins.forEach((plugin) => {
    const row = document.createElement("div");
    row.className = "plugin-card";
    row.classList.toggle("is-selected", plugin.id === pluginState.selectedId);
    row.classList.toggle("is-changed", pluginChanged(plugin));
    row.addEventListener("click", () => {
      pluginState.selectedId = plugin.id;
      renderPluginPage();
    });
    const top = document.createElement("div");
    top.className = "plugin-card-top";
    const toggle = document.createElement("input");
    toggle.type = "checkbox";
    toggle.checked = Boolean(pluginState.enabledById[plugin.id] || plugin.required);
    toggle.disabled = Boolean(plugin.required);
    toggle.addEventListener("click", (event) => event.stopPropagation());
    toggle.addEventListener("change", () => setPluginEnabled(plugin, toggle.checked));
    const title = document.createElement("strong");
    title.textContent = plugin.name || plugin.id;
    const version = document.createElement("span");
    version.className = "card-meta";
    version.textContent = `${plugin.author || "未知作者"} · ${plugin.version || "0.0.0"}`;
    top.append(toggle, title, version);
    const desc = document.createElement("p");
    desc.className = "card-desc";
    desc.textContent = compactText(plugin.description || "无描述", 96);
    const chips = document.createElement("span");
    chips.className = "chip-row";
    (plugin.permissions || []).slice(0, 4).forEach((permission) => {
      const chip = document.createElement("span");
      chip.className = "permission-chip";
      chip.textContent = permissionInfo(permission).label;
      chips.append(chip);
    });
    if (plugin.required) {
      const chip = document.createElement("span");
      chip.className = "permission-chip is-locked";
      chip.textContent = "必需";
      chips.append(chip);
    }
    if (pluginChanged(plugin)) {
      const chip = document.createElement("span");
      chip.className = "permission-chip is-pending";
      chip.textContent = "需重启生效";
      chips.append(chip);
    }
    row.append(top, desc, chips);
    fields.pluginList.append(row);
  });
}

function pluginSettingControl(plugin, section, field) {
  const value = pluginFieldValue(plugin, section, field);
  if (field.readonly || field.type === "readonly") {
    const row = document.createElement("div");
    row.className = "plugin-readonly-control";
    const input = document.createElement("input");
    input.type = "text";
    input.readOnly = true;
    input.value = Array.isArray(value) ? value.join(" ; ") : String(value ?? "");
    row.append(input);
    if (field.copyable) {
      const copy = document.createElement("button");
      copy.type = "button";
      copy.className = "secondary-button compact-button";
      copy.textContent = "复制";
      copy.addEventListener("click", async () => {
        await navigator.clipboard.writeText(input.value);
        copy.textContent = "已复制";
        window.setTimeout(() => {
          copy.textContent = "复制";
        }, 1200);
      });
      row.append(copy);
    }
    return row;
  }
  if (field.type === "boolean") {
    const label = document.createElement("label");
    label.className = "check-control";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = Boolean(value);
    input.addEventListener("change", () => setPluginFieldValue(plugin, section, field, input.checked));
    const text = document.createElement("span");
    text.textContent = field.description || field.label;
    label.append(input, text);
    return label;
  }
  if (field.type === "select") {
    const select = document.createElement("select");
    (field.options || []).forEach((option) => {
      const item = document.createElement("option");
      item.value = String(option.value);
      item.textContent = option.label || String(option.value);
      select.append(item);
    });
    select.value = String(value ?? field.default ?? "");
    select.addEventListener("change", () => setPluginFieldValue(plugin, section, field, select.value));
    window.setTimeout(() => enhanceSelect(select), 0);
    return select;
  }
  const input = document.createElement("input");
  input.type = field.type === "integer" || field.type === "number" ? "number" : field.type === "password" ? "password" : "text";
  if (field.minimum !== undefined) {
    input.min = String(field.minimum);
  }
  if (field.maximum !== undefined) {
    input.max = String(field.maximum);
  }
  if (field.step !== undefined) {
    input.step = String(field.step);
  } else if (field.type === "integer") {
    input.step = "1";
  }
  input.value = String(value ?? "");
  input.addEventListener("input", () => {
    if (field.type === "integer") {
      setPluginFieldValue(plugin, section, field, Number.parseInt(input.value, 10));
    } else if (field.type === "number") {
      setPluginFieldValue(plugin, section, field, Number.parseFloat(input.value));
    } else {
      setPluginFieldValue(plugin, section, field, input.value);
    }
  });
  return input;
}

function renderPluginSettings(plugin) {
  const sections = pluginSettingsSections(plugin);
  const container = document.createElement("div");
  container.className = "plugin-settings";
  if (!sections.length) {
    const empty = document.createElement("p");
    empty.className = "page-note";
    empty.textContent = plugin.enabled
      ? "此插件没有内置详细设置。"
      : "此插件未启用；启用并保存重启 Sakura 后才会加载内置详细设置。";
    container.append(empty);
    return container;
  }
  sections.forEach((section) => {
    const block = document.createElement("section");
    block.className = "plugin-settings-section";
    const heading = document.createElement("h3");
    heading.textContent = section.title || section.section_id;
    block.append(heading);
    if (section.error) {
      const error = document.createElement("p");
      error.className = "error";
      error.textContent = section.error;
      block.append(error);
    }
    (section.fields || []).forEach((field) => {
      const row = document.createElement("div");
      row.className = "form-row";
      const label = document.createElement("label");
      label.textContent = field.label || field.key;
      const control = pluginSettingControl(plugin, section, field);
      if (field.type !== "boolean" && field.description) {
        control.title = field.description;
      }
      row.append(label, control);
      if (field.restart_required) {
        const hint = document.createElement("p");
        hint.className = "hint";
        hint.textContent = "保存后重启或下次启动生效。";
        row.append(hint);
      }
      block.append(row);
    });
    if (Array.isArray(section.actions) && section.actions.length) {
      const actions = document.createElement("div");
      actions.className = "plugin-setting-actions";
      section.actions.forEach((action) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = action.danger ? "danger-button" : "secondary-button";
        button.textContent = action.label || action.action_id;
        const busyKey = `${plugin.id}:${section.section_id}:${action.action_id}`;
        button.disabled = pluginState.actionBusyKey === busyKey;
        button.addEventListener("click", () => runPluginSettingsAction(plugin, section, action));
        actions.append(button);
      });
      block.append(actions);
    }
    container.append(block);
  });
  return container;
}

async function runPluginSettingsAction(plugin, section, action) {
  const busyKey = `${plugin.id}:${section.section_id}:${action.action_id}`;
  pluginState.actionBusyKey = busyKey;
  renderPluginPage();
  setError("");
  try {
    const result = await hostCall("plugin.settings_action", {
      plugin_id: plugin.id,
      section_id: section.section_id,
      action_id: action.action_id,
      values: clonePlain(pluginSectionValues(plugin.id, section.section_id)),
    });
    if (result && typeof result.values === "object" && result.values !== null) {
      pluginState.settingsValues[plugin.id][section.section_id] = {
        ...pluginState.settingsValues[plugin.id][section.section_id],
        ...result.values,
      };
    }
    if (result && result.message) {
      setError(String(result.message));
    }
  } catch (error) {
    setError(String(error));
  } finally {
    pluginState.actionBusyKey = "";
    renderPluginPage();
  }
}

function renderPluginDetail() {
  const plugin = (request.plugins?.items || []).find((item) => item.id === pluginState.selectedId);
  fields.pluginDetail.textContent = "";
  if (!plugin) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "选择左侧插件查看详情。";
    fields.pluginDetail.append(empty);
    return;
  }
  const title = document.createElement("h2");
  title.textContent = plugin.name || plugin.id;
  const desc = document.createElement("p");
  desc.className = "detail-desc";
  desc.textContent = plugin.description || "无描述。";
  const meta = document.createElement("dl");
  meta.className = "detail-meta";
  [
    ["ID", plugin.id],
    ["入口", plugin.entry || "未声明"],
    ["来源", plugin.source || "未知"],
    ["优先级", String(plugin.priority ?? "")],
    ["版本", plugin.version || "0.0.0"],
    ["作者", plugin.author || "未知"],
    [
      "当前状态",
      pluginState.initialEnabledById[plugin.id] ? "已启用" : "已禁用",
    ],
    [
      "保存后状态",
      pluginState.enabledById[plugin.id] || plugin.required ? "已启用" : "已禁用",
    ],
  ].forEach(([label, value]) => {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = value;
    meta.append(dt, dd);
  });

  const groups = new Map();
  (plugin.permissions || []).forEach((permission) => {
    const info = permissionInfo(permission);
    const list = groups.get(info.group) || [];
    list.push(info.label);
    groups.set(info.group, list);
  });
  const permissions = document.createElement("div");
  permissions.className = "permission-groups";
  if (!groups.size) {
    const none = document.createElement("p");
    none.className = "hint";
    none.textContent = "未声明权限。";
    permissions.append(none);
  }
  groups.forEach((labels, group) => {
    const block = document.createElement("section");
    const heading = document.createElement("h3");
    heading.textContent = group;
    const chips = document.createElement("div");
    chips.className = "chip-row";
    labels.forEach((label) => {
      const chip = document.createElement("span");
      chip.className = "permission-chip";
      chip.textContent = label;
      chips.append(chip);
    });
    block.append(heading, chips);
    permissions.append(block);
  });
  const note = document.createElement("p");
  note.className = "page-note";
  note.textContent = plugin.required
    ? "必需插件由宿主锁定，不能关闭。"
    : "启停变化保存后重启 Sakura 生效。";
  fields.pluginDetail.append(title, desc, meta, permissions, note, renderPluginSettings(plugin));
}

function renderPluginPage() {
  renderPluginStatus();
  renderPluginList();
  renderPluginDetail();
}

function collectPluginSettings() {
  const enabledById = {};
  const settingsById = {};
  (request.plugins?.items || []).forEach((plugin) => {
    enabledById[plugin.id] = plugin.required ? true : Boolean(pluginState.enabledById[plugin.id]);
    const sections = pluginSettingsSections(plugin);
    if (sections.length) {
      settingsById[plugin.id] = {};
      sections.forEach((section) => {
        settingsById[plugin.id][section.section_id] = clonePlain(
          pluginSectionValues(plugin.id, section.section_id),
        );
      });
    }
  });
  return { enabled_by_id: enabledById, settings_by_id: settingsById };
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
    plugins: collectPluginSettings(),
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
  renderMemoryControls();
  initializePluginState();
  renderPluginPermissionFilter();
  enhanceSelect(fields.characterSelect);
  enhanceSelect(fields.visualEffectMode);
  enhanceSelect(fields.ttsProvider);
  enhanceSelect(fields.backchannelMode);
  enhanceSelect(fields.memoryLayerFilter);
  enhanceSelect(fields.memorySort);
  enhanceSelect(fields.memoryLayer);
  enhanceSelect(fields.pluginStatusFilter);
  enhanceSelect(fields.pluginPermissionFilter);

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
  lastTtsProvider = fields.ttsProvider.value;
  applyTtsProviderDefaults(lastTtsProvider);

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
  renderMemoryPage();
  renderPluginPage();
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
fields.ttsProvider.addEventListener("change", handleTtsProviderChange);
fields.visualEffectMode.addEventListener("change", markThemeChanged);
fields.resetThemeButton.addEventListener("click", () => {
  setThemeValues(request.theme_defaults);
  themeChanged = true;
});
fields.debugLogEnabled.addEventListener("change", syncDebugLogState);
fields.bubbleAutoHide.addEventListener("change", syncBubbleState);
let memorySearchTimer = null;
fields.memorySearch.addEventListener("input", () => {
  clearMemoryRetry();
  window.clearTimeout(memorySearchTimer);
  memorySearchTimer = window.setTimeout(loadMemories, 180);
});
fields.memoryLayerFilter.addEventListener("change", loadMemories);
fields.memorySort.addEventListener("change", renderMemoryPage);
fields.memoryAddButton.addEventListener("click", newMemoryDraft);
fields.memoryRefreshButton.addEventListener("click", loadMemories);
fields.memorySaveButton.addEventListener("click", saveMemoryEditor);
fields.memoryRevertButton.addEventListener("click", () => fillMemoryEditor(selectedMemory()));
fields.memoryDeleteButton.addEventListener("click", deleteSelectedMemory);
fields.memoryCurationEnabled.addEventListener("change", renderMemoryStatus);
fields.pluginSearch.addEventListener("input", renderPluginPage);
fields.pluginStatusFilter.addEventListener("change", renderPluginPage);
fields.pluginPermissionFilter.addEventListener("change", renderPluginPage);
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
