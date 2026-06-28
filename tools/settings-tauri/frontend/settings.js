const invoke = window.__TAURI__.core.invoke;

const fields = {
  characterSelect: document.getElementById("characterSelect"),
  characterImportButton: document.getElementById("characterImportButton"),
  ttsVoiceImportButton: document.getElementById("ttsVoiceImportButton"),
  characterExportButton: document.getElementById("characterExportButton"),
  characterArchiveHint: document.getElementById("characterArchiveHint"),
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
  providerStatusStrip: document.getElementById("providerStatusStrip"),
  providerSearch: document.getElementById("providerSearch"),
  addProviderButton: document.getElementById("addProviderButton"),
  providerList: document.getElementById("providerList"),
  providerDetail: document.getElementById("providerDetail"),
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
  ttsResourceCard: document.getElementById("ttsResourceCard"),
  ttsTestButton: document.getElementById("ttsTestButton"),
  ttsTimeout: document.getElementById("ttsTimeout"),
  themeColors: document.getElementById("themeColors"),
  visualEffectMode: document.getElementById("visualEffectMode"),
  themeAiButton: document.getElementById("themeAiButton"),
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
  backchannelResourceCard: document.getElementById("backchannelResourceCard"),
  memoryTriggerTurns: document.getElementById("memoryTriggerTurns"),
  memoryModelResourceCard: document.getElementById("memoryModelResourceCard"),
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
  applyButton: document.getElementById("applyButton"),
  cancelButton: document.getElementById("cancelButton"),
  pageTitle: document.getElementById("pageTitle"),
  pageSubtitle: document.getElementById("pageSubtitle"),
  navItems: Array.from(document.querySelectorAll(".nav-item[data-page]")),
  pages: {
    character: document.getElementById("page-character"),
    privacy: document.getElementById("page-privacy"),
    appearance: document.getElementById("page-appearance"),
    providers: document.getElementById("page-providers"),
    model: document.getElementById("page-model"),
    voice: document.getElementById("page-voice"),
    interaction: document.getElementById("page-interaction"),
    tools: document.getElementById("page-tools"),
    plugins: document.getElementById("page-plugins"),
    system: document.getElementById("page-system"),
    memory: document.getElementById("page-memory"),
  },
};

let request = null;
let lastTtsProvider = "";
let themeChanged = false;
// 「未保存改动」基线：load() 末尾拍下 collectSettings() 的 JSON 快照，之后任意输入都与它比对。
let settingsBaseline = null;
// 程序化关窗（保存/取消）前置真，避免关窗拦截器把正常关闭误判成「放弃改动」。
let bypassCloseGuard = false;
let memoryRetryTimer = null;
let characterArchiveBusy = false;
const characterExportOptions = [
  {
    kind: "full",
    label: "完整包 (.char)",
    description: "导出角色配置和可携带语音模型，适合完整迁移。",
    requiresVoice: true,
  },
  {
    kind: "card",
    label: "单角色包 (.char)",
    description: "只导出角色配置，不包含语音模型。",
    requiresVoice: false,
  },
  {
    kind: "voice",
    label: "语音包 (.voice)",
    description: "只导出当前角色的可携带 TTS 模型。",
    requiresVoice: true,
  },
];
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
const resourceState = {
  snapshot: null,
  pollTimer: null,
  ttsBundleKey: "",
  seenTaskFinishes: {},
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

let activeThemeField = "";
let themeEditor = {};

function setError(message) {
  fields.errorText.textContent = message || "";
}

// 反馈分流：错误常驻 footer 红字（role=alert）走 setError；成功/信息走右下角 toast，自动消失。
const toastStack = document.getElementById("toastStack");

function notify(message, type = "info") {
  const text = String(message ?? "").trim();
  if (!text) {
    return;
  }
  if (type === "error") {
    setError(text);
    return;
  }
  setError("");
  if (!toastStack) {
    return;
  }
  const toast = document.createElement("div");
  toast.className = `toast is-${type}`;
  toast.setAttribute("role", "status");
  toast.textContent = text;
  toastStack.append(toast);
  const remove = () => {
    toast.classList.add("is-leaving");
    window.setTimeout(() => toast.remove(), 220);
  };
  window.setTimeout(remove, 2600);
  toast.addEventListener("click", remove);
}

// ---------- 未保存改动追踪 ----------
function settingsSnapshot() {
  try {
    return JSON.stringify(collectSettings());
  } catch {
    return settingsBaseline;
  }
}

function computeDirty() {
  return Boolean(request) && settingsBaseline !== null && settingsSnapshot() !== settingsBaseline;
}

function refreshDirty() {
  const dirty = computeDirty();
  document.body.classList.toggle("is-dirty", dirty);
  fields.saveButton.classList.toggle("has-changes", dirty);
}

let dirtyTimer = null;
function scheduleDirty() {
  if (settingsBaseline === null) {
    return;
  }
  window.clearTimeout(dirtyTimer);
  dirtyTimer = window.setTimeout(refreshDirty, 150);
}

// 关窗/取消前若有未保存改动则二次确认，返回是否放行。
async function confirmDiscard() {
  if (!computeDirty()) {
    return true;
  }
  return confirmAction("有未保存的改动，确定放弃并关闭吗？", {
    title: "放弃改动",
    confirmText: "放弃",
    cancelText: "返回",
    danger: true,
  });
}

async function closeSettingsWindow() {
  bypassCloseGuard = true;
  try {
    await invoke("cancel_settings");
    return;
  } catch (error) {
    const current = window.__TAURI__?.window?.getCurrentWindow?.();
    if (current?.close) {
      await current.close();
      return;
    }
    throw error;
  }
}

let closeRequestInFlight = false;
async function requestCancelClose() {
  if (closeRequestInFlight) {
    return;
  }
  closeRequestInFlight = true;
  try {
    if (!(await confirmDiscard())) {
      return;
    }
    await closeSettingsWindow();
  } catch (error) {
    bypassCloseGuard = false;
    setError(String(error));
  } finally {
    closeRequestInFlight = false;
  }
}

function markInvalid(input, invalid) {
  if (input) {
    input.classList.toggle("is-invalid", Boolean(invalid));
  }
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
      if (option.disabled) {
        item.classList.add("is-disabled");
        item.setAttribute("aria-disabled", "true");
      }
      item.addEventListener("click", () => {
        if (option.disabled) {
          return;
        }
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
    const maxWidth = Math.max(120, window.innerWidth - 16);
    menu.style.minWidth = `${rect.width}px`;
    menu.style.width = "max-content";
    menu.style.maxWidth = `${maxWidth}px`;
    const menuWidth = Math.min(menu.offsetWidth, maxWidth);
    menu.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - menuWidth - 8))}px`;
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

function themeFieldInput(id) {
  return fields.themeColors.querySelector(`[data-theme-field="${id}"]`);
}

function themeFieldLabel(id) {
  return request.theme_fields.find((field) => field.id === id)?.label || id;
}

function themeFieldValue(id) {
  const input = themeFieldInput(id);
  return normalizeColorText(input?.value, request.theme_defaults[id]);
}

function hexToRgb(hex) {
  const value = normalizeColorText(hex, "#000000").slice(1);
  return {
    r: Number.parseInt(value.slice(0, 2), 16),
    g: Number.parseInt(value.slice(2, 4), 16),
    b: Number.parseInt(value.slice(4, 6), 16),
  };
}

function componentToHex(value) {
  return Math.round(Math.min(255, Math.max(0, value))).toString(16).padStart(2, "0");
}

function rgbToHex({ r, g, b }) {
  return `#${componentToHex(r)}${componentToHex(g)}${componentToHex(b)}`;
}

function rgbToHsv({ r, g, b }) {
  const red = r / 255;
  const green = g / 255;
  const blue = b / 255;
  const max = Math.max(red, green, blue);
  const min = Math.min(red, green, blue);
  const delta = max - min;
  let h = 0;
  if (delta !== 0) {
    if (max === red) {
      h = ((green - blue) / delta) % 6;
    } else if (max === green) {
      h = (blue - red) / delta + 2;
    } else {
      h = (red - green) / delta + 4;
    }
    h *= 60;
    if (h < 0) {
      h += 360;
    }
  }
  return {
    h,
    s: max === 0 ? 0 : delta / max,
    v: max,
  };
}

function hsvToRgb({ h, s, v }) {
  const chroma = v * s;
  const x = chroma * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = v - chroma;
  let red = 0;
  let green = 0;
  let blue = 0;
  if (h < 60) {
    red = chroma; green = x;
  } else if (h < 120) {
    red = x; green = chroma;
  } else if (h < 180) {
    green = chroma; blue = x;
  } else if (h < 240) {
    green = x; blue = chroma;
  } else if (h < 300) {
    red = x; blue = chroma;
  } else {
    red = chroma; blue = x;
  }
  return {
    r: (red + m) * 255,
    g: (green + m) * 255,
    b: (blue + m) * 255,
  };
}

const pageMeta = {
  character: { title: "角色与布局", subtitle: "选择陪伴角色与桌宠布局" },
  appearance: { title: "外观", subtitle: "配色与输入栏视觉效果" },
  providers: { title: "供应商", subtitle: "管理 API 供应商、密钥与模型" },
  model: { title: "模型", subtitle: "功能模型分配与高级参数" },
  voice: { title: "语音", subtitle: "TTS 提供器与语音参数" },
  interaction: { title: "交互", subtitle: "字幕、气泡与快速接话" },
  privacy: { title: "隐私", subtitle: "主动屏幕感知与截图预算" },
  tools: { title: "工具", subtitle: "桌面控制与工具循环上限" },
  plugins: { title: "插件", subtitle: "启停状态、权限、来源与重启生效预览" },
  system: { title: "系统", subtitle: "启动、日志与排查工具" },
  memory: { title: "记忆", subtitle: "查看、编辑、删除长期记忆与常驻档案" },
};

function showPage(page) {
  Object.entries(fields.pages).forEach(([key, element]) => {
    element.classList.toggle("is-active", key === page);
  });
  fields.navItems.forEach((item) => {
    const active = item.dataset.page === page;
    item.classList.toggle("is-active", active);
    if (active) {
      item.setAttribute("aria-current", "page");
    } else {
      item.removeAttribute("aria-current");
    }
  });
  document.querySelector(".page-scroll")?.classList.toggle(
    "is-admin-active",
    page === "memory" || page === "plugins" || page === "providers",
  );
  if (page !== "memory") {
    clearMemoryRetry();
  }
  const meta = pageMeta[page];
  if (meta) {
    fields.pageTitle.textContent = meta.title;
    fields.pageSubtitle.textContent = meta.subtitle;
  }
  // 进入「模型」页时按当前供应商重建槽位选项（供应商可能在另一页被改过）。
  if (page === "model" && request) {
    refreshModelSlots();
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

function selectedCharacterHasExportableVoice() {
  return Boolean(selectedCharacter()?.has_exportable_voice);
}

function selectedCharacterThemeDefaults() {
  return selectedCharacter()?.default_theme || request.theme_defaults;
}

function selectedCharacterTheme() {
  return selectedCharacter()?.theme || selectedCharacterThemeDefaults();
}

// 切换角色时跟随载入该角色的最终配色（仅配色，输入栏视觉效果等用户级偏好保留）。
function applySelectedCharacterTheme() {
  setThemeValues(selectedCharacterTheme(), { updateVisualEffect: false });
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
  fields.ttsTestButton.disabled = !active;
  syncTtsBundleNotice();
  if (request) {
    renderTtsResourceCard();
  }
}

async function testTtsSettings() {
  const character = selectedCharacter();
  if (!character) {
    setError("请先选择一个角色。");
    return;
  }
  const original = fields.ttsTestButton.textContent;
  fields.ttsTestButton.disabled = true;
  fields.ttsTestButton.textContent = "检测中…";
  setError("");
  try {
    const result = await hostCall("tts.test", {
      character_id: character.id,
      tts: collectTtsSettings(),
    });
    notify(result?.message || "TTS 服务检测成功。", "success");
  } catch (error) {
    setError(`TTS 检测失败：${error}`);
  } finally {
    fields.ttsTestButton.disabled = false;
    fields.ttsTestButton.textContent = original;
    syncTtsState();
  }
}

function handleTtsProviderChange() {
  resourceState.ttsBundleKey = "";
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
  syncCharacterArchiveState();
}

function syncCharacterArchiveState() {
  if (!request) {
    return;
  }
  const character = selectedCharacter();
  const hasCharacter = Boolean(character);
  fields.characterSelect.disabled = characterArchiveBusy || !request.character.characters.length;
  fields.characterImportButton.disabled = characterArchiveBusy;
  fields.ttsVoiceImportButton.disabled = characterArchiveBusy || !hasCharacter;
  fields.characterExportButton.disabled = characterArchiveBusy || !hasCharacter;
  fields.saveButton.disabled = characterArchiveBusy;
  fields.applyButton.disabled = characterArchiveBusy;
  fields.cancelButton.disabled = characterArchiveBusy;
  fields.characterArchiveHint.textContent = characterArchiveBusy
    ? "角色包处理中..."
    : (hasCharacter ? "管理 Sakura .char 与 .voice 文件。" : "先导入一个 Sakura .char 角色包。");
  refreshSelect(fields.characterSelect);
}

function setCharacterArchiveBusy(busy) {
  characterArchiveBusy = Boolean(busy);
  syncCharacterArchiveState();
}

function renderThemeControls() {
  fields.themeColors.textContent = "";
  activeThemeField = activeThemeField || request.theme_fields[0]?.id || "";

  request.theme_fields.forEach(({ id, label }) => {
    const row = document.createElement("div");
    row.className = "form-row theme-color-row";
    row.dataset.themeRole = id;
    const rowLabel = document.createElement("label");
    rowLabel.htmlFor = `theme-${id}`;
    rowLabel.textContent = label;
    const controls = document.createElement("div");
    controls.className = "theme-color-control";

    const swatchButton = document.createElement("button");
    swatchButton.type = "button";
    swatchButton.className = "theme-color-swatch";
    swatchButton.dataset.themeSwatch = id;
    swatchButton.title = "调整颜色";
    swatchButton.addEventListener("click", () => openThemeColorPopover(id, swatchButton));

    const textInput = document.createElement("input");
    textInput.id = `theme-${id}`;
    textInput.type = "text";
    textInput.maxLength = 7;
    textInput.placeholder = "#RRGGBB";
    textInput.dataset.themeField = id;
    textInput.addEventListener("input", () => {
      syncThemeRole(id);
      if (id === activeThemeField) {
        syncThemeEditor();
      }
      markThemeChanged();
    });

    controls.append(swatchButton, textInput);
    row.append(rowLabel, controls);
    fields.themeColors.append(row);
  });

  fields.themeColors.append(buildThemeEditor());
  request.theme_fields.forEach(({ id }) => syncThemeRole(id));
  selectThemeField(activeThemeField, { open: false });

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

function buildThemeEditor() {
  const editor = document.createElement("dialog");
  editor.className = "theme-color-popover";
  editor.hidden = true;

  const head = document.createElement("div");
  head.className = "theme-editor-head";
  const swatch = document.createElement("div");
  swatch.className = "theme-editor-swatch";
  const title = document.createElement("div");
  title.className = "theme-editor-title";
  const label = document.createElement("strong");
  const key = document.createElement("span");
  title.append(label, key);
  head.append(swatch, title);

  const hexRow = document.createElement("label");
  hexRow.className = "theme-editor-field";
  hexRow.textContent = "HEX";
  const hex = document.createElement("input");
  hex.type = "text";
  hex.maxLength = 7;
  hex.placeholder = "#RRGGBB";
  hex.addEventListener("input", () => {
    const color = normalizeColorText(hex.value, "");
    markInvalid(hex, !color);
    if (color) {
      updateActiveThemeColor(color);
    }
  });
  hexRow.append(hex);

  const rgb = document.createElement("div");
  rgb.className = "theme-rgb-row";
  const rgbInputs = ["R", "G", "B"].map((name) => {
    const field = document.createElement("label");
    field.textContent = name;
    const input = document.createElement("input");
    input.type = "number";
    input.min = "0";
    input.max = "255";
    input.step = "1";
    input.addEventListener("input", updateThemeFromRgbInputs);
    field.append(input);
    rgb.append(field);
    return input;
  });

  const svPad = document.createElement("div");
  svPad.className = "theme-sv-pad";
  const svPointer = document.createElement("span");
  svPointer.className = "theme-picker-pointer";
  svPad.append(svPointer);
  svPad.addEventListener("pointerdown", updateThemeFromSvPointer);
  svPad.addEventListener("pointermove", (event) => {
    if (event.buttons & 1) {
      updateThemeFromSvPointer(event);
    }
  });

  const hue = document.createElement("div");
  hue.className = "theme-hue-strip";
  const huePointer = document.createElement("span");
  huePointer.className = "theme-hue-pointer";
  hue.append(huePointer);
  hue.addEventListener("pointerdown", updateThemeFromHuePointer);
  hue.addEventListener("pointermove", (event) => {
    if (event.buttons & 1) {
      updateThemeFromHuePointer(event);
    }
  });

  const actions = document.createElement("div");
  actions.className = "theme-editor-actions";
  const pick = document.createElement("button");
  pick.type = "button";
  pick.className = "secondary-button";
  pick.textContent = "取色";
  pick.addEventListener("click", pickActiveThemeColor);
  const done = document.createElement("button");
  done.type = "button";
  done.className = "primary-button";
  done.textContent = "完成";
  done.addEventListener("click", closeThemeColorPopover);
  actions.append(pick, done);

  editor.append(head, svPad, hue, hexRow, rgb, actions);
  themeEditor = {
    root: editor,
    swatch,
    label,
    key,
    hex,
    rgbInputs,
    svPad,
    svPointer,
    hue,
    huePointer,
    pick,
  };
  return editor;
}

function syncThemeRole(id) {
  const input = themeFieldInput(id);
  const color = normalizeColorText(input?.value, "");
  const fallback = themeFieldValue(id);
  const row = fields.themeColors.querySelector(`[data-theme-role="${id}"]`);
  const swatch = fields.themeColors.querySelector(`[data-theme-swatch="${id}"]`);
  if (row) {
    row.classList.toggle("is-active", id === activeThemeField);
    row.classList.toggle("is-invalid", Boolean(input?.value) && !color);
  }
  if (swatch) {
    swatch.style.backgroundColor = color || fallback;
  }
}

function selectThemeField(id, options = {}) {
  if (!request.theme_fields.some((field) => field.id === id)) {
    activeThemeField = request.theme_fields[0]?.id || "";
  } else {
    activeThemeField = id;
  }
  request.theme_fields.forEach(({ id: fieldId }) => syncThemeRole(fieldId));
  syncThemeEditor();
  if (options.open !== false) {
    openThemeColorPopover(activeThemeField, fields.themeColors.querySelector(`[data-theme-swatch="${activeThemeField}"]`));
  }
}

function syncThemeEditor() {
  if (!themeEditor.root || !activeThemeField) {
    return;
  }
  const color = themeFieldValue(activeThemeField);
  const rgb = hexToRgb(color);
  const hsv = rgbToHsv(rgb);
  themeEditor.root.style.setProperty("--theme-editor-color", color);
  themeEditor.root.style.setProperty("--theme-editor-hue", `${hsv.h}deg`);
  themeEditor.swatch.style.background = color;
  themeEditor.label.textContent = themeFieldLabel(activeThemeField);
  themeEditor.key.textContent = activeThemeField;
  themeEditor.hex.value = color;
  markInvalid(themeEditor.hex, false);
  [rgb.r, rgb.g, rgb.b].forEach((value, index) => {
    themeEditor.rgbInputs[index].value = String(value);
  });
  themeEditor.svPointer.style.left = `${hsv.s * 100}%`;
  themeEditor.svPointer.style.top = `${(1 - hsv.v) * 100}%`;
  themeEditor.huePointer.style.left = `${(hsv.h / 360) * 100}%`;
}

function openThemeColorPopover(id) {
  selectThemeField(id, { open: false });
  const popover = themeEditor.root;
  if (!popover) {
    return;
  }
  popover.hidden = false;
  if (!popover.open) {
    popover.showModal();
  }
  themeEditor.hex.focus();
  document.addEventListener("keydown", closeThemePopoverOnEscape, true);
}

function closeThemeColorPopover() {
  if (themeEditor.root) {
    if (themeEditor.root.open) {
      themeEditor.root.close();
    }
    themeEditor.root.hidden = true;
  }
  document.removeEventListener("keydown", closeThemePopoverOnEscape, true);
}

function closeThemePopoverOnEscape(event) {
  if (event.key === "Escape") {
    closeThemeColorPopover();
  }
}

function updateActiveThemeColor(color) {
  const normalized = normalizeColorText(color, "");
  const input = themeFieldInput(activeThemeField);
  if (!normalized || !input) {
    return;
  }
  input.value = normalized;
  syncThemeRole(activeThemeField);
  syncThemeEditor();
  markThemeChanged();
}

function updateThemeFromRgbInputs() {
  if (!themeEditor.rgbInputs?.length) {
    return;
  }
  if (themeEditor.rgbInputs.some((input) => input.value === "")) {
    return;
  }
  const [r, g, b] = themeEditor.rgbInputs.map((input) => (
    Math.min(255, Math.max(0, Number.parseInt(input.value, 10) || 0))
  ));
  updateActiveThemeColor(rgbToHex({ r, g, b }));
}

function updateThemeFromSvPointer(event) {
  const rect = themeEditor.svPad.getBoundingClientRect();
  const x = Math.min(rect.width, Math.max(0, event.clientX - rect.left));
  const y = Math.min(rect.height, Math.max(0, event.clientY - rect.top));
  const hsv = rgbToHsv(hexToRgb(themeFieldValue(activeThemeField)));
  updateActiveThemeColor(rgbToHex(hsvToRgb({
    h: hsv.h,
    s: rect.width ? x / rect.width : 0,
    v: rect.height ? 1 - (y / rect.height) : 0,
  })));
}

function updateThemeFromHuePointer(event) {
  const rect = themeEditor.hue.getBoundingClientRect();
  const x = Math.min(rect.width, Math.max(0, event.clientX - rect.left));
  const hsv = rgbToHsv(hexToRgb(themeFieldValue(activeThemeField)));
  updateActiveThemeColor(rgbToHex(hsvToRgb({
    h: rect.width ? (x / rect.width) * 360 : 0,
    s: hsv.s,
    v: hsv.v,
  })));
}

async function pickActiveThemeColor() {
  if (!activeThemeField) {
    return;
  }
  themeEditor.pick.disabled = true;
  setError("");
  try {
    closeThemeColorPopover();
    const result = await hostCall("theme.pick_screen_color");
    if (result?.cancelled) {
      return;
    }
    const color = normalizeColorText(result?.color, "");
    if (!color) {
      throw new Error("取色结果无效。");
    }
    updateActiveThemeColor(color);
  } catch (error) {
    setError(`屏幕取色失败：${error}`);
  } finally {
    themeEditor.pick.disabled = false;
  }
}

function setThemeValues(theme, options = {}) {
  const updateVisualEffect = options.updateVisualEffect !== false;
  request.theme_fields.forEach(({ id }) => {
    const textInput = themeFieldInput(id);
    const color = normalizeColorText(theme[id], request.theme_defaults[id]);
    if (textInput) {
      textInput.value = color;
    }
    syncThemeRole(id);
  });
  if (updateVisualEffect && theme.visual_effect_mode) {
    fields.visualEffectMode.value = theme.visual_effect_mode;
    refreshSelect(fields.visualEffectMode);
  }
  applyTheme({
    ...theme,
    visual_effect_mode: fields.visualEffectMode.value || request.theme.visual_effect_mode,
  });
  syncThemeEditor();
}

async function generateAiTheme() {
  const character = selectedCharacter();
  if (!character) {
    setError("请先选择一个角色。");
    return;
  }
  const original = fields.themeAiButton.textContent;
  fields.themeAiButton.disabled = true;
  fields.themeAiButton.textContent = "生成中…";
  setError("");
  try {
    const result = await hostCall("theme.generate_ai", { character_id: character.id });
    if (!result?.theme) {
      throw new Error("AI 返回的主题格式无效。");
    }
    setThemeValues(result.theme);
    themeChanged = true;
    notify("AI 配色已生成。", "success");
  } catch (error) {
    setError(`AI 配色失败，已保留当前配色：${error}`);
  } finally {
    fields.themeAiButton.disabled = false;
    fields.themeAiButton.textContent = original;
  }
}

function makeProfileId() {
  if (window.crypto?.randomUUID) {
    return window.crypto.randomUUID();
  }
  return `profile-${Date.now()}`;
}

// 供应商页改为状态驱动的主从结构：providerState.profiles 是唯一数据源，
// 「供应商」页与「模型」页的槽位都从它派生（参照 pluginState/memoryState）。
const providerState = { profiles: [], selectedId: "", search: "" };
const inheritedSlotManualSelections = {};

// 内置预设：选中即预填 Base URL 与图标，其余走「自定义」。
const PROVIDER_PRESETS = [
  {
    key: "deepseek",
    label: "DeepSeek",
    base_url: "https://api.deepseek.com/v1",
    host: "api.deepseek.com",
    iconUrl: "./assets/providers/deepseek.svg",
  },
];

function initializeProviderState() {
  providerState.profiles = (request.api.profiles || []).map((profile) => ({
    id: profile.id || makeProfileId(),
    alias: profile.alias || profile.id || "供应商",
    base_url: profile.base_url || "",
    api_key: profile.api_key || "",
    models: Array.isArray(profile.models) ? profile.models.map(String) : [],
  }));
  providerState.selectedId = providerState.profiles[0]?.id || "";
}

function providerHost(url) {
  const text = String(url || "").trim();
  if (!text) {
    return "";
  }
  try {
    return new URL(text).host;
  } catch {
    return text.replace(/^https?:\/\//, "").split("/")[0];
  }
}

function presetForProfile(profile) {
  const host = providerHost(profile.base_url);
  const alias = String(profile.alias || "").toLowerCase();
  return (
    PROVIDER_PRESETS.find((preset) => preset.host === host || preset.label.toLowerCase() === alias)
    || null
  );
}

function filteredProviders() {
  const query = providerState.search.trim().toLowerCase();
  if (!query) {
    return providerState.profiles;
  }
  return providerState.profiles.filter((profile) =>
    [profile.alias, profile.base_url, ...(profile.models || [])]
      .join(" ")
      .toLowerCase()
      .includes(query),
  );
}

function renderProviderPage() {
  renderProviderStatus();
  renderProviderList();
  renderProviderDetail();
}

function renderProviderStatus() {
  const items = providerState.profiles;
  const configured = items.filter(
    (profile) => (profile.base_url || "").trim() && (profile.api_key || "").trim(),
  ).length;
  const totalModels = items.reduce((sum, profile) => sum + (profile.models || []).length, 0);
  renderStrip(fields.providerStatusStrip, [
    { label: "供应商", value: items.length },
    { label: "已配置", value: configured },
    { label: "模型", value: totalModels },
  ]);
}

// 填充头像：优先用图标资源（如 DeepSeek SVG），其次 emoji，最后名称首字母。
function applyAvatar(avatar, { iconUrl, icon, initial } = {}) {
  avatar.textContent = "";
  avatar.classList.remove("is-initial");
  if (iconUrl) {
    const img = document.createElement("img");
    img.className = "provider-avatar-img";
    img.src = iconUrl;
    img.alt = "";
    avatar.append(img);
  } else if (icon) {
    avatar.textContent = icon;
  } else {
    avatar.classList.add("is-initial");
    avatar.textContent = (initial || "?").trim().charAt(0).toUpperCase() || "?";
  }
}

function providerAvatar(profile) {
  const avatar = document.createElement("span");
  avatar.className = "provider-avatar";
  const preset = presetForProfile(profile);
  applyAvatar(avatar, {
    iconUrl: preset?.iconUrl,
    icon: preset?.icon,
    initial: profile.alias || "?",
  });
  return avatar;
}

function renderProviderList() {
  fields.providerList.textContent = "";
  const profiles = filteredProviders();
  if (!profiles.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    if (providerState.profiles.length) {
      empty.textContent = "没有匹配的供应商。";
    } else {
      const text = document.createElement("p");
      text.className = "empty-state-text";
      text.textContent = "还没有供应商，先添加一个开始配置 API。";
      const cta = document.createElement("button");
      cta.type = "button";
      cta.className = "primary-button";
      cta.textContent = "添加供应商";
      cta.addEventListener("click", openAddProviderChooser);
      empty.append(text, cta);
    }
    fields.providerList.append(empty);
    return;
  }
  profiles.forEach((profile) => {
    const card = document.createElement("div");
    card.className = "provider-card";
    card.classList.toggle("is-selected", profile.id === providerState.selectedId);
    card.addEventListener("click", () => {
      providerState.selectedId = profile.id;
      renderProviderPage();
    });
    const body = document.createElement("div");
    body.className = "provider-card-body";
    const title = document.createElement("strong");
    title.textContent = profile.alias || profile.id;
    const meta = document.createElement("span");
    meta.className = "card-meta";
    meta.textContent = providerHost(profile.base_url) || "未设置 Base URL";
    body.append(title, meta);
    const count = document.createElement("span");
    count.className = "provider-count";
    count.textContent = `${(profile.models || []).length} 个模型`;
    card.append(providerAvatar(profile), body, count);
    fields.providerList.append(card);
  });
}

function renderProviderDetail() {
  const detail = fields.providerDetail;
  detail.textContent = "";
  const profile = providerState.profiles.find((item) => item.id === providerState.selectedId);
  if (!profile) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "选择左侧供应商查看与编辑配置。";
    detail.append(empty);
    return;
  }
  const title = document.createElement("h2");
  title.textContent = profile.alias || profile.id;
  detail.append(
    title,
    providerField(profile, "alias", "名称", "text"),
    providerField(profile, "base_url", "Base URL", "text"),
    providerField(profile, "api_key", "API Key", "password"),
    renderProviderModels(profile),
  );
  const actions = document.createElement("div");
  actions.className = "detail-actions";
  const testButton = document.createElement("button");
  testButton.type = "button";
  testButton.className = "secondary-button";
  testButton.textContent = "测试连接";
  testButton.addEventListener("click", () => testProvider(profile, testButton));
  const removeButton = document.createElement("button");
  removeButton.type = "button";
  removeButton.className = "danger-button";
  removeButton.textContent = "删除供应商";
  removeButton.addEventListener("click", () => removeProvider(profile));
  actions.append(testButton, removeButton);
  detail.append(actions);
}

function providerField(profile, key, label, type) {
  const row = document.createElement("div");
  row.className = "form-row";
  const labelEl = document.createElement("label");
  labelEl.textContent = label;
  const input = document.createElement("input");
  input.type = type === "password" ? "password" : "text";
  input.className = "wide-input";
  input.dataset.providerField = key;
  input.value = profile[key] || "";
  input.addEventListener("input", () => {
    profile[key] = input.value;
    if (input.value.trim()) {
      markInvalid(input, false);
    }
    if (key === "alias" || key === "base_url") {
      // 仅刷新左侧卡片与标题，避免重渲详情导致输入框失焦。
      renderProviderStatus();
      renderProviderList();
      if (key === "alias") {
        const heading = fields.providerDetail.querySelector("h2");
        if (heading) {
          heading.textContent = input.value.trim() || profile.id;
        }
      }
    } else if (key === "api_key") {
      renderProviderStatus();
    }
  });
  row.append(labelEl, input);
  return row;
}

function renderProviderModels(profile) {
  const section = document.createElement("div");
  section.className = "provider-models";
  const head = document.createElement("div");
  head.className = "provider-models-head";
  const heading = document.createElement("h3");
  heading.textContent = "模型";
  const detectButton = document.createElement("button");
  detectButton.type = "button";
  detectButton.className = "secondary-button compact-button";
  detectButton.textContent = "自动检测";
  detectButton.addEventListener("click", () => autoDetectModels(profile, detectButton));
  head.append(heading, detectButton);
  section.append(head);

  const list = document.createElement("div");
  list.className = "model-chip-list";
  if (!(profile.models || []).length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = "还没有模型，点「自动检测」或在下方手动添加。";
    list.append(empty);
  } else {
    profile.models.forEach((model) => {
      const chip = document.createElement("span");
      chip.className = "model-chip";
      const name = document.createElement("span");
      name.textContent = model;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "model-chip-remove";
      remove.setAttribute("aria-label", `删除 ${model}`);
      remove.textContent = "×";
      remove.addEventListener("click", () => {
        profile.models = profile.models.filter((item) => item !== model);
        renderProviderPage();
        refreshModelSlots();
      });
      chip.append(name, remove);
      list.append(chip);
    });
  }
  section.append(list);

  const addRow = document.createElement("div");
  addRow.className = "model-add-row";
  const input = document.createElement("input");
  input.type = "text";
  input.className = "wide-input";
  input.placeholder = "手动添加模型 ID";
  const addButton = document.createElement("button");
  addButton.type = "button";
  addButton.className = "secondary-button compact-button";
  addButton.textContent = "添加";
  const commit = () => {
    const value = input.value.trim();
    if (!value) {
      return;
    }
    const added = addModelsToProfile(profile, [value]);
    input.value = "";
    setError(added ? "" : "该模型已存在。");
  };
  addButton.addEventListener("click", commit);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      commit();
    }
  });
  addRow.append(input, addButton);
  section.append(addRow);
  return section;
}

function addModelsToProfile(profile, models) {
  if (!Array.isArray(profile.models)) {
    profile.models = [];
  }
  const existing = new Set(profile.models);
  let added = 0;
  models.forEach((model) => {
    const name = String(model || "").trim();
    if (name && !existing.has(name)) {
      existing.add(name);
      profile.models.push(name);
      added += 1;
    }
  });
  if (added) {
    renderProviderPage();
    refreshModelSlots();
  }
  return added;
}

function providerDetailInput(key) {
  return fields.providerDetail.querySelector(`[data-provider-field="${key}"]`);
}

async function autoDetectModels(profile, button) {
  const baseUrl = (profile.base_url || "").trim();
  const apiKey = (profile.api_key || "").trim();
  if (!baseUrl) {
    markInvalid(providerDetailInput("base_url"), true);
    setError("请先填写 Base URL。");
    return;
  }
  if (!apiKey) {
    markInvalid(providerDetailInput("api_key"), true);
    setError("请先填写 API Key。");
    return;
  }
  setError("");
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "检测中…";
  try {
    const result = await hostCall("api.list_models", {
      base_url: baseUrl,
      api_key: apiKey,
      timeout_seconds: request?.api?.settings?.timeout_seconds || 60,
    });
    const models = Array.isArray(result?.models) ? result.models : [];
    if (!models.length) {
      notify("未检测到任何模型。", "info");
      return;
    }
    openModelPicker(profile, models);
  } catch (error) {
    setError(`自动检测失败：${error}`);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function testProvider(profile, button) {
  const baseUrl = (profile.base_url || "").trim();
  const apiKey = (profile.api_key || "").trim();
  const model = (profile.models || [])[0];
  if (!baseUrl || !apiKey) {
    markInvalid(providerDetailInput("base_url"), !baseUrl);
    markInvalid(providerDetailInput("api_key"), !apiKey);
    setError("请先填写 Base URL 与 API Key。");
    return;
  }
  if (!model) {
    setError("请先添加至少一个模型再测试。");
    return;
  }
  setError("");
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "测试中…";
  try {
    const result = await hostCall("api.test_connection", {
      base_url: baseUrl,
      api_key: apiKey,
      model,
      timeout_seconds: request?.api?.settings?.timeout_seconds || 60,
    });
    notify(`连接成功：${result?.message || "OK"}`, "success");
  } catch (error) {
    setError(`连接失败：${error}`);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

function removeProvider(profile) {
  providerState.profiles = providerState.profiles.filter((item) => item.id !== profile.id);
  if (providerState.selectedId === profile.id) {
    providerState.selectedId = providerState.profiles[0]?.id || "";
  }
  renderProviderPage();
  refreshModelSlots();
}

function addProvider(preset) {
  const profile = {
    id: makeProfileId(),
    alias: preset?.label || "新供应商",
    base_url: preset?.base_url || "",
    api_key: "",
    models: [],
  };
  providerState.profiles.push(profile);
  providerState.selectedId = profile.id;
  providerState.search = "";
  if (fields.providerSearch) {
    fields.providerSearch.value = "";
  }
  renderProviderPage();
  refreshModelSlots();
}

function makeModalButton(text, className, handler) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = text;
  button.addEventListener("click", handler);
  return button;
}

function openAddProviderChooser() {
  const overlay = document.createElement("div");
  overlay.className = "confirm-overlay";
  const dialog = document.createElement("div");
  dialog.className = "confirm-dialog provider-add-dialog";
  const heading = document.createElement("h2");
  heading.textContent = "添加供应商";
  const grid = document.createElement("div");
  grid.className = "provider-preset-grid";
  const close = () => overlay.remove();
  PROVIDER_PRESETS.forEach((preset) => {
    const option = makeModalButton("", "provider-preset-option", () => {
      addProvider(preset);
      close();
    });
    const icon = document.createElement("span");
    icon.className = "provider-avatar";
    applyAvatar(icon, { iconUrl: preset.iconUrl, icon: preset.icon, initial: preset.label });
    const label = document.createElement("span");
    label.textContent = preset.label;
    option.append(icon, label);
    grid.append(option);
  });
  const custom = makeModalButton("", "provider-preset-option", () => {
    addProvider(null);
    close();
  });
  const customIcon = document.createElement("span");
  customIcon.className = "provider-avatar is-initial";
  customIcon.textContent = "＋";
  const customLabel = document.createElement("span");
  customLabel.textContent = "自定义";
  custom.append(customIcon, customLabel);
  grid.append(custom);
  const actions = document.createElement("div");
  actions.className = "confirm-actions";
  actions.append(makeModalButton("取消", "secondary-button", close));
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) {
      close();
    }
  });
  dialog.append(heading, grid, actions);
  overlay.append(dialog);
  document.body.append(overlay);
}

function openModelPicker(profile, models) {
  const existing = new Set(profile.models || []);
  const overlay = document.createElement("div");
  overlay.className = "confirm-overlay";
  const dialog = document.createElement("div");
  dialog.className = "confirm-dialog model-picker-dialog";
  const heading = document.createElement("h2");
  heading.textContent = `检测到 ${models.length} 个模型`;
  const toolbar = document.createElement("div");
  toolbar.className = "model-picker-toolbar";
  const body = document.createElement("div");
  body.className = "model-picker-list";
  const checks = models.map((model) => {
    const item = document.createElement("label");
    item.className = "check-control model-picker-item";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = model;
    checkbox.checked = !existing.has(model);
    const text = document.createElement("span");
    text.textContent = existing.has(model) ? `${model}（已添加）` : model;
    item.append(checkbox, text);
    body.append(item);
    return checkbox;
  });
  const setAll = (predicate) => checks.forEach((checkbox) => {
    checkbox.checked = predicate(checkbox);
  });
  toolbar.append(
    makeModalButton("全选", "secondary-button compact-button", () => setAll(() => true)),
    makeModalButton("只选新增", "secondary-button compact-button", () =>
      setAll((checkbox) => !existing.has(checkbox.value)),
    ),
    makeModalButton("全不选", "secondary-button compact-button", () => setAll(() => false)),
  );
  const actions = document.createElement("div");
  actions.className = "confirm-actions";
  const close = () => overlay.remove();
  actions.append(
    makeModalButton("取消", "secondary-button", close),
    makeModalButton("添加", "primary-button", () => {
      const chosen = checks.filter((checkbox) => checkbox.checked).map((checkbox) => checkbox.value);
      const added = addModelsToProfile(profile, chosen);
      close();
      notify(added ? `已添加 ${added} 个模型。` : "没有新增模型。", added ? "success" : "info");
    }),
  );
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) {
      close();
    }
  });
  dialog.append(heading, toolbar, body, actions);
  overlay.append(dialog);
  document.body.append(overlay);
}

function modelSlotElements(slot) {
  return {
    inheritInput: fields.modelSlots.querySelector(`[data-slot-inherit="${slot}"]`),
    profileSelect: fields.modelSlots.querySelector(`[data-slot-profile="${slot}"]`),
    modelSelect: fields.modelSlots.querySelector(`[data-slot-model="${slot}"]`),
  };
}

function readSlotSelection(slot) {
  const { profileSelect, modelSelect } = modelSlotElements(slot);
  return {
    profile_id: profileSelect?.value || "",
    model: modelSelect?.value || "",
  };
}

function setSlotSelection(slot, selection, { preserveMissing = true } = {}) {
  const { profileSelect, modelSelect } = modelSlotElements(slot);
  if (!profileSelect || !modelSelect) {
    return;
  }
  const profileId = selection?.profile_id || "";
  if (profileId && Array.from(profileSelect.options).some((option) => option.value === profileId)) {
    profileSelect.value = profileId;
    refreshSelect(profileSelect);
  }
  syncModelOptions(slot, selection?.model || "", { preserveMissing });
}

function inheritedSlotSourceSelection(slot) {
  if (slot === "chat") {
    return null;
  }
  const chat = readSlotSelection("chat");
  return chat.profile_id && chat.model ? chat : null;
}

function syncInheritedSlotDisplays() {
  request.api.slot_fields.forEach((slot) => {
    const inheritInput = fields.modelSlots.querySelector(`[data-slot-inherit="${slot.id}"]`);
    if (inheritInput?.checked) {
      syncSlotInheritState(slot.id);
    }
  });
}

function handleSlotInheritChange(slot) {
  const { inheritInput } = modelSlotElements(slot);
  if (inheritInput?.checked) {
    const current = readSlotSelection(slot);
    if (current.profile_id && current.model) {
      inheritedSlotManualSelections[slot] = current;
    }
  } else if (inheritedSlotManualSelections[slot]) {
    setSlotSelection(slot, inheritedSlotManualSelections[slot], { preserveMissing: true });
    delete inheritedSlotManualSelections[slot];
  }
  syncSlotInheritState(slot);
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
    if (!slot.required) {
      const inheritLabel = document.createElement("label");
      inheritLabel.className = "check-control slot-inherit";
      const inheritInput = document.createElement("input");
      inheritInput.type = "checkbox";
      inheritInput.dataset.slotInherit = slot.id;
      const inheritText = document.createElement("span");
      inheritText.textContent = "继承";
      inheritLabel.append(inheritInput, inheritText);
      controls.append(inheritLabel);
      inheritInput.addEventListener("change", () => handleSlotInheritChange(slot.id));
    }
    controls.append(profileSelect, modelSelect);
    row.append(label, controls);
    fields.modelSlots.append(row);
    enhanceSelect(profileSelect);
    enhanceSelect(modelSelect);
    profileSelect.addEventListener("change", () => {
      syncModelOptions(slot.id, "", { preserveMissing: false });
      if (slot.id === "chat") {
        syncInheritedSlotDisplays();
      }
    });
    modelSelect.addEventListener("change", () => {
      if (slot.id === "chat") {
        syncInheritedSlotDisplays();
      }
    });
    const selected = selection?.slots?.[slot.id] || { profile_id: "", model: "" };
    const inheritInput = fields.modelSlots.querySelector(`[data-slot-inherit="${slot.id}"]`);
    if (inheritInput) {
      inheritInput.checked = !selected.profile_id || !selected.model;
    }
    fillProfileOptions(profileSelect, selected.profile_id, slot.required);
    syncModelOptions(slot.id, selected.model, { preserveMissing: true });
    syncSlotInheritState(slot.id);
  });
}

function fillProfileOptions(select, selectedId, required) {
  const profiles = providerState.profiles;
  select.textContent = "";
  profiles.forEach((profile) => {
    const option = document.createElement("option");
    option.value = profile.id;
    option.textContent = profile.alias || profile.id;
    select.append(option);
  });
  // 选中的供应商可能已被删除：回退到首个供应商。
  const ids = profiles.map((profile) => profile.id);
  let value = ids.includes(selectedId) ? selectedId : "";
  if (!value && profiles[0]) {
    value = profiles[0].id;
  }
  select.value = value;
  refreshSelect(select);
}

function syncModelOptions(slot, selectedModel, { preserveMissing = selectedModel !== undefined } = {}) {
  const profileSelect = fields.modelSlots.querySelector(`[data-slot-profile="${slot}"]`);
  const modelSelect = fields.modelSlots.querySelector(`[data-slot-model="${slot}"]`);
  const profile = providerState.profiles.find((item) => item.id === profileSelect.value);
  const models = profile?.models || [];
  const current = selectedModel ?? "";
  modelSelect.textContent = "";
  if (!profileSelect.value) {
    refreshSelect(modelSelect);
    return;
  }
  models.forEach((model) => {
    const option = document.createElement("option");
    option.value = model;
    option.textContent = model;
    modelSelect.append(option);
  });
  if (preserveMissing && current && !models.includes(current)) {
    const option = document.createElement("option");
    option.value = current;
    option.textContent = current;
    modelSelect.append(option);
  }
  modelSelect.value = current || models[0] || "";
  refreshSelect(modelSelect);
}

function syncSlotInheritState(slot) {
  const inheritInput = fields.modelSlots.querySelector(`[data-slot-inherit="${slot}"]`);
  const inherited = Boolean(inheritInput?.checked);
  const profileSelect = fields.modelSlots.querySelector(`[data-slot-profile="${slot}"]`);
  const modelSelect = fields.modelSlots.querySelector(`[data-slot-model="${slot}"]`);
  if (inherited) {
    const inheritedSelection = inheritedSlotSourceSelection(slot);
    if (inheritedSelection) {
      setSlotSelection(slot, inheritedSelection, { preserveMissing: true });
    }
  }
  if (profileSelect) {
    profileSelect.disabled = inherited;
    refreshSelect(profileSelect);
  }
  if (modelSelect) {
    modelSelect.disabled = inherited;
    refreshSelect(modelSelect);
  }
}

function refreshModelSlots() {
  renderModelSlots(collectModelSelection());
}

function collectModelSelection() {
  const slots = {};
  request.api.slot_fields.forEach((slot) => {
    const inherited = fields.modelSlots.querySelector(`[data-slot-inherit="${slot.id}"]`)?.checked;
    slots[slot.id] = {
      profile_id: inherited ? "" : fields.modelSlots.querySelector(`[data-slot-profile="${slot.id}"]`)?.value || "",
      model: inherited ? "" : fields.modelSlots.querySelector(`[data-slot-model="${slot.id}"]`)?.value || "",
    };
  });
  return { slots };
}

function renderTtsProviders() {
  fields.ttsProvider.textContent = "";
  request.tts.providers.filter((provider) => provider.id !== "none").forEach((provider) => {
    const option = document.createElement("option");
    option.value = provider.id;
    option.textContent = provider.label;
    fields.ttsProvider.append(option);
  });
}

function setTtsProviderValue(provider) {
  fields.ttsProvider.value = provider === "none" ? "" : provider;
  if (!fields.ttsProvider.value) {
    fields.ttsProvider.value = request.tts.providers.find((item) => item.id !== "none")?.id || "gpt-sovits";
  }
}

async function hostCall(method, params = {}) {
  return invoke("host_call", { method, params });
}

function characterExportDefaultName(kind) {
  const id = selectedCharacter()?.id || "character";
  if (kind === "voice") {
    return `${id}.voice`;
  }
  if (kind === "card") {
    return `${id}.card.char`;
  }
  return `${id}.char`;
}

function archiveDialogFilter(kind) {
  if (kind === "voice") {
    return [{ name: "Sakura TTS 模型包", extensions: ["voice"] }];
  }
  return [{ name: "Sakura 角色包", extensions: ["char"] }];
}

async function chooseArchivePath(kind) {
  const title = kind === "voice" ? "导入 Sakura TTS 模型包" : "导入 Sakura 角色包";
  const dialogApi = window.__TAURI__?.dialog;
  if (dialogApi?.open) {
    const selected = await dialogApi.open({
      title,
      multiple: false,
      filters: archiveDialogFilter(kind),
    });
    return Array.isArray(selected) ? selected[0] : selected;
  }
  return window.prompt(`${title}\n请输入文件完整路径：`, "") || "";
}

async function chooseExportPath(kind) {
  const title = kind === "voice"
    ? "导出 Sakura TTS 模型包"
    : (kind === "card" ? "导出 Sakura 单角色包" : "导出 Sakura 完整角色包");
  const dialogApi = window.__TAURI__?.dialog;
  const defaultPath = characterExportDefaultName(kind);
  if (dialogApi?.save) {
    return dialogApi.save({
      title,
      defaultPath,
      filters: archiveDialogFilter(kind),
    });
  }
  return window.prompt(`${title}\n请输入保存路径：`, defaultPath) || "";
}

function chooseExportKind() {
  return new Promise((resolve) => {
    const hasVoice = selectedCharacterHasExportableVoice();
    const overlay = document.createElement("div");
    overlay.className = "confirm-overlay";
    const dialog = document.createElement("section");
    dialog.className = "confirm-dialog export-kind-dialog";
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    const heading = document.createElement("h2");
    heading.textContent = "选择导出内容";
    const body = document.createElement("div");
    body.className = "export-kind-list";

    characterExportOptions.forEach((option) => {
      const disabled = option.requiresVoice && !hasVoice;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "export-kind-option";
      button.disabled = disabled;
      const title = document.createElement("span");
      title.className = "export-kind-title";
      title.textContent = option.label;
      const desc = document.createElement("span");
      desc.className = "export-kind-desc";
      desc.textContent = disabled
        ? `${option.description} 当前角色没有可导出的语音模型。`
        : option.description;
      button.append(title, desc);
      button.addEventListener("click", () => close(option.kind));
      body.append(button);
    });

    const actions = document.createElement("div");
    actions.className = "confirm-actions";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "secondary-button";
    cancel.textContent = "取消";
    actions.append(cancel);
    dialog.append(heading, body, actions);
    overlay.append(dialog);

    function close(kind) {
      document.removeEventListener("keydown", onKey, true);
      overlay.remove();
      resolve(kind || "");
    }
    function onKey(event) {
      if (event.key === "Escape") {
        close("");
      }
    }
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) {
        close("");
      }
    });
    cancel.addEventListener("click", () => close(""));
    document.addEventListener("keydown", onKey, true);
    document.body.append(overlay);
    dialog.querySelector("button:not(:disabled)")?.focus();
  });
}

function applyCharacterRpcResult(result, { dirty = true, applyTheme = false } = {}) {
  if (Array.isArray(result?.characters)) {
    request.character.characters = result.characters;
  }
  if (result?.current_character_id) {
    request.character.current_character_id = result.current_character_id;
  }
  renderCharacters();
  refreshSelect(fields.characterSelect);
  if (result?.current_character_id) {
    fields.characterSelect.value = result.current_character_id;
    refreshSelect(fields.characterSelect);
  }
  if (result?.disable_tts) {
    fields.ttsEnabled.checked = false;
  }
  if (applyTheme) {
    applySelectedCharacterTheme();
  }
  syncTtsState();
  syncCharacterArchiveState();
  if (dirty) {
    scheduleDirty();
  }
  if (result?.message) {
    notify(result.message, "success");
  }
}

async function runCharacterArchiveAction(action) {
  if (!request || characterArchiveBusy) {
    return;
  }
  setError("");
  setCharacterArchiveBusy(true);
  try {
    await action();
  } catch (error) {
    setError(String(error));
  } finally {
    setCharacterArchiveBusy(false);
  }
}

async function importCharacterArchive() {
  await runCharacterArchiveAction(async () => {
    const path = String(await chooseArchivePath("character") || "").trim();
    if (!path) {
      return;
    }
    const result = await hostCall("character.import_archive", { path });
    applyCharacterRpcResult(result, { dirty: true, applyTheme: true });
  });
}

async function importCharacterVoiceArchive() {
  await runCharacterArchiveAction(async () => {
    const character = selectedCharacter();
    if (!character) {
      setError("请先选择一个角色。");
      return;
    }
    const path = String(await chooseArchivePath("voice") || "").trim();
    if (!path) {
      return;
    }
    const result = await hostCall("character.import_voice_archive", {
      path,
      character_id: character.id,
    });
    applyCharacterRpcResult(result, { dirty: false });
  });
}

async function exportCharacterArchive() {
  await runCharacterArchiveAction(async () => {
    const character = selectedCharacter();
    if (!character) {
      setError("当前没有可导出的角色。");
      return;
    }
    const kind = await chooseExportKind();
    if (!kind) {
      return;
    }
    const path = String(await chooseExportPath(kind) || "").trim();
    if (!path) {
      return;
    }
    const result = await hostCall("character.export_archive", {
      path,
      character_id: character.id,
      kind,
    });
    applyCharacterRpcResult(result, { dirty: false });
  });
}

function resourcesSnapshot() {
  return resourceState.snapshot || request?.resources || {};
}

function taskFor(kind) {
  const snapshot = resourcesSnapshot();
  if (kind === "tts") {
    return snapshot.tts?.task || snapshot.tasks?.tts || null;
  }
  if (kind === "backchannel") {
    return snapshot.backchannel?.task || snapshot.tasks?.backchannel || null;
  }
  if (kind === "memory_model") {
    return snapshot.memory_model?.task || snapshot.tasks?.memory_model || null;
  }
  return null;
}

function taskRunning(task) {
  return task?.status === "running" || task?.status === "queued";
}

function hasRunningResourceTask(snapshot = resourcesSnapshot()) {
  return ["tts", "backchannel", "memory_model"].some((kind) => {
    const task =
      kind === "tts"
        ? snapshot.tts?.task || snapshot.tasks?.tts
        : kind === "backchannel"
          ? snapshot.backchannel?.task || snapshot.tasks?.backchannel
          : snapshot.memory_model?.task || snapshot.tasks?.memory_model;
    return taskRunning(task);
  });
}

function resourceStatusLabel(status, ready = false) {
  if (status === "not_required") {
    return "无需";
  }
  if (status === "running" || status === "queued") {
    return "处理中";
  }
  if (status === "succeeded") {
    return "已完成";
  }
  if (status === "failed") {
    return "失败";
  }
  if (status === "cancelled") {
    return "可继续";
  }
  return ready ? "已就绪" : "缺失";
}

function resourceStatusClass(status, ready = false) {
  if (status === "not_required") {
    return "is-ready";
  }
  if (status === "running" || status === "queued") {
    return "is-running";
  }
  if (status === "succeeded" || ready) {
    return "is-ready";
  }
  if (status === "failed") {
    return "is-failed";
  }
  if (status === "cancelled") {
    return "is-paused";
  }
  return "is-missing";
}

function renderResourceCards() {
  renderTtsResourceCard();
  renderBackchannelResourceCard();
  renderMemoryModelResourceCard();
}

function renderResourceCard(container, model) {
  if (!container) {
    return;
  }
  container.textContent = "";
  container.classList.toggle("is-muted", Boolean(model.muted));
  const head = document.createElement("div");
  head.className = "resource-card__head";
  const titleWrap = document.createElement("div");
  titleWrap.className = "resource-card__title-wrap";
  const title = document.createElement("strong");
  title.textContent = model.title;
  const subtitle = document.createElement("span");
  subtitle.textContent = model.subtitle || "";
  titleWrap.append(title, subtitle);
  const badge = document.createElement("span");
  badge.className = `resource-badge ${resourceStatusClass(model.status, model.ready)}`;
  badge.textContent = resourceStatusLabel(model.status, model.ready);
  head.append(titleWrap, badge);

  const body = document.createElement("div");
  body.className = "resource-card__body";
  if (model.message) {
    const message = document.createElement("p");
    message.className = "resource-message";
    message.textContent = model.message;
    body.append(message);
  }
  if (model.detail) {
    const detail = document.createElement("p");
    detail.className = "resource-detail";
    detail.textContent = model.detail;
    body.append(detail);
  }
  if (model.progressVisible) {
    const progress = document.createElement("div");
    progress.className = "resource-progress";
    const bar = document.createElement("span");
    bar.style.width = `${Math.max(0, Math.min(100, Number(model.progress || 0)))}%`;
    progress.append(bar);
    body.append(progress);
  }
  if (Array.isArray(model.meta) && model.meta.length) {
    const meta = document.createElement("dl");
    meta.className = "resource-meta";
    model.meta.forEach(([label, value]) => {
      if (!value) {
        return;
      }
      const dt = document.createElement("dt");
      dt.textContent = label;
      const dd = document.createElement("dd");
      dd.textContent = value;
      meta.append(dt, dd);
    });
    body.append(meta);
  }

  const actions = document.createElement("div");
  actions.className = "resource-actions";
  (model.actions || []).forEach((action) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = action.primary ? "" : action.danger ? "danger-button" : "secondary-button";
    button.textContent = action.label;
    button.disabled = Boolean(action.disabled);
    button.addEventListener("click", action.onClick);
    actions.append(button);
  });
  if (actions.childNodes.length) {
    body.append(actions);
  }
  container.append(head, body);
}

function selectedTtsBundle() {
  const resources = resourcesSnapshot().tts || {};
  const bundles = Array.isArray(resources.bundles) ? resources.bundles : [];
  const provider = fields.ttsProvider.value;
  const providerBundles = ttsBundlesForProvider(provider, bundles);
  if (!providerBundles.length) {
    return null;
  }

  const providerRecommendedKey = ttsProviderRecommendedKey(provider, resources);
  let selected = providerBundles.find((bundle) => bundle.key === resourceState.ttsBundleKey);
  if (!selected) {
    selected = providerBundles.find((bundle) => bundle.key === providerRecommendedKey) || providerBundles[0];
  }
  if (selected) {
    resourceState.ttsBundleKey = selected.key;
  }
  return selected || null;
}

function ttsBundlesForProvider(provider, bundles) {
  if (provider === "genie-tts") {
    return bundles.filter((bundle) => bundle.provider === "genie-tts");
  }
  if (provider === "gpt-sovits") {
    return bundles.filter((bundle) => bundle.provider === "gpt-sovits");
  }
  return bundles.filter((bundle) => bundle.provider === provider);
}

function ttsProviderRecommendedKey(provider, resources) {
  if (provider === "genie-tts") {
    return resources.genie_key || "";
  }
  if (provider === "gpt-sovits") {
    return resources.gpt_sovits_recommended_key || "";
  }
  return resources.recommended_key || "";
}

function ttsProviderLabel(provider) {
  if (provider === "genie-tts") {
    return "Genie TTS";
  }
  if (provider === "gpt-sovits") {
    return "GPT-SoVITS";
  }
  if (provider === "custom-gpt-sovits") {
    return "外部 GPT-SoVITS";
  }
  return "TTS";
}

function ttsInstallActionLabel(provider, selected, ready, running, task) {
  if (running) {
    return "处理中";
  }
  if (ready) {
    return "重新安装";
  }
  if (task?.status === "cancelled") {
    return "继续安装";
  }
  if (provider === "genie-tts") {
    return "安装 Genie CPU 包";
  }
  if (selected?.variant === "gpt-sovits-50") {
    return "安装 SoVITS 50 系包";
  }
  if (provider === "gpt-sovits") {
    return "安装 SoVITS 通用包";
  }
  return "安装推荐包";
}

function renderTtsBundleSelector(container, bundles, selectedKey, disabled) {
  const row = document.createElement("label");
  row.className = "resource-select-row";
  const label = document.createElement("span");
  label.textContent = "整合包";
  const select = document.createElement("select");
  select.disabled = disabled;
  bundles.forEach((bundle) => {
    const option = document.createElement("option");
    option.value = bundle.key;
    option.textContent = bundle.display_label || bundle.label || bundle.key;
    select.append(option);
  });
  select.value = selectedKey;
  select.addEventListener("change", () => {
    resourceState.ttsBundleKey = select.value;
    renderResourceCards();
  });
  row.append(label, select);
  container.append(row);
}

async function copyResourceDiagnostic(kind, task, context = {}) {
  const diagnostic = JSON.stringify(
    {
      kind,
      status: task?.status || "",
      stage: task?.stage || "",
      message: task?.message || "",
      detail: task?.detail || "",
      error: task?.error || "",
      result: task?.result || {},
      context,
    },
    null,
    2,
  );
  try {
    await navigator.clipboard.writeText(diagnostic);
    notify("诊断信息已复制。", "success");
  } catch (_error) {
    window.prompt("复制以下诊断信息：", diagnostic);
  }
}

function renderTtsResourceCard() {
  const resources = resourcesSnapshot().tts || {};
  const bundles = Array.isArray(resources.bundles) ? resources.bundles : [];
  const provider = fields.ttsProvider.value;
  const providerBundles = ttsBundlesForProvider(provider, bundles);
  const task = taskFor("tts");
  const running = taskRunning(task);
  const selected = selectedTtsBundle();
  const taskBundleKey = task?.context?.bundle_key || "";
  const taskMatchesSelected = !taskBundleKey || taskBundleKey === selected?.key;
  const muted = !fields.ttsEnabled.checked || provider === "none" || provider === "custom-gpt-sovits";
  const ready = Boolean(selected?.installed) || (taskMatchesSelected && task?.status === "succeeded");
  const providerLabel = ttsProviderLabel(provider);
  const providerHint = ttsProviderResourceHint(provider, resources, selected);
  const message = muted
    ? !fields.ttsEnabled.checked || provider === "none"
      ? "TTS 已关闭。"
      : provider === "custom-gpt-sovits"
      ? "外部 GPT-SoVITS 使用你填写的本机路径。"
      : "无需内置资源。"
    : running
      ? taskMatchesSelected
        ? task.message || `正在处理 ${providerLabel} 整合包。`
        : `后台正在处理 ${task.title || "其他 TTS 整合包"}，当前展示 ${providerLabel} 的安装信息。`
      : ready
        ? `${providerLabel} 本地运行环境已就绪。`
        : `${providerLabel} 需要安装对应的本地整合包。`;
  const detail = running && taskMatchesSelected
    ? task.detail || ""
    : providerHint
      ? providerHint
      : selected
      ? `${selected.variant_label || selected.display_label || selected.label} · ${selected.work_dir || "待安装"}`
      : resources.gpu_summary || "";
  const actions = [];
  if (!muted && selected) {
    actions.push({
      label: ttsInstallActionLabel(provider, selected, ready, running, task),
      primary: !ready,
      disabled: running,
      onClick: () => startResourceAction("resources.tts.install", { bundle_key: selected.key }),
    });
    if (running && task?.cancellable) {
      actions.push({
        label: "暂停",
        danger: true,
        onClick: () => startResourceAction("resources.tts.cancel"),
      });
    }
  }
  if (task?.status === "failed") {
    actions.push({
      label: "复制诊断",
      onClick: () => copyResourceDiagnostic("tts", task, { provider, bundle_key: selected?.key || "" }),
    });
  }
  actions.push({ label: "刷新", onClick: refreshResources });

  renderResourceCard(fields.ttsResourceCard, {
    title: `${providerLabel} 整合包`,
    subtitle: muted
      ? "无需内置资源"
      : selected?.variant_label || resources.platform || "",
    status: muted ? "not_required" : task?.status || "",
    ready,
    muted,
    message,
    detail,
    progressVisible: running && taskMatchesSelected,
    progress: task?.progress || 0,
    meta: [
      ["平台", resources.platform],
      ["显卡", provider === "gpt-sovits" ? resources.gpu_summary : ""],
      ["下载源", selected?.download_url],
      ["安装目录", selected?.work_dir],
    ],
    actions,
  });
  if (!muted && providerBundles.length > 1 && fields.ttsResourceCard) {
    const body = fields.ttsResourceCard.querySelector(".resource-card__body");
    if (body) {
      renderTtsBundleSelector(body, providerBundles, selected?.key || "", running);
    }
  }
}

function ttsProviderResourceHint(provider, resources, selected) {
  if (provider === "genie-tts") {
    return selected
      ? `${selected.display_label || selected.label} · 使用 CPU 整合包，不依赖 NVIDIA 显卡。`
      : "Genie TTS 使用 CPU 整合包，不依赖 NVIDIA 显卡。";
  }
  if (provider !== "gpt-sovits") {
    return "";
  }
  const status = resources.gpu_status?.gpt_sovits;
  if (!status) {
    return resources.gpu_summary || "";
  }
  const selectedText = selected?.variant_label ? `当前选择：${selected.variant_label}。` : "";
  if (status.severity === "warning") {
    return `${status.message} ${status.vram_note || ""} ${selectedText}`.trim();
  }
  return "";
}

function renderBackchannelResourceCard() {
  const resources = resourcesSnapshot().backchannel || {};
  const task = taskFor("backchannel");
  const running = taskRunning(task);
  const mode = fields.backchannelMode.value;
  const enabled = fields.backchannelEnabled.checked;
  const ready = Boolean(resources.ready) || task?.status === "succeeded";
  const wantsModel = enabled && mode === "hybrid";
  const message = running
    ? task.message || "正在处理接话模型。"
    : ready
      ? mode === "hybrid"
        ? "智能辅助已就绪。"
        : "本地接话模型已就绪，切换到智能辅助后启用。"
      : wantsModel
        ? "智能辅助还没有本地接话模型，当前会先用规则模式。"
        : "规则模式不需要本地模型，可先安装备用。";
  const actions = [
    {
      label: running ? "安装中" : ready ? "重新安装" : "在线安装",
      primary: !ready,
      disabled: running,
      onClick: () => startResourceAction("resources.backchannel.download"),
    },
    {
      label: "导入 ZIP",
      disabled: running,
      onClick: () => importResourceZip("backchannel"),
    },
  ];
  if (task?.status === "failed") {
    actions.push({
      label: "复制诊断",
      onClick: () => copyResourceDiagnostic("backchannel", task, { mode, enabled, model_name: resources.model_name || "" }),
    });
  }
  actions.push({ label: "刷新", onClick: refreshResources });

  renderResourceCard(fields.backchannelResourceCard, {
    title: "接话模型",
    subtitle: resources.model_name || "",
    status: task?.status || "",
    ready,
    message,
    detail: running ? task.detail || "" : resources.cache_folder || resources.endpoint || "",
    progressVisible: running,
    progress: task?.progress || (running ? 35 : 0),
    meta: [
      ["端点", resources.endpoint],
      ["缓存", resources.cache_folder],
      ["错误", task?.error],
    ],
    actions,
  });
}

function renderMemoryModelResourceCard() {
  const resources = resourcesSnapshot().memory_model || {};
  const task = taskFor("memory_model");
  const running = taskRunning(task);
  const ready = Boolean(resources.ready) || task?.status === "succeeded";
  const available = resources.available !== false;
  const message = !available
    ? "长期记忆系统暂不可用。"
    : running
      ? task.message || "正在处理记忆模型。"
      : ready
        ? "记忆模型已就绪。"
        : "记忆检索需要本地嵌入模型。";
  const actions = [
    {
      label: running ? "安装中" : ready ? "重新安装" : "在线安装",
      primary: !ready,
      disabled: running || !available,
      onClick: () => startResourceAction("resources.memory.download"),
    },
    {
      label: "导入 ZIP",
      disabled: running || !available,
      onClick: () => importResourceZip("memory"),
    },
  ];
  if (task?.status === "failed") {
    actions.push({
      label: "复制诊断",
      onClick: () => copyResourceDiagnostic("memory_model", task, { model_name: resources.model_name || "" }),
    });
  }
  actions.push({ label: "刷新", disabled: !available, onClick: refreshResources });

  renderResourceCard(fields.memoryModelResourceCard, {
    title: "记忆模型",
    subtitle: resources.model_name || "",
    status: task?.status || "",
    ready,
    muted: !available,
    message,
    detail: running ? task.detail || "" : resources.error || "",
    progressVisible: running,
    progress: task?.progress || (running ? 35 : 0),
    meta: [
      ["缓存", task?.result?.cache_folder],
      ["错误", task?.error || resources.error],
    ],
    actions,
  });
}

async function refreshResources() {
  if (!request) {
    return;
  }
  const previous = resourcesSnapshot();
  try {
    const snapshot = await hostCall("resources.status");
    resourceState.snapshot = snapshot;
    handleResourceTaskTransitions(previous, snapshot);
    renderResourceCards();
    if (hasRunningResourceTask(snapshot)) {
      startResourcePolling();
    } else {
      stopResourcePolling();
    }
  } catch (error) {
    setError(String(error));
  }
}

async function startResourceAction(method, params = {}) {
  setError("");
  const previous = resourcesSnapshot();
  try {
    const snapshot = await hostCall(method, params);
    resourceState.snapshot = snapshot;
    handleResourceTaskTransitions(previous, snapshot);
    renderResourceCards();
    if (hasRunningResourceTask(snapshot)) {
      startResourcePolling();
    }
  } catch (error) {
    setError(String(error));
  }
}

function startResourcePolling() {
  if (resourceState.pollTimer) {
    return;
  }
  resourceState.pollTimer = window.setInterval(refreshResources, 1200);
}

function stopResourcePolling() {
  window.clearInterval(resourceState.pollTimer);
  resourceState.pollTimer = null;
}

function handleResourceTaskTransitions(previous, next) {
  const pairs = [
    ["tts", previous?.tts?.task || previous?.tasks?.tts, next?.tts?.task || next?.tasks?.tts],
    [
      "backchannel",
      previous?.backchannel?.task || previous?.tasks?.backchannel,
      next?.backchannel?.task || next?.tasks?.backchannel,
    ],
    [
      "memory_model",
      previous?.memory_model?.task || previous?.tasks?.memory_model,
      next?.memory_model?.task || next?.tasks?.memory_model,
    ],
  ];
  pairs.forEach(([kind, before, after]) => {
    if (!after || after.status !== "succeeded") {
      return;
    }
    const finishKey = `${kind}:${after.finished_at || ""}`;
    if (!after.finished_at || resourceState.seenTaskFinishes[finishKey]) {
      return;
    }
    if (before?.status === "succeeded") {
      resourceState.seenTaskFinishes[finishKey] = true;
      return;
    }
    resourceState.seenTaskFinishes[finishKey] = true;
    if (kind === "tts") {
      applyTtsInstallResult(after.result || {});
    } else if (kind === "backchannel") {
      notify("接话模型已就绪。", "success");
    } else if (kind === "memory_model") {
      notify("记忆模型已就绪。", "success");
      if (fields.pages.memory.classList.contains("is-active")) {
        loadMemories();
      }
    }
  });
}

function applyTtsInstallResult(result) {
  if (!result || !result.work_dir) {
    return;
  }
  fields.ttsEnabled.checked = true;
  fields.ttsProvider.value = result.provider || fields.ttsProvider.value;
  fields.ttsWorkDir.value = result.work_dir || "";
  fields.ttsPythonPath.value = result.python_path || "";
  fields.ttsConfigPath.value = result.tts_config_path || "";
  fields.ttsApiUrl.value = result.api_url || fields.ttsApiUrl.value;
  refreshSelect(fields.ttsProvider);
  syncTtsState();
  scheduleDirty();
  notify("TTS 整合包已安装，配置已回填。", "success");
}

async function chooseZipPath(title) {
  const dialogApi = window.__TAURI__?.dialog;
  if (dialogApi?.open) {
    const selected = await dialogApi.open({
      title,
      multiple: false,
      filters: [{ name: "ZIP", extensions: ["zip"] }],
    });
    return Array.isArray(selected) ? selected[0] : selected;
  }
  return window.prompt(`${title}\n请输入 ZIP 文件完整路径：`, "") || "";
}

async function importResourceZip(kind) {
  const title = kind === "backchannel" ? "导入接话模型 ZIP" : "导入记忆模型 ZIP";
  let path = "";
  try {
    path = String(await chooseZipPath(title) || "").trim();
  } catch (error) {
    setError(String(error));
    return;
  }
  if (!path) {
    return;
  }
  const method = kind === "backchannel" ? "resources.backchannel.import" : "resources.memory.import";
  await startResourceAction(method, { path });
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
    { label: "整理频率", value: `${fields.memoryTriggerTurns.value || request.memory.curation.trigger_turns} 轮` },
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
  renderMemoryModelResourceCard();
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
    notify("已保存记忆。", "success");
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
    notify("已删除记忆。", "success");
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

function plainEqual(left, right) {
  return JSON.stringify(left || {}) === JSON.stringify(right || {});
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
      notify(String(result.message), "success");
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
    const enabled = plugin.required ? true : Boolean(pluginState.enabledById[plugin.id]);
    if (enabled !== pluginState.initialEnabledById[plugin.id]) {
      enabledById[plugin.id] = enabled;
    }
    const sections = pluginSettingsSections(plugin);
    if (sections.length) {
      sections.forEach((section) => {
        const values = clonePlain(pluginSectionValues(plugin.id, section.section_id));
        const initial = pluginState.initialSettingsValues[plugin.id]?.[section.section_id] || {};
        if (!plainEqual(values, initial)) {
          settingsById[plugin.id] = settingsById[plugin.id] || {};
          settingsById[plugin.id][section.section_id] = values;
        }
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

function normalizedProviderProfiles() {
  return providerState.profiles.map((profile) => ({
    id: profile.id,
    alias: (profile.alias || "").trim() || profile.id,
    base_url: (profile.base_url || "").trim(),
    api_key: (profile.api_key || "").trim(),
    models: (profile.models || []).map((model) => String(model).trim()).filter(Boolean),
  }));
}

function providerDisplayName(profile) {
  return profile.alias || profile.id || "未命名供应商";
}

function focusProviderValidation(profile, field) {
  providerState.selectedId = profile.id;
  providerState.search = "";
  if (fields.providerSearch) {
    fields.providerSearch.value = "";
  }
  showPage("providers");
  renderProviderPage();
  markInvalid(providerDetailInput(field), true);
}

function validateApiSettingsBeforeSubmit() {
  const profiles = normalizedProviderProfiles();
  if (!profiles.length) {
    showPage("providers");
    setError("请至少添加一个 API 供应商。");
    return false;
  }
  const missingBaseUrl = profiles.find((profile) => !profile.base_url);
  if (missingBaseUrl) {
    focusProviderValidation(missingBaseUrl, "base_url");
    setError(`供应商「${providerDisplayName(missingBaseUrl)}」缺少 Base URL。`);
    return false;
  }
  const missingModels = profiles.find((profile) => !profile.models.length);
  if (missingModels) {
    focusProviderValidation(missingModels, "");
    setError(`供应商「${providerDisplayName(missingModels)}」至少需要一个模型。`);
    return false;
  }
  const selection = collectModelSelection();
  const chat = selection.slots.chat || {};
  const chatProfile = profiles.find((profile) => profile.id === chat.profile_id);
  if (!chatProfile || !chat.model || !chatProfile.models.includes(chat.model)) {
    showPage("model");
    refreshModelSlots();
    setError("请选择可用的聊天模型。");
    return false;
  }
  return true;
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
    profiles: normalizedProviderProfiles(),
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
    theme_changed: themeChanged,
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
  resourceState.snapshot = request.resources || {};
  renderCharacters();
  renderThemeControls();
  initializeProviderState();
  renderProviderPage();
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
  setTtsProviderValue(request.tts.provider);
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
  fields.backchannelMode.value =
    request.system_extra.backchannel.mode === "hybrid" ? "hybrid" : "rules";
  fields.backchannelDelay.value = request.system_extra.backchannel.delay_ms;
  fields.backchannelProbability.value = request.system_extra.backchannel.probability;
  fields.backchannelTtsEnabled.checked = request.system_extra.backchannel.tts_enabled;
  fields.memoryTriggerTurns.value = request.memory.curation.trigger_turns;

  setThemeValues(request.theme);
  themeChanged = false;
  fields.tokenEstimate.textContent =
    `按当前屏幕估算：约 ${request.estimated_tokens_per_image.toLocaleString("zh-CN")} token/张。`;
  syncEnabledState();
  syncRuntimeLoopState();
  syncDebugLogState();
  syncBubbleState();
  syncApiAdvancedState();
  syncTtsState();
  syncCharacterArchiveState();
  refreshSelect(fields.characterSelect);
  refreshSelect(fields.ttsProvider);
  refreshSelect(fields.backchannelMode);
  renderMemoryPage();
  renderPluginPage();
  renderResourceCards();
  if (hasRunningResourceTask()) {
    startResourcePolling();
  }

  // 配置全部填充完毕后拍基线，作为「未保存改动」的比对基准。
  settingsBaseline = settingsSnapshot();
  refreshDirty();
}

fields.navItems.forEach((item) => {
  item.addEventListener("click", () => showPage(item.dataset.page));
});
layoutSliders.forEach((fieldKey) => {
  const preview = () => {
    updateSliderOutput(fieldKey);
    requestLayoutPreview();
  };
  fields[fieldKey].addEventListener("input", preview);
  fields[fieldKey].addEventListener("change", preview);
});
fields.characterSelect.addEventListener("change", syncTtsState);
fields.characterSelect.addEventListener("change", applySelectedCharacterTheme);
fields.characterSelect.addEventListener("change", syncCharacterArchiveState);
fields.characterImportButton.addEventListener("click", importCharacterArchive);
fields.ttsVoiceImportButton.addEventListener("click", importCharacterVoiceArchive);
fields.characterExportButton.addEventListener("click", exportCharacterArchive);
fields.enabled.addEventListener("change", syncEnabledState);
fields.toolCallsPerStep.addEventListener("input", syncRuntimeLoopState);
fields.addProviderButton.addEventListener("click", openAddProviderChooser);
fields.providerSearch.addEventListener("input", () => {
  providerState.search = fields.providerSearch.value;
  renderProviderList();
});
fields.apiTopPEnabled.addEventListener("change", syncApiAdvancedState);
fields.apiMaxTokensEnabled.addEventListener("change", syncApiAdvancedState);
fields.ttsEnabled.addEventListener("change", syncTtsState);
fields.ttsProvider.addEventListener("change", handleTtsProviderChange);
fields.ttsTestButton.addEventListener("click", testTtsSettings);
fields.backchannelEnabled.addEventListener("change", renderBackchannelResourceCard);
fields.backchannelMode.addEventListener("change", renderBackchannelResourceCard);
fields.visualEffectMode.addEventListener("change", markThemeChanged);
fields.themeAiButton.addEventListener("click", generateAiTheme);
fields.resetThemeButton.addEventListener("click", () => {
  setThemeValues(selectedCharacterThemeDefaults(), { updateVisualEffect: false });
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
fields.memoryTriggerTurns.addEventListener("input", renderMemoryStatus);
fields.pluginSearch.addEventListener("input", renderPluginPage);
fields.pluginStatusFilter.addEventListener("change", renderPluginPage);
fields.pluginPermissionFilter.addEventListener("change", renderPluginPage);
fields.saveButton.addEventListener("click", async () => {
  if (!request) {
    return;
  }
  setError("");
  if (!validateApiSettingsBeforeSubmit()) {
    return;
  }
  const original = fields.saveButton.textContent;
  let settings;
  try {
    settings = collectSettings();
  } catch (error) {
    setError(String(error));
    return;
  }
  // 保存成功后 Rust/Python 会关窗，提前放行关窗拦截。
  bypassCloseGuard = true;
  fields.saveButton.disabled = true;
  fields.saveButton.textContent = "保存中…";
  try {
    await invoke("save_settings", { settings });
    settingsBaseline = settingsSnapshot();
    refreshDirty();
    notify("已保存。", "success");
  } catch (error) {
    bypassCloseGuard = false;
    fields.saveButton.disabled = false;
    fields.saveButton.textContent = original;
    setError(String(error));
    return;
  }
  window.setTimeout(() => {
    bypassCloseGuard = false;
    fields.saveButton.disabled = false;
    fields.saveButton.textContent = original;
  }, 800);
});

fields.applyButton.addEventListener("click", async () => {
  if (!request) {
    return;
  }
  setError("");
  if (!validateApiSettingsBeforeSubmit()) {
    return;
  }
  fields.applyButton.disabled = true;
  try {
    await invoke("apply_settings", { settings: collectSettings() });
    // 应用同样会持久化（仅不关窗），故重置基线，清掉「未保存」状态。
    settingsBaseline = settingsSnapshot();
    refreshDirty();
    notify("已应用。", "success");
  } catch (error) {
    setError(String(error));
  } finally {
    fields.applyButton.disabled = false;
  }
});

fields.cancelButton.addEventListener("click", async () => {
  await requestCancelClose();
});

// 任意输入/勾选/点击后重算「未保存」状态（动态重建 DOM 的供应商/插件/模型区也能覆盖）。
["input", "change", "click"].forEach((evt) => {
  document.addEventListener(evt, scheduleDirty, true);
});

// 数字输入失焦时越界标红，改回合法即清除。
const detailCard = document.querySelector(".detail-card");
function numberOutOfBounds(el) {
  if (el.value === "") {
    return false;
  }
  const value = Number.parseFloat(el.value);
  const min = el.min !== "" ? Number.parseFloat(el.min) : -Infinity;
  const max = el.max !== "" ? Number.parseFloat(el.max) : Infinity;
  return Number.isNaN(value) || value < min || value > max;
}
detailCard?.addEventListener("focusout", (event) => {
  const el = event.target;
  if (el instanceof HTMLInputElement && el.type === "number") {
    markInvalid(el, numberOutOfBounds(el));
  }
});
detailCard?.addEventListener("input", (event) => {
  const el = event.target;
  if (el instanceof HTMLInputElement && el.type === "number" && el.classList.contains("is-invalid")) {
    markInvalid(el, numberOutOfBounds(el));
  }
});

// 关窗（X / OS）拦截：统一走「取消」路径；有未保存改动时二次确认。
(function guardWindowClose() {
  try {
    window.__TAURI__?.event?.listen?.("sakura://settings-close-requested", requestCancelClose);
    const current = window.__TAURI__?.window?.getCurrentWindow?.();
    if (!current?.onCloseRequested) {
      return;
    }
    current.onCloseRequested(async (event) => {
      if (bypassCloseGuard) {
        return;
      }
      event.preventDefault();
      await requestCancelClose();
    });
  } catch {
    // 监听不可用时不阻断窗口正常关闭。
  }
})();

load().catch((error) => setError(String(error)));
