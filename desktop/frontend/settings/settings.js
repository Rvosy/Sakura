import {
  createRootSettingsClient,
  formatSettingsError,
  legacyDataImportPlanHasWork,
  normalizeCharacterSettingsSnapshot,
} from "./root-settings-runtime.js";
import { findProviderModelSelectionIssue } from "./provider-model-runtime.js";
import {
  applyCharacterCatalogChange,
  applyCharacterSwitch,
  commitCharacterSelection,
  countCharacterScopedCollectionDrafts,
  hasCharacterScopedDrafts,
  pendingCharacterSelection,
  syncCharacterEditorControl,
  setCharacterSwitchLock,
} from "./character-switch-runtime.js";
import {
  drawHueSurface,
  drawSaturationValueSurface,
} from "./theme-color-picker.js";
import {
  applyThemeTokens,
  isHexColor,
  normalizeColorText,
} from "../core/theme-runtime.js";
import { installDevtoolsShortcutGuard } from "../core/devtools-guard.js";

installDevtoolsShortcutGuard();

const nativeInvoke = window.__TAURI__.core.invoke;
let runtimeDiagnostics = null;
const runtimeDiagnosticsReady = import("../core/runtime-diagnostics.js")
  .then(({ createRuntimeDiagnostics }) => {
    runtimeDiagnostics = createRuntimeDiagnostics({ invoke: nativeInvoke });
    return runtimeDiagnostics;
  })
  .catch(() => null);
function invoke(command, args) {
  if (runtimeDiagnostics) return runtimeDiagnostics.invoke(command, args);
  return runtimeDiagnosticsReady.then((diagnostics) => (
    diagnostics ? diagnostics.invoke(command, args) : nativeInvoke(command, args)
  ));
}
const rootSettingsClient = createRootSettingsClient({ invoke });
const settingsCloseFlowPromise = import("./close-flow.js");
const runtimeFontsReadyPromise = import("../core/font-loader.js")
  .then(({ waitForRuntimeFonts }) => waitForRuntimeFonts({ families: ["sc"] }))
  .catch(() => {
    document.documentElement.dataset.runtimeFonts = "fallback";
    return "fallback";
  });

document.addEventListener("contextmenu", (event) => event.preventDefault());

const fields = {
  characterSelect: document.getElementById("characterSelect"),
  characterImportButton: document.getElementById("characterImportButton"),
  ttsVoiceImportButton: document.getElementById("ttsVoiceImportButton"),
  characterExportButton: document.getElementById("characterExportButton"),
  characterEditorButton: document.getElementById("characterEditorButton"),
  characterArchiveHint: document.getElementById("characterArchiveHint"),
  portraitScale: document.getElementById("portraitScale"),
  controlPanelWidth: document.getElementById("controlPanelWidth"),
  bubbleHeight: document.getElementById("bubbleHeight"),
  bubbleAutoExpand: document.getElementById("bubbleAutoExpand"),
  controlPanelOffset: document.getElementById("controlPanelOffset"),
  inputBarOffset: document.getElementById("inputBarOffset"),
  enabled: document.getElementById("enabled"),
  checkInterval: document.getElementById("checkInterval"),
  cooldown: document.getElementById("cooldown"),
  batchLimit: document.getElementById("batchLimit"),
  screenResolution: document.getElementById("screenResolution"),
  agentSteps: document.getElementById("agentSteps"),
  toolCallsPerStep: document.getElementById("toolCallsPerStep"),
  toolCallsPerTurn: document.getElementById("toolCallsPerTurn"),
  providerStatusStrip: document.getElementById("providerStatusStrip"),
  providerSearch: document.getElementById("providerSearch"),
  addProviderButton: document.getElementById("addProviderButton"),
  providerList: document.getElementById("providerList"),
  providerDetail: document.getElementById("providerDetail"),
  modelSlots: document.getElementById("modelSlots"),
  contextWindowTokens: document.getElementById("contextWindowTokens"),
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
  ttsTestButton: document.getElementById("ttsTestButton"),
  ttsTimeout: document.getElementById("ttsTimeout"),
  themeColors: document.getElementById("themeColors"),
  visualEffectMode: document.getElementById("visualEffectMode"),
  themeAiButton: document.getElementById("themeAiButton"),
  resetThemeButton: document.getElementById("resetThemeButton"),
  subtitleTypingInterval: document.getElementById("subtitleTypingInterval"),
  replySegmentPause: document.getElementById("replySegmentPause"),
  bubbleAutoHide: document.getElementById("bubbleAutoHide"),
  bubbleAutoHideDelay: document.getElementById("bubbleAutoHideDelay"),
  memoryTriggerTurns: document.getElementById("memoryTriggerTurns"),
  speechFontSize: document.getElementById("speechFontSize"),
  nameFontSize: document.getElementById("nameFontSize"),
  inputFontSize: document.getElementById("inputFontSize"),
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
  pluginSearch: document.getElementById("pluginSearch"),
  pluginInstallMenuRoot: document.getElementById("pluginInstallMenuRoot"),
  pluginInstallMenuButton: document.getElementById("pluginInstallMenuButton"),
  pluginInstallMenu: document.getElementById("pluginInstallMenu"),
  pluginInstallZipButton: document.getElementById("pluginInstallZipButton"),
  pluginInstallFolderButton: document.getElementById("pluginInstallFolderButton"),
  pluginList: document.getElementById("pluginList"),
  pluginDetail: document.getElementById("pluginDetail"),
  storageUserRoot: document.getElementById("storageUserRoot"),
  storageTtsRoot: document.getElementById("storageTtsRoot"),
  storageTtsStatus: document.getElementById("storageTtsStatus"),
  storageOpenUserRoot: document.getElementById("storageOpenUserRoot"),
  storageChooseTtsRoot: document.getElementById("storageChooseTtsRoot"),
  storageResetTtsRoot: document.getElementById("storageResetTtsRoot"),
  legacyRoleDataImportButton: document.getElementById("legacyRoleDataImportButton"),
  legacyRoleDataImportStatus: document.getElementById("legacyRoleDataImportStatus"),
  systemFirstRunGuideButton: document.getElementById("systemFirstRunGuideButton"),
  updateStatus: document.getElementById("updateStatus"),
  updateNotes: document.getElementById("updateNotes"),
  updateFeedback: document.getElementById("updateFeedback"),
  updateCheckButton: document.getElementById("updateCheckButton"),
  updateCheckLabel: document.getElementById("updateCheckLabel"),
  updateAutoCheck: document.getElementById("updateAutoCheck"),
  launchAtLogin: document.getElementById("launchAtLogin"),
  updateActionButton: document.getElementById("updateActionButton"),
  updateActionLabel: document.getElementById("updateActionLabel"),
  telemetryEnabled: document.getElementById("telemetryEnabled"),
  telemetryHelpButton: document.getElementById("telemetryHelpButton"),
  telemetryInstallationId: document.getElementById("telemetryInstallationId"),
  telemetryCopyButton: document.getElementById("telemetryCopyButton"),
  telemetryRegenerateButton: document.getElementById("telemetryRegenerateButton"),
  aboutVersion: document.getElementById("aboutVersion"),
  aboutWebsiteButton: document.getElementById("aboutWebsiteButton"),
  aboutRepositoryButton: document.getElementById("aboutRepositoryButton"),
  aboutChangelogButton: document.getElementById("aboutChangelogButton"),
  aboutSponsorButton: document.getElementById("aboutSponsorButton"),
  aboutComponentsSummary: document.getElementById("aboutComponentsSummary"),
  aboutComponentsRefresh: document.getElementById("aboutComponentsRefresh"),
  aboutComponentsState: document.getElementById("aboutComponentsState"),
  aboutComponentsList: document.getElementById("aboutComponentsList"),
  errorText: document.getElementById("errorText"),
  onboardingHead: document.getElementById("onboardingHead"),
  onboardingCharacterStep: document.getElementById("onboardingCharacterStep"),
  onboardingProviderStep: document.getElementById("onboardingProviderStep"),
  onboardingCompleteStep: document.getElementById("onboardingCompleteStep"),
  onboardingBackButton: document.getElementById("onboardingBackButton"),
  saveButton: document.getElementById("saveButton"),
  applyButton: document.getElementById("applyButton"),
  cancelButton: document.getElementById("cancelButton"),
  pageHead: document.querySelector(".page-head"),
  pageTitle: document.getElementById("pageTitle"),
  pageSubtitle: document.getElementById("pageSubtitle"),
  memorySurface: document.getElementById("memorySurface"),
  navItems: Array.from(document.querySelectorAll(".nav-item[data-page]")),
  pages: {
    character: document.getElementById("page-character"),
    appearance: document.getElementById("page-appearance"),
    providers: document.getElementById("page-providers"),
    model: document.getElementById("page-model"),
    voice: document.getElementById("page-voice"),
    interaction: document.getElementById("page-interaction"),
    tools: document.getElementById("page-tools"),
    plugins: document.getElementById("page-plugins"),
    system: document.getElementById("page-system"),
    about: document.getElementById("page-about"),
    memory: document.getElementById("page-memory"),
  },
};

let request = null;
let runtimeSettingsHost = false;
let runtimeAppearanceController = null;
let runtimeProviderModelController = null;
let runtimeChatTimingController = null;
let runtimeBubbleAutoHideController = null;
let runtimeMemoryController = null;
let runtimeToolsController = null;
let runtimePluginController = null;
let latestUpdateSnapshot = null;
let updateActionBusy = false;
let pluginPresentation = null;
let runtimeVoiceController = null;
let runtimeScreenAwarenessController = null;
let runtimeAutostartController = null;
let firstRunGuideController = null;
let runtimeCharacterSnapshot = null;
let runtimeCharacterDraftId = "";
let runtimeCharacterVisualPreviewRevision = 0;
let runtimeCharacterVisualPreviewPromise = Promise.resolve();
let runtimeAppearanceInitialized = false;
let runtimeCapabilityManifest = null;
let runtimeVisualEffectModes = Object.freeze([
  Object.freeze({ id: "solid", label: "纯色块", disabled: false, reason: "" }),
  Object.freeze({ id: "gaussian_blur", label: "高斯模糊", disabled: false, reason: "" }),
  Object.freeze({ id: "liquid_glass", label: "液态玻璃", disabled: false, reason: "" }),
]);
let lastTtsProvider = "";
let themeChanged = false;
// 「未保存改动」基线：load() 末尾拍下 collectSettings() 的 JSON 快照，之后任意输入都与它比对。
let settingsBaseline = null;
// 程序化关窗（保存/取消）前置真，避免关窗拦截器把正常关闭误判成「放弃改动」。
let bypassCloseGuard = false;
let memoryRetryTimer = null;
let memoryRetryStartedAt = 0;
let memoryReadErrorRetryable = () => false;
const MEMORY_LOADING_RETRY_DELAY_MS = 1500;
const MEMORY_LOADING_RETRY_BUDGET_MS = 120_000;
const MEMORY_INITIALIZING_MESSAGE = "记忆系统正在初始化，完成后会自动显示。";
let settingsWindowClosing = false;
let characterArchiveBusy = false;
let characterSwitching = false;
let characterCatalogRefreshRevision = 0;
let onboardingStep = "character";
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
  editorDrafts: new Map(),
  rebinding: false,
  composing: false,
};
let memoryLoadRevision = 0;
const pluginState = {
  selectedId: "",
  enabledById: {},
  initialEnabledById: {},
  settingsValues: {},
  initialSettingsValues: {},
  actionBusyKey: "",
  managementBusy: false,
};
const pluginCollectionState = new Map();
let pluginActivityRefreshTimer = null;
let pluginActivityRefreshInFlight = false;
let aboutComponentsReadError = "";

const runtimeThemeLegacyFields = Object.freeze({
  primary: "primary_color",
  primaryHover: "primary_hover_color",
  accent: "accent_color",
  text: "text_color",
  secondaryText: "secondary_text_color",
  mutedText: "muted_text_color",
  pageBackground: "page_background_color",
  panelBackground: "panel_background_color",
  inputBackground: "input_background_color",
  bubbleBackground: "bubble_background_color",
  border: "border_color",
});

const reduceMotionQuery = window.matchMedia?.("(prefers-reduced-motion: reduce)") || null;

let activeThemeField = "";
let themeEditor = {};
const RUNTIME_UNAVAILABLE_REASON = "该设置能力尚未迁移到 Runtime v2";
const RUNTIME_LAYOUT_DEFAULTS = Object.freeze({
  controlPanelWidth: [[420, 860], 640],
  bubbleHeight: [[96, 400], 128],
  controlPanelOffset: [[-400, 400], 0],
  inputBarOffset: [[0, 400], 0],
});

function disableRuntimeControl(control, { markRow = true } = {}) {
  if (!control) return;
  control.disabled = true;
  control.title = RUNTIME_UNAVAILABLE_REASON;
  control.setAttribute("aria-disabled", "true");
  if (!markRow) return;
  const row = control.closest(".setting-row");
  row?.classList.add("is-disabled");
  if (row) row.title = RUNTIME_UNAVAILABLE_REASON;
}

function prepareRuntimeAppearance(snapshot, themeFields) {
  const theme = Object.fromEntries(
    themeFields.map(([field, legacyField]) => [legacyField, snapshot.appearance.values.themeTokens[field]]),
  );
  const themeDefaults = Object.fromEntries(
    themeFields.map(([field, legacyField]) => [legacyField, snapshot.presentation.themeTokens[field]]),
  );
  const knownCharacters = request?.character?.characters || [];
  const currentCharacter = {
    ...(knownCharacters.find((item) => item.id === snapshot.presentation.characterId) || {}),
    id: snapshot.presentation.characterId,
    display_name: snapshot.presentation.displayName,
    theme,
    default_theme: themeDefaults,
  };
  request = {
    ...(request || {}),
    character: {
      current_character_id: snapshot.presentation.characterId,
      characters: knownCharacters.length
        ? knownCharacters.map((item) => item.id === currentCharacter.id ? currentCharacter : item)
        : [currentCharacter],
    },
    theme: { ...theme, visual_effect_mode: snapshot.appearance.values.visualEffectMode },
    theme_defaults: themeDefaults,
    theme_fields: themeFields.map(([, id, label]) => ({ id, label })),
    visual_effect_modes: runtimeVisualEffectModes.map((mode) => ({ ...mode })),
  };

  renderCharacters();

  renderThemeControls();
  setThemeValues(theme);
  for (const [fieldKey, [bounds, value]] of Object.entries(RUNTIME_LAYOUT_DEFAULTS)) {
    setNumericBounds(fields[fieldKey], bounds);
    fields[fieldKey].value = String(value);
    updateSliderOutput(fieldKey);
  }

  for (const control of [
    fields.ttsVoiceImportButton,
    fields.characterExportButton,
    fields.themeAiButton,
    themeEditor.pick,
  ]) {
    // Each of these shares a row with a migrated control. Disable only the
    // unavailable button so the active character/import/theme controls do not
    // inherit the legacy grey unavailable treatment.
    disableRuntimeControl(control, { markRow: false });
  }
  enhanceSelect(fields.characterSelect);
  enhanceSelect(fields.visualEffectMode);
  refreshSelect(fields.characterSelect);
  refreshSelect(fields.visualEffectMode);
  upgradeSliderControls();
  syncCharacterArchiveState();
}

function setError(message) {
  fields.errorText.textContent = formatSettingsError(message);
}

// 反馈分流：错误常驻 footer 红字（role=alert）走 setError；成功/信息走右上角 toast，自动消失。
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
  if (runtimeSettingsHost) {
    return Boolean(
      runtimeAppearanceController?.isDirty()
      || runtimeProviderModelController?.isDirty()
      || runtimeChatTimingController?.isDirty()
      || runtimeBubbleAutoHideController?.isDirty()
      || runtimeMemoryController?.isDirty()
      || runtimeToolsController?.isDirty()
      || runtimePluginController?.isDirty()
      || runtimeVoiceController?.isDirty()
      || runtimeScreenAwarenessController?.isDirty()
      || runtimeAutostartController?.isDirty()
      || memoryState.editorDrafts.size > 0
      || pendingRuntimeCharacterId()
    );
  }
  return Boolean(request) && settingsBaseline !== null && settingsSnapshot() !== settingsBaseline;
}

function refreshDirty() {
  const dirty = computeDirty();
  document.body.classList.toggle("is-dirty", dirty);
  fields.saveButton.classList.toggle("has-changes", dirty);
  if (runtimeSettingsHost) syncCharacterArchiveState();
}

let dirtyTimer = null;
let submissionBusy = false;
const submissionDisabledStates = new Map();

function setSubmissionBusy(busy) {
  submissionBusy = Boolean(busy);
  document.body.classList.toggle("is-submitting", submissionBusy);
  document.querySelector(".settings-shell")
    ?.setAttribute("aria-busy", String(submissionBusy));
  document.querySelectorAll("[data-submission-lock]").forEach((surface) => {
    surface.inert = submissionBusy;
  });
  [
    fields.onboardingBackButton,
    fields.cancelButton,
    fields.applyButton,
    fields.saveButton,
  ].filter(Boolean).forEach((control) => {
    if (submissionBusy) {
      if (!submissionDisabledStates.has(control)) {
        submissionDisabledStates.set(control, control.disabled);
      }
      control.disabled = true;
      return;
    }
    if (submissionDisabledStates.has(control)) {
      control.disabled = submissionDisabledStates.get(control);
      submissionDisabledStates.delete(control);
    }
  });
}

function scheduleDirty() {
  if (settingsBaseline === null || submissionBusy) {
    return;
  }
  window.clearTimeout(dirtyTimer);
  dirtyTimer = window.setTimeout(refreshDirty, 150);
}

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
  beginSettingsWindowClose();
  if (runtimeSettingsHost) {
    try {
      await runtimeCharacterVisualPreviewPromise;
      await runtimeProviderModelController?.cancelOperations();
      await invoke("resolve_settings_close", { discard: true });
    } catch (error) {
      settingsWindowClosing = false;
      throw error;
    }
    return;
  }
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
    if (runtimeSettingsHost) {
      const { executeSettingsClose } = await settingsCloseFlowPromise;
      setError("");
      await executeSettingsClose({
        dirty: computeDirty(),
        choose: chooseUnsavedClose,
        save: async () => {
          setSubmissionBusy(true);
          await saveRuntimeSettings();
          notify("已保存。", "success");
        },
        discard: async () => {
          setSubmissionBusy(true);
          await runtimeAppearanceController?.cancelPreview();
          await runtimeProviderModelController?.cancelOperations();
          runtimeChatTimingController?.discard();
          runtimeBubbleAutoHideController?.discard();
          runtimeAutostartController?.discard();
          runtimeMemoryController?.discard();
          runtimeToolsController?.discard();
          await discardRuntimeCharacterSelection();
        },
        close: closeSettingsWindow,
        stay: async () => {
          await invoke("resolve_settings_close", { discard: false });
        },
      });
      return;
    }
    if (!(await confirmDiscard())) {
      return;
    }
    await closeSettingsWindow();
  } catch (error) {
    bypassCloseGuard = false;
    setError(String(error));
  } finally {
    setSubmissionBusy(false);
    closeRequestInFlight = false;
  }
}

function beginSettingsWindowClose() {
  settingsWindowClosing = true;
  clearMemoryRetry();
  window.clearTimeout(memorySearchTimer);
  memoryLoadRevision += 1;
}

let exitRequestInFlight = false;
async function requestAppExitClose() {
  if (exitRequestInFlight) {
    return;
  }
  exitRequestInFlight = true;
  try {
    const { executeSettingsClose } = await settingsCloseFlowPromise;
    setError("");
    await executeSettingsClose({
      dirty: computeDirty(),
      choose: chooseUnsavedClose,
      save: async () => {
        setSubmissionBusy(true);
        await saveRuntimeSettings();
        notify("已保存。", "success");
      },
      discard: async () => {
        setSubmissionBusy(true);
        await runtimeAppearanceController?.cancelPreview();
        await runtimeProviderModelController?.cancelOperations();
        runtimeChatTimingController?.discard();
        runtimeBubbleAutoHideController?.discard();
        runtimeAutostartController?.discard();
        runtimeMemoryController?.discard();
        runtimeToolsController?.discard();
        await discardRuntimeCharacterSelection();
      },
      close: async () => {
        beginSettingsWindowClose();
        try {
          await runtimeCharacterVisualPreviewPromise;
          await runtimeProviderModelController?.cancelOperations();
          bypassCloseGuard = true;
          await invoke("resolve_settings_exit", { discard: true });
        } catch (error) {
          settingsWindowClosing = false;
          throw error;
        }
      },
      stay: async () => {
        await invoke("resolve_settings_exit", { discard: false });
      },
    });
  } catch (error) {
    bypassCloseGuard = false;
    setError(String(error));
  } finally {
    setSubmissionBusy(false);
    exitRequestInFlight = false;
  }
}

function markInvalid(input, invalid) {
  if (input) {
    input.classList.toggle("is-invalid", Boolean(invalid));
  }
}

function setControlDisabled(control, disabled, { row = true } = {}) {
  if (!control) {
    return;
  }
  control.disabled = Boolean(disabled);
  if (row) {
    control.closest(".setting-row")?.classList.toggle("is-disabled", Boolean(disabled));
  }
  refreshSelect(control);
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
  memoryRetryTimer = window.setTimeout(
    () => loadMemories({ continueRetry: true }),
    MEMORY_LOADING_RETRY_DELAY_MS,
  );
}

function memoryRetryBudgetAvailable() {
  if (!memoryRetryStartedAt) {
    memoryRetryStartedAt = Date.now();
  }
  return Date.now() - memoryRetryStartedAt < MEMORY_LOADING_RETRY_BUDGET_MS;
}

function removeOverlayAfterExit(overlay) {
  if (!overlay?.isConnected) return Promise.resolve();
  if (reduceMotionQuery?.matches) {
    overlay.remove();
    return Promise.resolve();
  }
  overlay.classList.add("is-closing");
  return new Promise((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      window.clearTimeout(fallbackTimer);
      overlay.removeEventListener("animationend", onAnimationEnd);
      overlay.remove();
      resolve();
    };
    const onAnimationEnd = (event) => {
      if (event.target === overlay) finish();
    };
    const fallbackTimer = window.setTimeout(finish, 260);
    overlay.addEventListener("animationend", onAnimationEnd);
  });
}

function confirmAction(
  message,
  {
    title = "确认操作", confirmText = "确认", cancelText = "取消", danger = false, details = [],
  } = {},
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
    const detailList = document.createElement("ul");
    detailList.className = "confirm-dialog-list";
    details.forEach((detail) => {
      const item = document.createElement("li");
      item.textContent = detail;
      detailList.append(item);
    });
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
    dialog.append(heading, body);
    if (detailList.childElementCount) dialog.append(detailList);
    dialog.append(actions);
    overlay.append(dialog);

    let closing = false;
    function close(value) {
      if (closing) return;
      closing = true;
      document.removeEventListener("keydown", onKey, true);
      cancel.disabled = true;
      confirm.disabled = true;
      void removeOverlayAfterExit(overlay).then(() => resolve(value));
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

async function chooseUnsavedClose() {
  const { CloseDecision } = await settingsCloseFlowPromise;
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "confirm-overlay";
    const dialog = document.createElement("section");
    dialog.className = "confirm-dialog";
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    const heading = document.createElement("h2");
    heading.textContent = "保存改动";
    const body = document.createElement("p");
    body.textContent = "设置有未保存的改动，是否保存后关闭？";
    const actions = document.createElement("div");
    actions.className = "confirm-actions";
    const stay = document.createElement("button");
    stay.type = "button";
    stay.className = "secondary-button";
    stay.textContent = "返回";
    const discard = document.createElement("button");
    discard.type = "button";
    discard.className = "danger-button";
    discard.textContent = "不保存";
    const save = document.createElement("button");
    save.type = "button";
    save.textContent = "保存";
    actions.append(stay, discard, save);
    dialog.append(heading, body, actions);
    overlay.append(dialog);

    function close(decision) {
      document.removeEventListener("keydown", onKey, true);
      overlay.remove();
      resolve(decision);
    }
    function onKey(event) {
      if (event.key === "Escape") {
        close(CloseDecision.STAY);
      }
    }
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) {
        close(CloseDecision.STAY);
      }
    });
    stay.addEventListener("click", () => close(CloseDecision.STAY));
    discard.addEventListener("click", () => close(CloseDecision.DISCARD));
    save.addEventListener("click", () => close(CloseDecision.SAVE));
    document.addEventListener("keydown", onKey, true);
    document.body.append(overlay);
    save.focus();
  });
}

function runThemeTransition(update) {
  if (reduceMotionQuery?.matches || typeof document.startViewTransition !== "function") {
    update();
    return;
  }
  document.documentElement.classList.add("is-theme-view-transition");
  const transition = document.startViewTransition(update);
  transition.finished.finally(() => {
    document.documentElement.classList.remove("is-theme-view-transition");
  });
}

function replayMotion(element, className) {
  if (!element || reduceMotionQuery?.matches) {
    return;
  }
  element.classList.remove(className);
  void element.offsetWidth;
  element.classList.add(className);
}

function markThemeChanged() {
  themeChanged = true;
  applyThemeTokens(collectThemeSettings());
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
  voice: { title: "语音", subtitle: "选择语音引擎和服务来源" },
  interaction: { title: "交互", subtitle: "字幕、气泡与主动屏幕感知" },
  tools: { title: "工具", subtitle: "工具调用与循环上限" },
  plugins: { title: "插件", subtitle: "安装、启用和设置插件" },
  system: { title: "系统", subtitle: "管理启动、更新与本地数据" },
  about: { title: "关于", subtitle: "查看版本、更新与本地组件" },
  memory: { title: "记忆", subtitle: "查看、编辑、删除长期记忆与常驻档案" },
};

function showPage(page) {
  Object.entries(fields.pages).forEach(([key, element]) => {
    element.hidden = key !== page;
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
    memoryRetryStartedAt = 0;
  }
  clearPluginActivityRefresh();
  if (page === "plugins" || page === "about") schedulePluginActivityRefresh();
  const meta = pageMeta[page];
  if (meta) {
    fields.pageTitle.textContent = meta.title;
    fields.pageSubtitle.textContent = meta.subtitle;
    replayMotion(fields.pageHead, "is-switching");
  }
  // 进入「模型」页时按当前供应商重建槽位选项（供应商可能在另一页被改过）。
  if (page === "model" && request) {
    refreshModelSlots();
  }
  if (page === "memory" && runtimeSettingsHost) {
    renderMemorySurface();
    return;
  }
  if (
    page === "memory"
    && request?.memory
    && !memoryState.loading
    && (!memoryState.loaded || memoryState.status === "loading")
  ) {
    loadMemories();
  } else if (page === "memory" && !request?.memory) {
    renderMemoryInitializationState();
  }
}

function isOnboarding() {
  return Boolean(request?.onboarding);
}

function onboardingChatProfile() {
  const profiles = normalizedProviderProfiles();
  const chat = collectModelSelection().slots.chat || {};
  return profiles.find((profile) => profile.id === chat.profile_id) || null;
}

function onboardingApiReady() {
  const profile = onboardingChatProfile();
  const chat = collectModelSelection().slots.chat || {};
  return Boolean(
    profile
    && profile.base_url
    && profile.api_key
    && chat.model
    && profile.models.includes(chat.model)
  );
}

function updateOnboardingUi() {
  if (!isOnboarding()) {
    return;
  }
  const characterReady = Boolean(selectedCharacter());
  const apiReady = onboardingApiReady();
  const providerActive = onboardingStep === "providers";
  fields.onboardingCharacterStep.classList.toggle("is-active", !providerActive);
  fields.onboardingCharacterStep.classList.toggle("is-complete", characterReady);
  fields.onboardingProviderStep.classList.toggle("is-active", providerActive);
  fields.onboardingProviderStep.classList.toggle("is-complete", apiReady);
  fields.onboardingProviderStep.disabled = !characterReady;
  fields.onboardingCompleteStep.classList.toggle("is-complete", characterReady && apiReady);
  fields.onboardingBackButton.hidden = !providerActive;
  fields.saveButton.disabled = characterArchiveBusy || !(characterReady && apiReady);
}

function showOnboardingStep(page) {
  if (!isOnboarding() || (page === "providers" && !selectedCharacter())) {
    return;
  }
  onboardingStep = page;
  showPage(page);
  updateOnboardingUi();
}

function initializeOnboarding() {
  const active = isOnboarding();
  document.body.classList.toggle("is-onboarding", active);
  fields.onboardingHead.hidden = !active;
  if (!active) {
    return;
  }
  fields.saveButton.textContent = "完成并启动 Sakura";
  showOnboardingStep(selectedCharacter() ? "providers" : "character");
}

function syncEnabledState() {
  const enabled = fields.enabled.checked;
  setControlDisabled(fields.checkInterval, !enabled);
  setControlDisabled(fields.cooldown, !enabled);
  setControlDisabled(fields.batchLimit, !enabled);
  setControlDisabled(fields.screenResolution, !enabled);
}

function updateScreenResolutionEstimate() {
  // Runtime v2 第一版不展示依赖屏幕尺寸与模型规则的 token 估算。
}

function syncRuntimeLoopState() {
  if (runtimeToolsController || !request?.limits?.max_tool_calls_per_step) {
    return;
  }
  const perStep = clampInt(fields.toolCallsPerStep.value, request.limits.max_tool_calls_per_step);
  fields.toolCallsPerTurn.min = String(perStep);
}

function syncBubbleState() {
  setControlDisabled(fields.bubbleAutoHideDelay, !fields.bubbleAutoHide.checked);
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
  setThemeValues(selectedCharacterTheme(), { updateVisualEffect: false, animateTheme: true });
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
  if (runtimeSettingsHost) return;
  const character = selectedCharacter();
  const hasVoice = character ? Boolean(character.has_voice) : true;
  if (!hasVoice) {
    fields.ttsEnabled.checked = false;
  }
  setControlDisabled(fields.ttsEnabled, !hasVoice);
  const active = fields.ttsEnabled.checked && fields.ttsProvider.value !== "none";
  const bundledProvider = isBundledTtsProvider(fields.ttsProvider.value);
  setControlDisabled(fields.ttsApiUrl, !active);
  setControlDisabled(fields.ttsTimeout, !active);
  setControlDisabled(fields.ttsWorkDir, !active || bundledProvider);
  setControlDisabled(fields.ttsPythonPath, !active || bundledProvider);
  fields.ttsWorkDir.readOnly = false;
  fields.ttsPythonPath.readOnly = false;
  fields.ttsConfigPath.disabled = true;
  setControlDisabled(fields.ttsTestButton, !active);
  syncTtsBundleNotice();
  if (request) {
    renderTtsResourceCard();
  }
}

async function testTtsSettings() {
  if (runtimeSettingsHost) return;
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
  if (runtimeSettingsHost) return;
  applyTtsProviderDefaults(lastTtsProvider);
  syncTtsState();
}

function syncApiAdvancedState() {
  setControlDisabled(fields.apiTopP, !fields.apiTopPEnabled.checked, { row: false });
  setControlDisabled(fields.apiMaxTokens, !fields.apiMaxTokensEnabled.checked, { row: false });
}

function renderCharacters() {
  fields.characterSelect.textContent = "";
  request.character.characters.forEach((character) => {
    const option = document.createElement("option");
    option.value = character.id;
    option.textContent = character.display_name || character.id;
    fields.characterSelect.append(option);
  });
  const pendingCharacterId = runtimeSettingsHost
    ? pendingCharacterSelection({
      committedCharacterId: request.character.current_character_id,
      selectedCharacterId: runtimeCharacterDraftId,
    })
    : null;
  fields.characterSelect.value = pendingCharacterId
    || request.character.current_character_id;
  syncCharacterArchiveState();
}

function applyRuntimeCharacterSnapshot(snapshot, { preserveSelection = false } = {}) {
  const normalized = snapshot?.snapshot && snapshot?.character
    ? snapshot
    : normalizeCharacterSettingsSnapshot(snapshot);
  const pendingSelection = preserveSelection ? pendingRuntimeCharacterId() : null;
  runtimeCharacterSnapshot = normalized.snapshot;
  runtimeCharacterDraftId = normalized.character.characters.some((item) => item.id === pendingSelection)
    ? pendingSelection : normalized.character.current_character_id;
  request = request || {};
  request.character = normalized.character;
  renderCharacters();
  refreshSelect(fields.characterSelect);
}

function prepareRuntimeCharacterOnly() {
  for (const control of [
    fields.portraitScale,
    fields.controlPanelWidth,
    fields.bubbleHeight,
    fields.bubbleAutoExpand,
    fields.controlPanelOffset,
    fields.inputBarOffset,
    fields.speechFontSize,
    fields.nameFontSize,
    fields.inputFontSize,
    fields.themeAiButton,
    fields.resetThemeButton,
    fields.visualEffectMode,
  ]) disableRuntimeControl(control);
  for (const control of [
    fields.ttsVoiceImportButton,
    fields.characterExportButton,
  ]) disableRuntimeControl(control, { markRow: false });
  enhanceSelect(fields.characterSelect);
  refreshSelect(fields.characterSelect);
  syncCharacterArchiveState();
}

function applyStorageSnapshot(snapshot) {
  const normalized = snapshot;
  fields.storageUserRoot.textContent = snapshot.userRoot;
  fields.storageTtsRoot.textContent = snapshot.ttsRoot;
  fields.storageTtsStatus.textContent = normalized.statusText;
  fields.storageTtsStatus.dataset.state = normalized.statusState;
  fields.storageResetTtsRoot.disabled = !normalized.canReset;
}

async function refreshStorageSettings() {
  applyStorageSnapshot(await rootSettingsClient.storageGet());
}

async function chooseTtsStorageRoot() {
  try {
    const snapshot = await rootSettingsClient.storageChooseTtsRoot();
    if (snapshot) {
      applyStorageSnapshot(snapshot);
      notify("TTS 位置已切换；已有文件不会自动搬运。", "success");
    }
  } catch (error) {
    setError(String(error));
  }
}

async function resetTtsStorageRoot() {
  try {
    applyStorageSnapshot(await rootSettingsClient.storageResetTtsRoot());
    notify("TTS 位置已恢复为默认目录。", "success");
  } catch (error) {
    setError(String(error));
  }
}

async function importLegacyRoleData() {
  fields.legacyRoleDataImportButton.disabled = true;
  fields.legacyRoleDataImportStatus.textContent = "正在检查旧目录，Sakura Core 会短暂重启…";
  try {
    const plan = await rootSettingsClient.legacyRoleDataImportChoose();
    if (!plan) {
      fields.legacyRoleDataImportStatus.textContent = "";
      return;
    }
    if (plan.blocked) {
      throw new Error("检测到跨角色身份冲突；为避免记忆串角色，本次导入已阻止。");
    }
    const totals = plan.totals;
    const additions = totals.historyNew + totals.memoryNew;
    const conflicts = totals.historyConflicts + totals.memoryConflicts;
    if (!legacyDataImportPlanHasWork(plan)) {
      fields.legacyRoleDataImportStatus.textContent = `没有新数据；已跳过 ${totals.historyIdentical + totals.memoryIdentical} 条相同记录。`;
      return;
    }
    let overwriteConflicts = false;
    if (plan.requiresConflictConfirmation) {
      const details = plan.characters
        .filter((character) => character.history.conflicts || character.memory.conflicts)
        .map((character) => (
          `${character.characterId}：历史 ${character.history.conflicts} 条，记忆 ${character.memory.conflicts} 条`
        ));
      overwriteConflicts = await confirmAction(
        `发现 ${conflicts} 条同一身份但内容不同的记录。只会覆盖这些冲突项；其他现有数据保持不变。`,
        {
          title: "确认覆盖冲突记录",
          confirmText: "覆盖并导入",
          cancelText: "取消",
          danger: true,
          details,
        },
      );
      if (!overwriteConflicts) {
        fields.legacyRoleDataImportStatus.textContent = "已取消，当前数据没有改变。";
        return;
      }
    }
    fields.legacyRoleDataImportStatus.textContent = "正在合并聊天历史和长期记忆…";
    await rootSettingsClient.legacyRoleDataImportApply(
      plan.selectionId,
      plan.planToken,
      overwriteConflicts,
    );
    fields.legacyRoleDataImportStatus.textContent = `导入完成：新增 ${additions} 条，跳过 ${totals.historyIdentical + totals.memoryIdentical} 条相同记录，隔离 ${totals.recoverableErrors} 条坏数据。`;
  } catch (error) {
    const code = String(error);
    const message = code.includes("LEGACY_SOURCE_ACTIVE")
      ? "检测到 Sakura 0.9.x 仍在运行，请先完全退出旧版本。"
      : code.includes("LEGACY_IMPORT_CORE_STOP_FAILED")
        ? "无法确认旧版本迁移进程和 Sakura Core 已停止。请立即退出 Sakura，保留迁移记录并重启系统后再试。"
        : code.includes("LEGACY_IMPORT_PROCESS_TERMINATION_FAILED")
          ? "无法确认旧版本迁移进程已停止。Sakura Core 将保持关闭，请保留迁移记录并重启系统后重试。"
          : code.includes("LEGACY_IMPORT_OPERATION_TIMEOUT")
            ? "旧版本数据导入等待超时，已安全停止并恢复现有数据。"
            : code.includes("LEGACY_DATA_SOURCE_UNRECOGNIZED")
              ? "所选目录不是可识别的 Sakura 0.9.x 数据目录。"
              : code.includes("LEGACY_DATA_IMPORT_PLAN_STALE")
                ? "源数据或当前数据已变化，请重新选择目录并检查。"
                : `导入失败：${code}`;
    fields.legacyRoleDataImportStatus.textContent = message;
  } finally {
    fields.legacyRoleDataImportButton.disabled = false;
  }
}

function applyUpdateSnapshot(snapshot) {
  latestUpdateSnapshot = snapshot;
  fields.updateFeedback.hidden = !snapshot.available;
  fields.updateFeedback.dataset.state = snapshot.available ? "available" : "current";
  fields.updateStatus.textContent = snapshot.available
    ? `检测到新版本：v${snapshot.version}`
    : `当前已是最新版本 v${snapshot.currentVersion}`;
  fields.updateNotes.textContent = snapshot.notes?.trim() || "";
  fields.updateNotes.hidden = !fields.updateNotes.textContent;
  fields.updateActionButton.hidden = !snapshot.available;
  fields.updateActionButton.disabled = updateActionBusy;
  fields.updateActionLabel.textContent = snapshot.mode === "portable"
    ? `下载 v${snapshot.version} ZIP`
    : `更新到 v${snapshot.version}`;
  fields.updateCheckButton.classList.toggle("primary-button", !snapshot.available);
  fields.updateCheckButton.classList.toggle("secondary-button", snapshot.available);
  fields.updateCheckLabel.textContent = snapshot.available ? "重新检查" : "检查更新";
}

function applyAboutSnapshot(snapshot) {
  fields.aboutVersion.textContent = `版本 v${snapshot.version}`;
}

async function refreshAboutSettings() {
  const [about, preferences, cachedUpdate] = await Promise.all([
    rootSettingsClient.aboutGet(),
    rootSettingsClient.updatePreferencesGet(),
    rootSettingsClient.updateCachedGet(),
  ]);
  applyAboutSnapshot(about);
  fields.updateAutoCheck.checked = preferences.autoCheckEnabled;
  if (cachedUpdate) applyUpdateSnapshot(cachedUpdate);
}

function applyTelemetrySnapshot(snapshot) {
  fields.telemetryEnabled.checked = snapshot.enabled;
  fields.telemetryInstallationId.textContent = snapshot.installationId || "开启后生成";
  fields.telemetryCopyButton.disabled = snapshot.installationId === null;
  fields.telemetryRegenerateButton.disabled = snapshot.installationId === null;
}

async function refreshTelemetrySettings() {
  applyTelemetrySnapshot(await rootSettingsClient.telemetryGet());
}

async function setTelemetryEnabled() {
  const requested = fields.telemetryEnabled.checked;
  fields.telemetryEnabled.disabled = true;
  try {
    applyTelemetrySnapshot(await rootSettingsClient.telemetrySetEnabled(requested));
    notify(requested ? "已开启匿名统计。" : "已关闭匿名统计。", "success");
  } catch (error) {
    try {
      applyTelemetrySnapshot(await rootSettingsClient.telemetryGet());
    } catch {
      fields.telemetryEnabled.checked = false;
    }
    setError(String(error));
  } finally {
    fields.telemetryEnabled.disabled = false;
  }
}

async function regenerateTelemetryInstallationId() {
  fields.telemetryRegenerateButton.disabled = true;
  try {
    const snapshot = await rootSettingsClient.telemetryRegenerateInstallationId();
    applyTelemetrySnapshot(snapshot);
    notify("诊断 ID 已重新生成。", "success");
  } catch (error) {
    setError(String(error));
  } finally {
    fields.telemetryRegenerateButton.disabled = false;
  }
}

async function checkForUpdates() {
  if (updateActionBusy) return;
  fields.updateCheckButton.disabled = true;
  fields.updateActionButton.disabled = true;
  fields.updateFeedback.hidden = false;
  fields.updateFeedback.dataset.state = "checking";
  fields.updateStatus.textContent = "正在检查更新…";
  fields.updateCheckLabel.textContent = "正在检查…";
  fields.updateNotes.hidden = true;
  try {
    applyUpdateSnapshot(await rootSettingsClient.updateGet());
  } catch (error) {
    latestUpdateSnapshot = null;
    fields.updateActionButton.hidden = true;
    fields.updateFeedback.dataset.state = "failed";
    fields.updateStatus.textContent = "检查更新失败。";
    fields.updateCheckButton.classList.add("primary-button");
    fields.updateCheckButton.classList.remove("secondary-button");
    fields.updateCheckLabel.textContent = "重新检查";
    setError(String(error));
  } finally {
    fields.updateCheckButton.disabled = updateActionBusy;
    fields.updateActionButton.disabled = updateActionBusy;
  }
}

async function saveUpdatePreferences() {
  fields.updateAutoCheck.disabled = true;
  try {
    const snapshot = await rootSettingsClient.updatePreferencesSet(fields.updateAutoCheck.checked);
    fields.updateAutoCheck.checked = snapshot.autoCheckEnabled;
    notify(snapshot.autoCheckEnabled ? "已开启自动检测更新。" : "已关闭自动检测更新。", "success");
  } catch (error) {
    fields.updateAutoCheck.checked = !fields.updateAutoCheck.checked;
    setError(String(error));
  } finally {
    fields.updateAutoCheck.disabled = false;
  }
}

async function runUpdateAction() {
  const snapshot = latestUpdateSnapshot;
  if (!snapshot?.available || updateActionBusy) return;
  updateActionBusy = true;
  fields.updateActionButton.disabled = true;
  fields.updateCheckButton.disabled = true;
  try {
    if (snapshot.mode === "portable") {
      await rootSettingsClient.updateOpenPortableDownload(snapshot.downloadUrl);
      fields.updateStatus.textContent = "已打开新版 Portable ZIP 下载地址。";
      updateActionBusy = false;
      fields.updateActionButton.disabled = false;
      fields.updateCheckButton.disabled = false;
      return;
    }
    fields.updateActionLabel.textContent = "正在下载并安装…";
    await rootSettingsClient.updateInstall();
    fields.updateStatus.textContent = "更新已安装，请重启 Sakura 后使用新版本。";
    fields.updateActionLabel.textContent = "安装完成";
  } catch (error) {
    updateActionBusy = false;
    fields.updateFeedback.dataset.state = "failed";
    fields.updateStatus.textContent = "更新操作失败。";
    fields.updateActionLabel.textContent = snapshot.mode === "portable"
      ? `下载 v${snapshot.version} ZIP`
      : "重新尝试安装";
    fields.updateActionButton.disabled = false;
    fields.updateCheckButton.disabled = false;
    setError(String(error));
  }
}

function syncCharacterArchiveState() {
  if (!request) {
    return;
  }
  const pendingCharacterId = pendingRuntimeCharacterId();
  setCharacterSwitchLock({
    pages: [fields.pages.character],
    // Global drafts remain editable on their own pages, but the aggregate
    // submit actions must not cross the generation hand-off.
    submitControls: runtimeSettingsHost ? [fields.saveButton, fields.applyButton] : [],
  }, characterSwitching);
  for (const page of [fields.pages.appearance, fields.pages.voice, fields.pages.memory]) {
    if (!page) continue;
    page.inert = characterSwitching || Boolean(pendingCharacterId);
    page.setAttribute("aria-busy", String(characterSwitching));
    page.setAttribute("aria-disabled", String(Boolean(pendingCharacterId)));
  }
  if (runtimeSettingsHost && submissionBusy) {
    fields.saveButton.disabled = true;
    fields.applyButton.disabled = true;
  }
  const character = selectedCharacter();
  const hasCharacter = Boolean(character);
  fields.characterSelect.disabled = characterArchiveBusy || characterSwitching
    || !request.character.characters.length;
  fields.characterImportButton.disabled = characterArchiveBusy || characterSwitching
    || Boolean(pendingCharacterId);
  if (runtimeSettingsHost) {
    fields.ttsVoiceImportButton.disabled = characterArchiveBusy || characterSwitching
      || !hasCharacter || Boolean(pendingCharacterId) || currentCharacterHasDrafts();
    fields.characterExportButton.disabled = characterArchiveBusy || characterSwitching
      || !hasCharacter || Boolean(pendingCharacterId);
    syncCharacterEditorControl(
      fields.characterEditorButton,
      characterArchiveBusy || characterSwitching || !hasCharacter,
    );
    fields.characterArchiveHint.textContent = pendingCharacterId
      ? `已选择 ${character?.display_name || pendingCharacterId}；角色级设置已锁定，点击“应用”或“保存并关闭”后正式切换。`
      : currentCharacterHasDrafts()
        ? "当前角色有未保存的改动。保存或放弃后可以导入语音；导出仍使用已保存的角色包。"
        : hasCharacter
        ? "可以导入或导出角色包，也可以在角色工坊中编辑当前角色。"
      : "当前没有角色。请导入一个 Sakura .char 角色包。";
    refreshSelect(fields.characterSelect);
    return;
  }
  fields.ttsVoiceImportButton.disabled = characterArchiveBusy || !hasCharacter;
  fields.characterExportButton.disabled = characterArchiveBusy || !hasCharacter;
  fields.characterEditorButton.disabled = characterArchiveBusy;
  fields.saveButton.disabled = characterArchiveBusy;
  fields.applyButton.disabled = characterArchiveBusy;
  fields.cancelButton.disabled = characterArchiveBusy;
  fields.characterArchiveHint.textContent = characterArchiveBusy
    ? "角色包处理中..."
    : (hasCharacter ? "管理 Sakura .char 与 .voice 文件。" : "先导入一个 Sakura .char 角色包。");
  refreshSelect(fields.characterSelect);
  updateOnboardingUi();
}

function setCharacterArchiveBusy(busy) {
  characterArchiveBusy = Boolean(busy);
  syncCharacterArchiveState();
}

function currentCharacterHasDrafts() {
  return hasCharacterScopedDrafts({
    appearanceDirty: runtimeAppearanceController?.isDirty(),
    voiceDirty: runtimeVoiceController?.isDirty(),
    memorySettingsDirty: runtimeMemoryController?.isDirty(),
    memoryDraft: memoryState.draft,
    memoryEditorDraftCount: memoryState.editorDrafts.size
      + countCharacterScopedCollectionDrafts(pluginCollectionState.values()),
  });
}

function pendingRuntimeCharacterId() {
  return pendingCharacterSelection({
    committedCharacterId: runtimeCharacterSnapshot?.currentCharacterId,
    selectedCharacterId: runtimeCharacterDraftId,
  });
}

function runtimeVisualPreviewTheme(publication) {
  const presentation = publication?.presentation;
  const appearance = publication?.appearance;
  if (
    publication?.schemaVersion !== 1
    || !Number.isSafeInteger(publication.windowGeneration)
    || !Number.isSafeInteger(publication.revision)
    || appearance?.coreGenerationId !== presentation?.generationId
    || appearance?.characterId !== presentation?.characterId
  ) throw new Error("CHARACTER_VISUAL_PREVIEW_INVALID");
  return Object.fromEntries(Object.entries(runtimeThemeLegacyFields).map(([source, target]) => {
    const value = appearance.values?.themeTokens?.[source];
    if (!isHexColor(value)) throw new Error("CHARACTER_VISUAL_PREVIEW_INVALID");
    return [target, value];
  }));
}

function previewRuntimeCharacterVisual(characterId) {
  if (!runtimeSettingsHost || !characterId) return;
  const pending = (async () => {
    const revision = ++runtimeCharacterVisualPreviewRevision;
    const publication = await invoke("settings_character_visual_preview", {
      characterId,
      revision,
    });
    if (
      revision !== runtimeCharacterVisualPreviewRevision
      || characterId !== runtimeCharacterDraftId
      || publication?.revision !== revision
      || publication?.presentation?.characterId !== characterId
    ) return;
    runThemeTransition(() => applyThemeTokens(runtimeVisualPreviewTheme(publication)));
  })();
  runtimeCharacterVisualPreviewPromise = pending;
  return pending;
}

async function discardRuntimeCharacterSelection() {
  runtimeCharacterDraftId = runtimeCharacterSnapshot?.currentCharacterId || "";
  fields.characterSelect.value = runtimeCharacterDraftId;
  refreshSelect(fields.characterSelect);
  syncCharacterArchiveState();
  refreshDirty();
  if (runtimeCharacterDraftId) await previewRuntimeCharacterVisual(runtimeCharacterDraftId);
}

function clearCharacterScopedRuntimeState() {
  clearMemoryRetry();
  memoryLoadRevision += 1;
  memoryState.entries = [];
  memoryState.selectedId = "";
  memoryState.loading = false;
  memoryState.loaded = false;
  memoryState.status = "loading";
  memoryState.message = "正在切换角色，记忆将在新角色就绪后重新加载。";
  memoryState.draft = null;
  memoryState.editorDrafts.clear();
  pluginCollectionState.forEach((state) => {
    window.clearTimeout(state.searchTimer);
    state.queryRevision += 1;
    state.queryPending = false;
    state.queryPendingRender = false;
    state.editor = null;
    state.editorError = "";
  });
  clearMemoryEditorPortal();
  pluginCollectionState.clear();
  renderMemoryPage();
  renderMemorySurface();
}

async function rebindSettingsAfterCharacterSwitch(lifecycle) {
  const generationId = lifecycle?.supervisor?.generationId;
  if (typeof generationId !== "string" || !generationId) {
    throw new Error("CHARACTER_SWITCH_IDENTITY_INVALID");
  }
  runtimeProviderModelController?.rebindIdentity(generationId);
  runtimeScreenAwarenessController?.rebindIdentity(generationId);
  await runtimeAppearanceController?.rebindGeneration(generationId);
  await runtimeToolsController?.refreshCurrent();
  await runtimePluginController?.refreshCurrent();
  await runtimeVoiceController?.refreshCurrent({ preserveDraft: true });
  applyRuntimeCharacterSnapshot(await rootSettingsClient.charactersGet(), { preserveSelection: true });
  memoryState.rebinding = false;
  if (fields.pages.memory.classList.contains("is-active")) {
    await loadMemories();
  } else {
    renderMemoryPage();
  }
  refreshDirty();
}

async function refreshRuntimeCharacterCatalog(payload) {
  const revision = ++characterCatalogRefreshRevision;
  const generationId = typeof payload?.generationId === "string"
    ? payload.generationId
    : "";
  const rebinding = Boolean(generationId);
  if (rebinding) {
    characterSwitching = true;
    memoryState.rebinding = true;
    syncCharacterArchiveState();
  }
  try {
    const applied = await applyCharacterCatalogChange({
      generationId,
      readLifecycle: () => invoke("runtime_lifecycle_snapshot"),
      readCatalog: () => rootSettingsClient.charactersGet(),
      applyCatalog: (snapshot) => applyRuntimeCharacterSnapshot(snapshot, { preserveSelection: true }),
      rebindSettings: rebindSettingsAfterCharacterSwitch,
    });
    if (applied && revision === characterCatalogRefreshRevision) setError("");
  } catch (error) {
    if (revision === characterCatalogRefreshRevision) {
      setError(`角色列表刷新失败：${String(error)}`);
    }
  } finally {
    if (rebinding && revision === characterCatalogRefreshRevision) {
      characterSwitching = false;
      memoryState.rebinding = false;
      renderMemorySurface();
      syncCharacterArchiveState();
    }
  }
}

async function applyRuntimeCharacterChange(receipt, previousLifecycle) {
  await applyCharacterSwitch({
    receipt,
    previousLifecycle,
    applyCommittedSnapshot: applyRuntimeCharacterSnapshot,
    clearCharacterState() {
      memoryState.rebinding = true;
      clearCharacterScopedRuntimeState();
    },
    rebindSettings: rebindSettingsAfterCharacterSwitch,
    setSwitching(value) {
      characterSwitching = value;
      if (!value) memoryState.rebinding = false;
      syncCharacterArchiveState();
    },
    readLifecycle: () => invoke("runtime_lifecycle_snapshot"),
    delay: (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds)),
  });
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
    option.disabled = Boolean(mode.disabled);
    option.textContent = mode.disabled && mode.reason
      ? `${mode.label}（${mode.reason}）`
      : mode.label;
    if (mode.reason) option.title = mode.reason;
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
  const svCanvas = document.createElement("canvas");
  svCanvas.className = "theme-picker-canvas";
  svCanvas.setAttribute("aria-hidden", "true");
  const svPointer = document.createElement("span");
  svPointer.className = "theme-picker-pointer";
  svPad.append(svCanvas, svPointer);
  svPad.addEventListener("pointerdown", updateThemeFromSvPointer);
  svPad.addEventListener("pointermove", (event) => {
    if (event.buttons & 1) {
      updateThemeFromSvPointer(event);
    }
  });

  const hue = document.createElement("div");
  hue.className = "theme-hue-strip";
  const hueCanvas = document.createElement("canvas");
  hueCanvas.className = "theme-picker-canvas";
  hueCanvas.setAttribute("aria-hidden", "true");
  const huePointer = document.createElement("span");
  huePointer.className = "theme-hue-pointer";
  hue.append(hueCanvas, huePointer);
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
  pick.className = "secondary-button theme-editor-pick";
  pick.textContent = "取色";
  pick.addEventListener("click", pickActiveThemeColor);
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "secondary-button";
  cancel.textContent = "取消";
  cancel.addEventListener("click", cancelThemeColorPopover);
  const done = document.createElement("button");
  done.type = "button";
  done.className = "primary-button";
  done.textContent = "完成";
  done.addEventListener("click", completeThemeColorPopover);
  actions.append(pick, cancel, done);

  editor.addEventListener("cancel", (event) => {
    event.preventDefault();
    cancelThemeColorPopover();
  });

  editor.append(head, svPad, hue, hexRow, rgb, actions);
  themeEditor = {
    root: editor,
    swatch,
    label,
    key,
    hex,
    rgbInputs,
    svPad,
    svCanvas,
    svPointer,
    hue,
    hueCanvas,
    huePointer,
    pick,
    initialValue: "",
    initialThemeChanged: false,
    editing: false,
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
  drawThemeColorSurfaces(hsv.h);
}

function openThemeColorPopover(id) {
  selectThemeField(id, { open: false });
  const popover = themeEditor.root;
  if (!popover) {
    return;
  }
  themeEditor.initialValue = themeFieldInput(activeThemeField)?.value || "";
  themeEditor.initialThemeChanged = themeChanged;
  themeEditor.editing = true;
  popover.hidden = false;
  if (!popover.open) {
    popover.showModal();
  }
  drawThemeColorSurfaces(rgbToHsv(hexToRgb(themeFieldValue(activeThemeField))).h);
  themeEditor.hex.focus();
}

function hideThemeColorPopover() {
  if (themeEditor.root) {
    if (themeEditor.root.open) {
      themeEditor.root.close();
    }
    themeEditor.root.hidden = true;
  }
}

function completeThemeColorPopover() {
  hideThemeColorPopover();
  themeEditor.initialValue = "";
  themeEditor.editing = false;
}

function cancelThemeColorPopover() {
  const originalValue = themeEditor.initialValue;
  const originalThemeChanged = themeEditor.initialThemeChanged;
  const input = themeFieldInput(activeThemeField);
  hideThemeColorPopover();
  if (themeEditor.editing && input) {
    input.value = originalValue;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    themeChanged = originalThemeChanged;
    refreshDirty();
  }
  themeEditor.initialValue = "";
  themeEditor.editing = false;
}

function drawThemeColorSurfaces(hue) {
  if (!themeEditor.svCanvas || !themeEditor.hueCanvas) return;
  drawSaturationValueSurface(themeEditor.svCanvas, hue);
  drawHueSurface(themeEditor.hueCanvas);
}

function updateActiveThemeColor(color) {
  const normalized = normalizeColorText(color, "");
  const input = themeFieldInput(activeThemeField);
  if (!normalized || !input) {
    return;
  }
  input.value = normalized;
  input.dispatchEvent(new Event("input", { bubbles: true }));
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
    hideThemeColorPopover();
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
    if (themeEditor.editing) {
      themeEditor.root.hidden = false;
      if (!themeEditor.root.open) themeEditor.root.showModal();
      syncThemeEditor();
      themeEditor.hex.focus();
    }
  }
}

function setThemeValues(theme, options = {}) {
  const updateVisualEffect = options.updateVisualEffect !== false;
  const animateTheme = options.animateTheme === true;
  const update = () => {
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
    applyThemeTokens({
      ...theme,
      visual_effect_mode: fields.visualEffectMode.value || request.theme.visual_effect_mode,
    });
    syncThemeEditor();
  };
  if (animateTheme) {
    runThemeTransition(update);
    return;
  }
  update();
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
    setThemeValues(result.theme, { animateTheme: true });
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
const PROVIDER_FIELD_PLACEHOLDERS = {
  base_url: "通常以 /v1 结尾",
  api_key: "通常以 sk- 开头",
};

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
    configured: Boolean(profile.configured),
    credential_action: profile.credential_action || (profile.configured ? "keep" : "keep"),
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
    (profile) => (profile.base_url || "").trim()
      && ((profile.api_key || "").trim() || (profile.configured && profile.credential_action !== "clear")),
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
  if (runtimeSettingsHost && profile.configured) {
    const clearButton = document.createElement("button");
    clearButton.type = "button";
    clearButton.className = "secondary-button";
    clearButton.textContent = profile.credential_action === "clear" ? "已标记清除" : "清除凭据";
    clearButton.addEventListener("click", () => {
      profile.api_key = "";
      profile.credential_action = "clear";
      profile.configured = false;
      renderProviderPage();
      refreshDirty();
    });
    actions.append(testButton, clearButton, removeButton);
  } else {
    actions.append(testButton, removeButton);
  }
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
  input.placeholder = PROVIDER_FIELD_PLACEHOLDERS[key] || "";
  if (key === "api_key" && runtimeSettingsHost && profile.configured) {
    input.placeholder = "已保存；留空保持原值";
  }
  input.addEventListener("input", () => {
    profile[key] = input.value;
    if (key === "api_key" && runtimeSettingsHost) {
      profile.credential_action = input.value.trim() ? "replace" : (profile.configured ? "keep" : "clear");
    }
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
    updateOnboardingUi();
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
        updateOnboardingUi();
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
    updateOnboardingUi();
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
  if (!apiKey && !(runtimeSettingsHost && profile.configured && profile.credential_action === "keep")) {
    markInvalid(providerDetailInput("api_key"), true);
    setError("请先填写 API Key。");
    return;
  }
  setError("");
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "检测中…";
  try {
    const result = runtimeProviderModelController
      ? await runtimeProviderModelController.listModels(runtimeProbeProfile(profile, ""))
      : await hostCall("api.list_models", {
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
  if (!baseUrl || (!apiKey && !(runtimeSettingsHost && profile.configured && profile.credential_action === "keep"))) {
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
    const result = runtimeProviderModelController
      ? await runtimeProviderModelController.testConnection(runtimeProbeProfile(profile, model))
      : await hostCall("api.test_connection", {
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
  updateOnboardingUi();
}

function addProvider(preset) {
  const profile = {
    id: makeProfileId(),
    alias: preset?.label || "新供应商",
    base_url: preset?.base_url || "",
    api_key: "",
    configured: false,
    credential_action: "keep",
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
  updateOnboardingUi();
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
    contextWindowInput: slot === "core:chat" ? fields.contextWindowTokens : null,
  };
}

function readSlotSelection(slot) {
  const { profileSelect, modelSelect, contextWindowInput } = modelSlotElements(slot);
  const selection = {
    profile_id: profileSelect?.value || "",
    model: modelSelect?.value || "",
  };
  if (contextWindowInput) {
    const value = contextWindowInput.value.trim();
    selection.context_window_tokens = value ? Number.parseInt(value, 10) : null;
  }
  return selection;
}

function setSlotSelection(slot, selection, { preserveMissing = true } = {}) {
  const { profileSelect, modelSelect, contextWindowInput } = modelSlotElements(slot);
  if (!profileSelect || !modelSelect) {
    return;
  }
  const profileId = selection?.profile_id || "";
  if (profileId && Array.from(profileSelect.options).some((option) => option.value === profileId)) {
    profileSelect.value = profileId;
    refreshSelect(profileSelect);
  }
  syncModelOptions(slot, selection?.model || "", { preserveMissing });
  if (contextWindowInput) {
    contextWindowInput.value = selection?.context_window_tokens ?? "";
  }
}

function inheritedSlotSourceSelection(slot) {
  if (slot === "core:chat") {
    return null;
  }
  const chat = readSlotSelection("core:chat");
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

function renderModelSlots(selection, { preserveMissing = true } = {}) {
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
    const contextWindowInput = slot.id === "core:chat" ? fields.contextWindowTokens : null;
    if (slot.allow_inherit) {
      row.classList.add("has-inherit");
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
    const text = document.createElement("span");
    text.className = "setting-row-text";
    const title = document.createElement("span");
    title.className = "setting-title";
    title.textContent = slot.label;
    const description = document.createElement("span");
    description.className = "setting-desc";
    description.textContent = slot.description || "";
    text.append(title, description);
    row.append(text, controls);
    fields.modelSlots.append(row);
    enhanceSelect(profileSelect);
    enhanceSelect(modelSelect);
    profileSelect.addEventListener("change", () => {
      syncModelOptions(slot.id, "", { preserveMissing: false });
      if (slot.id === "core:chat") {
        syncInheritedSlotDisplays();
      }
    });
    modelSelect.addEventListener("change", () => {
      if (slot.id === "core:chat") {
        syncInheritedSlotDisplays();
      }
    });
    const selected = selection?.slots?.[slot.id] || { profile_id: "", model: "" };
    const inheritInput = fields.modelSlots.querySelector(`[data-slot-inherit="${slot.id}"]`);
    if (inheritInput) {
      inheritInput.checked = !selected.profile_id || !selected.model;
    }
    fillProfileOptions(profileSelect, selected.profile_id, slot.required);
    syncModelOptions(slot.id, selected.model, { preserveMissing });
    if (contextWindowInput) {
      contextWindowInput.value = selected.context_window_tokens ?? "";
    }
    syncSlotInheritState(slot.id);
  });
}

function fillProfileOptions(select, selectedId, required) {
  const profiles = providerState.profiles;
  select.textContent = "";
  if (!required) {
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = "不启用";
    select.append(empty);
  }
  profiles.forEach((profile) => {
    const option = document.createElement("option");
    option.value = profile.id;
    option.textContent = profile.alias || profile.id;
    select.append(option);
  });
  const ids = profiles.map((profile) => profile.id);
  if (selectedId && !ids.includes(selectedId)) {
    const missing = document.createElement("option");
    missing.value = selectedId;
    missing.textContent = `${selectedId}（原选择不可用）`;
    select.append(missing);
  }
  let value = ids.includes(selectedId) ? selectedId : "";
  if (selectedId && !ids.includes(selectedId)) value = selectedId;
  if (!value && required && profiles[0]) {
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
  const resolved = resolveModelOptions(models, current, preserveMissing);
  resolved.options.forEach((model) => {
    const option = document.createElement("option");
    option.value = model;
    option.textContent = models.includes(model) ? model : `${model}（原选择不可用）`;
    modelSelect.append(option);
  });
  modelSelect.value = resolved.value;
  refreshSelect(modelSelect);
}

function resolveModelOptions(models, selectedModel, preserveMissing) {
  const options = [...models];
  const current = String(selectedModel || "");
  if (preserveMissing && current && !options.includes(current)) {
    options.push(current);
  }
  const value = options.includes(current) ? current : options[0] || "";
  return { options, value };
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
    setControlDisabled(profileSelect, inherited, { row: false });
  }
  if (modelSelect) {
    setControlDisabled(modelSelect, inherited, { row: false });
  }
  fields.modelSlots
    .querySelector(`[data-slot="${slot}"]`)
    ?.classList.toggle("is-inherited", inherited);
}

function refreshModelSlots() {
  renderModelSlots(collectModelSelection(), { preserveMissing: false });
}

function collectModelSelection() {
  const slots = {};
  request.api.slot_fields.forEach((slot) => {
    const inherited = fields.modelSlots.querySelector(`[data-slot-inherit="${slot.id}"]`)?.checked;
    const selection = readSlotSelection(slot.id);
    slots[slot.id] = inherited
      ? { profile_id: "", model: "" }
      : selection;
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

async function chooseArchivePath(kind) {
  return invoke("settings_character_choose_import", { kind });
}

async function chooseExportPath(kind) {
  return invoke("settings_character_choose_export", {
    kind,
    defaultName: characterExportDefaultName(kind),
  });
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
  const hasCurrentCharacterId = typeof result?.current_character_id === "string";
  if (hasCurrentCharacterId) {
    request.character.current_character_id = result.current_character_id;
  }
  renderCharacters();
  refreshSelect(fields.characterSelect);
  if (hasCurrentCharacterId) {
    fields.characterSelect.value = result.current_character_id;
    refreshSelect(fields.characterSelect);
  }
  if (result?.disable_tts) {
    fields.ttsEnabled.checked = false;
  }
  if (applyTheme && selectedCharacter()) {
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
  if (isOnboarding() && selectedCharacter()) {
    showOnboardingStep("providers");
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
    if (runtimeSettingsHost) {
      const previousLifecycle = await invoke("runtime_lifecycle_snapshot");
      const result = await rootSettingsClient.characterImport(path);
      await applyRuntimeCharacterChange(result, previousLifecycle);
      notify("角色包已导入。", "success");
      return;
    }
    const result = await hostCall("character.import_archive", { path });
    applyCharacterRpcResult(result, { dirty: true, applyTheme: true });
  });
}

async function stageRuntimeCharacterSelection() {
  if (!runtimeSettingsHost || characterArchiveBusy) return;
  const characterId = fields.characterSelect.value;
  if (!characterId || characterId === runtimeCharacterDraftId) return;
  const previousCharacterId = runtimeCharacterDraftId
    || runtimeCharacterSnapshot?.currentCharacterId
    || "";
  const committedCharacterId = runtimeCharacterSnapshot?.currentCharacterId || "";
  if (characterId !== committedCharacterId && currentCharacterHasDrafts()) {
    fields.characterSelect.value = previousCharacterId;
    refreshSelect(fields.characterSelect);
    setError("当前角色还有未保存的外观、语音或记忆改动，请先保存或放弃后再切换。");
    return;
  }
  runtimeCharacterDraftId = characterId;
  setError("");
  refreshSelect(fields.characterSelect);
  syncCharacterArchiveState();
  refreshDirty();
  if (pendingRuntimeCharacterId()) {
    notify("角色选择已暂存，点击“应用”或“保存并关闭”后生效。", "info");
  }
  try {
    await previewRuntimeCharacterVisual(characterId);
  } catch (error) {
    if (characterId === runtimeCharacterDraftId) {
      setError(`角色视觉预览失败：${String(error)}`);
    }
  }
}

async function importCharacterVoiceArchive() {
  await runCharacterArchiveAction(async () => {
    const character = selectedCharacter();
    if (!character) {
      setError("请先选择一个角色。");
      return;
    }
    if (runtimeSettingsHost && (pendingRuntimeCharacterId() || currentCharacterHasDrafts())) {
      setError("请先保存或放弃角色相关改动，再导入语音包。");
      return;
    }
    const path = String(await chooseArchivePath("voice") || "").trim();
    if (!path) {
      return;
    }
    if (runtimeSettingsHost) {
      const previousLifecycle = await invoke("runtime_lifecycle_snapshot");
      const result = await rootSettingsClient.characterVoiceImport(path, character.id);
      await applyRuntimeCharacterChange(result, previousLifecycle);
      notify(`已为角色「${character.display_name}」导入 TTS 模型包。`, "success");
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
    if (runtimeSettingsHost && pendingRuntimeCharacterId()) {
      setError("请先应用或放弃待切换的角色，再导出角色包。");
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
    if (runtimeSettingsHost) {
      const result = await rootSettingsClient.characterExport(path, character.id, kind);
      notify(result.message, "success");
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

async function launchCharacterStudio() {
  await runCharacterArchiveAction(async () => {
    const character = selectedCharacter();
    if (!character) {
      setError("请先选择一个角色。");
      return;
    }
    await invoke("open_character_studio", { characterId: character.id });
  });
}

function runtimeFeatureAvailable(feature) {
  if (!runtimeSettingsHost) return true;
  return Object.values(runtimeCapabilityManifest?.sections || {})
    .some((section) => section?.features?.[feature] === "available");
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
    return "ready";
  }
  if (status === "running" || status === "queued") {
    return "working";
  }
  if (status === "succeeded" || ready) {
    return "ready";
  }
  if (status === "failed") {
    return "error";
  }
  if (status === "cancelled") {
    return "warning";
  }
  return "neutral";
}

function renderResourceCard(container, model) {
  if (!container) {
    return;
  }
  container.textContent = "";
  container.classList.toggle("is-muted", Boolean(model.muted));
  container.classList.toggle("is-running", model.status === "running" || model.status === "queued");
  const head = document.createElement("div");
  head.className = "resource-card__head";
  const titleWrap = document.createElement("div");
  titleWrap.className = "resource-card__title-wrap";
  const title = document.createElement("strong");
  title.textContent = model.title;
  const subtitle = document.createElement("span");
  subtitle.textContent = model.subtitle || "";
  titleWrap.append(title, subtitle);
  const status = renderSemanticStatus({
    state: model.statusTone || resourceStatusClass(model.status, model.ready),
    label: model.statusLabel || resourceStatusLabel(model.status, model.ready),
  }, "resource-card__status");
  head.append(titleWrap, status);

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
    progress.setAttribute("role", "progressbar");
    progress.setAttribute("aria-label", model.progressLabel || `${model.title}处理进度`);
    const bar = document.createElement("span");
    if (Number.isFinite(model.progress)) {
      const progressValue = Math.max(0, Math.min(100, Number(model.progress)));
      progress.setAttribute("aria-valuemin", "0");
      progress.setAttribute("aria-valuemax", "100");
      progress.setAttribute("aria-valuenow", String(progressValue));
      bar.style.width = `${progressValue}%`;
    } else {
      progress.classList.add("is-indeterminate");
    }
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
    button.textContent = action.busy ? `${action.label}…` : action.label;
    button.disabled = Boolean(action.disabled);
    if (action.focusKey) button.dataset.aboutActionKey = action.focusKey;
    if (action.resourceKey) button.dataset.aboutResourceKey = action.resourceKey;
    button.addEventListener("click", action.onClick);
    actions.append(button);
  });
  if (actions.childNodes.length) {
    body.append(actions);
  }
  container.append(head, body);
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
  const entry = memoryState.entries.find((item) => item.id === memoryState.selectedId);
  if (entry) return entry;
  const editorDraft = memoryState.editorDrafts.get(memoryState.selectedId);
  return editorDraft ? { id: memoryState.selectedId, ...editorDraft } : null;
}

function syncRuntimeMemorySettingsAvailability() {
  const settingsReadOnly = memoryState.rebinding
    || ["read_only", "failed", "stopped"].includes(memoryState.status);
  fields.memoryTriggerTurns.disabled = settingsReadOnly
    || !runtimeFeatureAvailable("memory.curation");
}

function captureMemoryEditorDraft() {
  if (!memoryState.selectedId) return;
  const draft = {
    content: fields.memoryContent.value,
    layer: fields.memoryLayer.value,
    category: fields.memoryCategory.value,
    source: fields.memorySource.value,
    importance: fields.memoryImportance.value,
    confidence: fields.memoryConfidence.value,
  };
  const committed = memoryState.entries.find((entry) => entry.id === memoryState.selectedId);
  const unchanged = committed
    && draft.content === memoryContent(committed)
    && draft.layer === (committed.layer || memoryDefaults().layer)
    && draft.category === (committed.category || "")
    && draft.source === (committed.source || memoryDefaults().source)
    && Number(draft.importance) === Number(committed.importance ?? memoryDefaults().importance)
    && Number(draft.confidence) === Number(committed.confidence ?? memoryDefaults().confidence);
  if (unchanged) {
    memoryState.editorDrafts.delete(memoryState.selectedId);
  } else {
    memoryState.editorDrafts.set(memoryState.selectedId, draft);
  }
  refreshDirty();
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

function setMemoryEditorDisabled(editorDisabled, actionsDisabled = editorDisabled) {
  [
    fields.memoryContent,
    fields.memoryLayer,
    fields.memoryCategory,
    fields.memorySource,
    fields.memoryImportance,
    fields.memoryConfidence,
  ].forEach((field) => {
    field.disabled = editorDisabled;
  });
  fields.memorySaveButton.disabled = actionsDisabled;
  fields.memoryRevertButton.disabled = actionsDisabled;
  fields.memoryDeleteButton.disabled = actionsDisabled;
  refreshSelect(fields.memoryLayer);
}

function fillMemoryEditor(record) {
  const readOnly = ["degraded", "read_only", "failed", "stopped"].includes(memoryState.status);
  const actionsDisabled = readOnly || memoryState.loading || memoryState.rebinding;
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
  const editorDraft = memoryState.editorDrafts.get(memoryState.selectedId);
  if (!memoryState.composing) {
    fields.memoryContent.value = editorDraft?.content ?? memoryContent(record);
  }
  fields.memoryLayer.value = editorDraft?.layer || record.layer || memoryDefaults().layer;
  fields.memoryCategory.value = editorDraft?.category ?? record.category ?? "";
  fields.memorySource.value = editorDraft?.source ?? record.source ?? memoryDefaults().source;
  fields.memoryImportance.value = editorDraft?.importance ?? Number(record.importance ?? memoryDefaults().importance);
  fields.memoryConfidence.value = editorDraft?.confidence ?? Number(record.confidence ?? memoryDefaults().confidence);
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
  setMemoryEditorDisabled(readOnly, actionsDisabled);
  fields.memoryDeleteButton.disabled = actionsDisabled || memoryState.selectedId === "__draft__";
  fields.memoryRevertButton.disabled = actionsDisabled || memoryState.selectedId === "__draft__";
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
  const triggerTurns = fields.memoryTriggerTurns.value
    || request?.memory?.curation?.trigger_turns;
  renderStrip(fields.memoryStatusStrip, [
    { label: "总数", value: counts.all },
    { label: "常驻档案", value: counts.core_profile },
    { label: "长期事实", value: counts.semantic },
    { label: "事件总结", value: counts.episodic },
    { label: "协作规则", value: counts.procedural },
    { label: "当前任务", value: counts.session },
    {
      label: "整理频率",
      value: triggerTurns ? `${triggerTurns} 轮` : "未配置",
    },
  ]);
}

function renderMemoryList() {
  fields.memoryList.textContent = "";
  if (memoryState.loading && memoryState.entries.length === 0) {
    const item = document.createElement("p");
    item.className = "empty-state";
    item.textContent = MEMORY_INITIALIZING_MESSAGE;
    fields.memoryList.append(item);
    return;
  }
  if (["failed", "stopped"].includes(memoryState.status)) {
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
  fields.memoryAddButton.disabled = memoryState.rebinding
    || ["loading", "degraded", "read_only", "failed", "stopped"].includes(memoryState.status);
  fields.memoryRefreshButton.disabled = memoryState.loading || memoryState.rebinding;
}

function renderMemoryInitializationState(
  message = MEMORY_INITIALIZING_MESSAGE,
  { failed = false } = {},
) {
  memoryState.loading = !failed;
  memoryState.status = failed ? "degraded" : "loading";
  memoryState.message = message;
  fields.memoryStatusStrip.textContent = "";
  renderMemoryList();
  setMemoryEditorDisabled(true, true);
  fields.memoryAddButton.disabled = true;
  fields.memoryRefreshButton.disabled = true;
}

async function loadMemories({ continueRetry = false } = {}) {
  if (!request) {
    return;
  }
  clearMemoryRetry();
  if (!continueRetry || !memoryRetryStartedAt) {
    memoryRetryStartedAt = Date.now();
  }
  captureMemoryEditorDraft();
  const loadRevision = ++memoryLoadRevision;
  const loadingMessage = MEMORY_INITIALIZING_MESSAGE;
  memoryState.loading = true;
  memoryState.status = "loading";
  memoryState.message = loadingMessage;
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
    const result = runtimeMemoryController
      ? await runtimeMemoryController.search(params)
      : await hostCall("memory.search", params);
    if (loadRevision !== memoryLoadRevision) return;
    const status = result.status || "ready";
    if (status === "loading") {
      shouldRetry = memoryRetryBudgetAvailable();
      memoryState.status = shouldRetry ? "loading" : "degraded";
      memoryState.message = shouldRetry
        ? MEMORY_INITIALIZING_MESSAGE
        : "本地记忆模型初始化超过两分钟，请点击刷新重试。";
    } else {
      memoryRetryStartedAt = 0;
      memoryState.status = status;
      memoryState.message = result.message || result.error || "";
      memoryState.entries = Array.isArray(result.memories)
        ? result.memories.filter((entry) => entry && entry.id)
        : [];
      memoryState.loaded = true;
      const preserveSelection = memoryState.selectedId === "__draft__"
        || memoryState.editorDrafts.has(memoryState.selectedId);
      if (!preserveSelection && !memoryState.entries.some((entry) => entry.id === memoryState.selectedId)) {
        memoryState.selectedId = memoryState.entries[0]?.id || "";
      }
    }
  } catch (error) {
    if (loadRevision !== memoryLoadRevision) return;
    const retryable = runtimeMemoryController
      && memoryReadErrorRetryable(error)
      && memoryRetryBudgetAvailable();
    if (retryable) {
      memoryState.status = "loading";
      memoryState.message = MEMORY_INITIALIZING_MESSAGE;
      shouldRetry = true;
    } else {
      memoryRetryStartedAt = 0;
      memoryState.status = "degraded";
      memoryState.message = runtimeMemoryController
        ? "记忆连接暂不可用；已有内容和草稿已保留，请点击刷新重试。"
        : String(error);
    }
  } finally {
    if (loadRevision !== memoryLoadRevision) return;
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
    const result = runtimeMemoryController
      ? await runtimeMemoryController.upsert(payload)
      : await hostCall("memory.upsert", payload);
    if (result.status === "loading" || result.status === "failed") {
      setError(result.error || result.message || "记忆系统暂不可用。");
      return;
    }
    const saved = result.memory || {};
    memoryState.editorDrafts.delete(memoryState.selectedId);
    memoryState.selectedId = saved.id || payload.id || "";
    memoryState.draft = null;
    await loadMemories();
    notify("已保存记忆。", "success");
  } catch (error) {
    if (runtimeMemoryController && error?.code === "MEMORY_WRITE_OUTCOME_UNCERTAIN") {
      await loadMemories();
    }
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
    const result = runtimeMemoryController
      ? await runtimeMemoryController.delete(record.id)
      : await hostCall("memory.delete", { id: record.id });
    if (Array.isArray(result.failed) && result.failed.length) {
      setError(result.failed[0].error || "记忆删除失败。");
      return;
    }
    memoryState.selectedId = "";
    memoryState.editorDrafts.delete(record.id);
    await loadMemories();
    notify("已删除记忆。", "success");
  } catch (error) {
    if (runtimeMemoryController && error?.code === "MEMORY_WRITE_OUTCOME_UNCERTAIN") {
      await loadMemories();
    }
    setError(String(error));
  }
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

function pluginFieldEditable(field) {
  return !field.readonly && !["readonly", "status", "resource"].includes(field.type);
}

function setPluginFieldValue(plugin, section, field, value) {
  const values = pluginSectionValues(plugin.id, section.section_id);
  values[field.key] = value;
  refreshDirty();
}

function initializePluginState() {
  const previouslySelectedId = pluginState.selectedId;
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
  pluginState.selectedId = request.plugins?.items?.some((item) => item.id === previouslySelectedId)
    ? previouslySelectedId
    : request.plugins?.items?.[0]?.id || "";
}

function projectPluginActivity(plugin) {
  const settings = pluginSettingsSections(plugin).map((section) => ({
    ...section,
    values: pluginState.settingsValues[plugin?.id]?.[section.section_id] || section.values,
  }));
  return pluginPresentation?.projectPluginActivity?.({ ...plugin, settings }) || {
    state: "neutral",
    label: "",
    message: "",
    hasRunningResource: false,
    isTransient: false,
  };
}

function pluginStatusCopy(plugin) {
  const plugins = request.plugins?.items || [];
  const unavailable = (plugin.missing_services || []).map((serviceKey) => (
    pluginPresentation.presentPluginComponent(serviceKey, plugins)
  ));
  return pluginPresentation.presentPluginStatus({
    state: plugin.state,
    reasonCode: plugin.reason_code,
    unavailable,
  });
}

function pluginStatusIsStarting(plugin) {
  return plugin?.reason_code === "PLUGIN_APPLICATION_NOT_READY";
}

function pluginHasExceptionalStatus(plugin) {
  if (pluginStatusIsStarting(plugin)) return false;
  const status = pluginStatusCopy(plugin);
  return Boolean(status.message || status.diagnostic);
}

function pluginInstallMenuItems() {
  return [fields.pluginInstallZipButton, fields.pluginInstallFolderButton]
    .filter((item) => item && !item.disabled);
}

function setPluginInstallMenuOpen(open, { focusItem = false, restoreFocus = false } = {}) {
  const nextOpen = Boolean(open && !fields.pluginInstallMenuButton.disabled);
  fields.pluginInstallMenu.hidden = !nextOpen;
  fields.pluginInstallMenuButton.setAttribute("aria-expanded", String(nextOpen));
  fields.pluginInstallMenuRoot.classList.toggle("is-open", nextOpen);
  if (nextOpen && focusItem) {
    pluginInstallMenuItems()[0]?.focus();
  } else if (!nextOpen && restoreFocus && !fields.pluginInstallMenuButton.disabled) {
    fields.pluginInstallMenuButton.focus();
  }
}

function movePluginInstallMenuFocus(direction) {
  const items = pluginInstallMenuItems();
  if (!items.length) return;
  const current = items.indexOf(document.activeElement);
  const next = current < 0 ? 0 : (current + direction + items.length) % items.length;
  items[next].focus();
}

function filteredPlugins() {
  const query = fields.pluginSearch.value.trim().toLowerCase();
  return (request.plugins?.items || []).filter((plugin) => {
    const text = [plugin.plugin_id, plugin.id, plugin.name, plugin.author, plugin.description]
      .join(" ")
      .toLowerCase();
    if (query && !text.includes(query)) {
      return false;
    }
    return true;
  });
}

function pluginDisplayName(plugin) {
  return `${plugin.name || plugin.plugin_id || plugin.id}（${plugin.plugin_id || plugin.id}）`;
}

function syncPluginEnableSwitches() {
  fields.pluginList.querySelectorAll(".plugin-enable-switch input[data-plugin-install-id]")
    .forEach((toggle) => {
      const plugin = (request.plugins?.items || [])
        .find((item) => item.id === toggle.dataset.pluginInstallId);
      if (plugin) toggle.checked = Boolean(pluginState.enabledById[plugin.id] || plugin.required);
    });
}

async function setPluginEnabled(plugin, enabled) {
  if (pluginState.managementBusy) return;
  const plugins = request.plugins?.items || [];
  if (enabled) {
    const dependencies = pluginPresentation.disabledRequiredPluginProviders(
      plugin,
      plugins,
      pluginState.enabledById,
    );
    if (dependencies.length) {
      const confirmed = await confirmAction(
        `“${plugin.name || plugin.id}”还需要以下插件。要一起启用吗？`,
        {
          title: "启用所需插件",
          confirmText: "一起启用",
          details: dependencies.map(pluginDisplayName),
        },
      );
      if (!confirmed) {
        syncPluginEnableSwitches();
        return;
      }
      dependencies.forEach((dependency) => {
        pluginState.enabledById[dependency.id] = true;
      });
    }
  } else {
    const dependents = pluginPresentation.enabledPluginDependents(
      plugin,
      plugins,
      pluginState.enabledById,
    );
    if (dependents.length) {
      const confirmed = await confirmAction(
        `以下插件正在依赖“${plugin.name || plugin.id}”。停用后，它们将无法使用。`,
        {
          title: "停用依赖插件",
          confirmText: "仍要停用",
          danger: true,
          details: dependents.map(pluginDisplayName),
        },
      );
      if (!confirmed) {
        syncPluginEnableSwitches();
        return;
      }
    }
  }
  pluginState.enabledById[plugin.id] = plugin.required ? true : Boolean(enabled);
  syncPluginEnableSwitches();
  refreshDirty();
}

function renderPluginList() {
  fields.pluginList.textContent = "";
  const plugins = filteredPlugins();
  if (!plugins.length) {
    const item = document.createElement("p");
    item.className = "empty-state";
    item.textContent = "没有找到符合条件的插件。";
    fields.pluginList.append(item);
    return;
  }
  plugins.forEach((plugin) => {
    const row = document.createElement("div");
    row.className = "plugin-card";
    row.classList.toggle("is-selected", plugin.id === pluginState.selectedId);
    const main = document.createElement("button");
    main.type = "button";
    main.className = "plugin-card-main";
    main.setAttribute("aria-pressed", String(plugin.id === pluginState.selectedId));
    main.addEventListener("click", () => {
      pluginState.selectedId = plugin.id;
      renderPluginPage();
    });

    const heading = document.createElement("span");
    heading.className = "plugin-card-heading";
    const titleLine = document.createElement("span");
    titleLine.className = "plugin-card-title-line";
    const title = document.createElement("strong");
    title.textContent = plugin.name || plugin.id;
    titleLine.append(title);
    const status = pluginStatusCopy(plugin);
    const exceptionalStatus = pluginHasExceptionalStatus(plugin);
    if (exceptionalStatus || pluginStatusIsStarting(plugin)) {
      const statusBadge = document.createElement("span");
      statusBadge.className = exceptionalStatus
        ? "plugin-state-badge is-error"
        : "plugin-state-badge";
      statusBadge.textContent = status.label;
      titleLine.append(statusBadge);
    }
    if (plugin.required) {
      const required = document.createElement("span");
      required.className = "plugin-state-badge is-required";
      required.textContent = "必需";
      titleLine.append(required);
    }
    const version = document.createElement("span");
    version.className = "card-meta";
    version.textContent = `${plugin.author || "未知作者"} · ${plugin.version || "0.0.0"}`;
    heading.append(titleLine, version);
    const desc = document.createElement("span");
    desc.className = "card-desc";
    desc.textContent = compactText(plugin.description || "暂无说明。", 96);
    main.append(heading, desc);

    const switchLabel = document.createElement("label");
    switchLabel.className = "plugin-enable-switch";
    switchLabel.title = plugin.required ? "Sakura 运行需要这个插件" : "启用插件";
    const toggle = document.createElement("input");
    toggle.type = "checkbox";
    toggle.setAttribute("role", "switch");
    toggle.setAttribute("aria-label", `启用 ${plugin.name || plugin.id}`);
    toggle.dataset.pluginInstallId = plugin.id;
    toggle.checked = Boolean(pluginState.enabledById[plugin.id] || plugin.required);
    toggle.disabled = Boolean(plugin.required || pluginState.managementBusy || !plugin.plugin_id
      || plugin.reason_code === "PLUGIN_ID_CONFLICT" || !plugin.supported);
    toggle.addEventListener("change", () => { void setPluginEnabled(plugin, toggle.checked); });
    const track = document.createElement("span");
    track.className = "plugin-enable-switch__track";
    track.setAttribute("aria-hidden", "true");
    switchLabel.append(toggle, track);

    row.append(main, switchLabel);
    fields.pluginList.append(row);
  });
}

function renderSemanticStatus(value, className = "") {
  const state = ["neutral", "ready", "working", "warning", "error"].includes(value?.state)
    ? value.state : "neutral";
  const status = document.createElement("span");
  status.className = `semantic-status is-${state} ${className}`.trim();
  const dot = document.createElement("span");
  dot.className = "semantic-status__dot";
  dot.setAttribute("aria-hidden", "true");
  const label = document.createElement("span");
  label.textContent = String(value?.label || "状态未知");
  status.append(dot, label);
  return status;
}

function pluginResourceStatus(value) {
  if (value.applicability === "not_required") return { state: "ready", label: "无需安装" };
  if (value.applicability === "unsupported") return { state: "warning", label: "不支持一键安装" };
  if (value.taskState === "queued") return { state: "working", label: "等待下载" };
  if (value.taskState === "running") return { state: "working", label: "下载中" };
  if (value.taskState === "failed") {
    return value.ready
      ? { state: "warning", label: "更新失败" }
      : { state: "error", label: "下载失败" };
  }
  if (value.taskState === "cancelled") return { state: "warning", label: "已取消" };
  if (value.ready) return { state: "ready", label: "已安装" };
  return { state: "neutral", label: "未安装" };
}

function pluginResourceControl(plugin, section, field, value, options = {}) {
  const container = document.createElement("div");
  container.className = "resource-card plugin-resource-card";
  const available = new Set(value.availableActionIds || []);
  const resourceKey = `${plugin.id}:${section.section_id}:${field.key}`;
  const actionModels = (section.actions || [])
    .filter((action) => available.has(action.action_id))
    .map((action, index) => {
      const busyKey = `${plugin.id}:${section.section_id}:${action.action_id}`;
      return {
        label: action.label || action.action_id,
        danger: Boolean(action.danger),
        primary: index === 0 && !value.ready
          && !["queued", "running"].includes(value.taskState),
        disabled: pluginState.managementBusy || Boolean(pluginState.actionBusyKey),
        busy: pluginState.actionBusyKey === busyKey,
        focusKey: options.focusActions ? busyKey : "",
        resourceKey: options.focusActions ? resourceKey : "",
        onClick: () => runPluginSettingsAction(
          plugin,
          section,
          action,
          options.focusActions ? resourceKey : "",
        ),
      };
    });
  const progressVisible = ["queued", "running"].includes(value.taskState);
  const status = pluginResourceStatus(value);
  const detail = [
    value.detail,
    progressVisible && Number.isSafeInteger(value.progress) ? `${value.progress}%` : "",
  ].filter(Boolean).join(" · ");
  renderResourceCard(container, {
    title: field.label || field.key,
    subtitle: [options.owner, value.subtitle].filter(Boolean).join(" · "),
    status: value.taskState,
    ready: Boolean(value.ready),
    statusLabel: status.label,
    statusTone: status.state,
    message: value.message || field.description || "",
    detail,
    progressVisible,
    progress: value.progress,
    progressLabel: `${field.label || field.key}下载进度`,
    actions: actionModels,
  });
  return container;
}

function aboutComponentContributions() {
  const contributions = [];
  (request?.plugins?.items || []).filter((plugin) => plugin.enabled).forEach((plugin) => {
    pluginSettingsSections(plugin)
      .filter((section) => section.surface === "about")
      .forEach((section) => {
        (section.fields || []).filter((field) => field.type === "resource").forEach((field) => {
          contributions.push({
            plugin,
            section,
            field,
            value: pluginFieldValue(plugin, section, field) || {},
          });
        });
      });
  });
  return contributions.sort((left, right) => (
    String(left.plugin.name || left.plugin.plugin_id).localeCompare(
      String(right.plugin.name || right.plugin.plugin_id), "zh-CN",
    ) || String(left.field.label || left.field.key).localeCompare(
      String(right.field.label || right.field.key), "zh-CN",
    )
  ));
}

function aboutComponentsRunning() {
  return aboutComponentContributions().some(({ value }) => (
    ["queued", "running"].includes(value.taskState)
  ));
}

function renderAboutComponents({ restoreResourceKey = "" } = {}) {
  if (!fields.aboutComponentsList) return;
  const focusedKey = document.activeElement?.dataset?.aboutActionKey || "";
  const focusedResourceKey = document.activeElement?.dataset?.aboutResourceKey || restoreResourceKey;
  const contributions = aboutComponentContributions();
  const ready = contributions.filter(({ value }) => (
    value.ready || value.applicability === "not_required"
  )).length;
  const unsupported = contributions.filter(({ value }) => value.applicability === "unsupported").length;
  fields.aboutComponentsSummary.textContent = contributions.length
    ? `${ready}/${contributions.length} 已就绪${unsupported ? ` · ${unsupported} 项当前平台不支持` : ""}`
    : "启用插件尚未注册本地组件";
  fields.aboutComponentsRefresh.disabled = pluginActivityRefreshInFlight;
  fields.aboutComponentsState.textContent = aboutComponentsReadError;
  const snapshot = runtimePluginController?.snapshot?.();
  if (!aboutComponentsReadError && snapshot && ["starting", "waiting"].includes(snapshot.state)) {
    fields.aboutComponentsState.textContent = "插件 Worker 正在初始化…";
  }
  fields.aboutComponentsList.textContent = "";
  contributions.forEach(({ plugin, section, field, value }) => {
    fields.aboutComponentsList.append(pluginResourceControl(
      plugin,
      section,
      field,
      value,
      { owner: plugin.name || plugin.plugin_id, focusActions: true },
    ));
  });
  if (focusedKey) {
    const action = fields.aboutComponentsList.querySelector(
      `[data-about-action-key="${CSS.escape(focusedKey)}"]`,
    );
    const fallback = focusedResourceKey ? fields.aboutComponentsList.querySelector(
      `[data-about-resource-key="${CSS.escape(focusedResourceKey)}"]`,
    ) : null;
    (action || fallback)?.focus({ preventScroll: true });
  }
  schedulePluginActivityRefresh();
}

function pluginSettingControl(plugin, section, field) {
  const value = pluginFieldValue(plugin, section, field);
  if (field.type === "status") {
    const control = document.createElement("div");
    control.className = "plugin-status-control";
    control.append(renderSemanticStatus(value));
    if (value?.message && value.state !== "ready" && value.state !== "neutral") {
      const message = document.createElement("p");
      message.className = "plugin-status-message";
      message.textContent = value.message;
      control.append(message);
    }
    return control;
  }
  if (field.type === "resource") {
    return pluginResourceControl(plugin, section, field, value || {});
  }
  if (field.readonly || field.type === "readonly") {
    const row = document.createElement("div");
    row.className = "plugin-readonly-control";
    const output = document.createElement("output");
    output.className = "plugin-readonly-output";
    output.textContent = Array.isArray(value) ? value.join(" ; ") : String(value ?? "");
    row.append(output);
    if (field.copyable) {
      const copy = document.createElement("button");
      copy.type = "button";
      copy.className = "secondary-button compact-button";
      copy.textContent = "复制";
      copy.addEventListener("click", async () => {
        await navigator.clipboard.writeText(output.textContent || "");
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
    select.addEventListener("change", () => {
      const selected = (field.options || []).find((option) => String(option.value) === select.value);
      setPluginFieldValue(plugin, section, field, selected ? selected.value : select.value);
    });
    window.setTimeout(() => enhanceSelect(select), 0);
    return select;
  }
  const input = document.createElement("input");
  input.type = field.type === "integer" || field.type === "number" ? "number" : field.type === "password" ? "password" : "text";
  if (Number.isSafeInteger(field.maxLength)) input.maxLength = field.maxLength;
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

function pluginCollectionKey(plugin, section, collection) {
  return `${plugin.id}:${section.section_id}:${collection.collection_id}`;
}

function pluginCollectionRuntimeState(plugin, section, collection) {
  const key = pluginCollectionKey(plugin, section, collection);
  if (!pluginCollectionState.has(key)) {
    pluginCollectionState.set(key, {
      surface: section.surface,
      items: [], nextCursor: null, total: null, search: "", filters: {},
      loading: false, loaded: false, error: "", editor: null, editorError: "",
      selectedItemId: "", searchTimer: null, queryRevision: 0, queryPending: false,
      queryPendingRender: false, operation: "", motion: null,
    });
  }
  return pluginCollectionState.get(key);
}

async function queryPluginCollection(
  plugin,
  section,
  collection,
  { append = false, render = true } = {},
) {
  if (!request?.plugins?.items.includes(plugin)) return;
  if (section.surface === "memory" && (
    memoryState.rebinding || characterSwitching || pendingRuntimeCharacterId()
  )) return;
  if (section.surface === "memory" && memoryActivityBlocksCollection(projectPluginActivity(plugin))) return;
  const state = pluginCollectionRuntimeState(plugin, section, collection);
  if (!runtimePluginController) return;
  if (state.loading) {
    state.queryPending = true;
    state.queryPendingRender ||= render;
    return;
  }
  const collectionKey = pluginCollectionKey(plugin, section, collection);
  const queryRevision = state.queryRevision;
  const querySearch = state.search;
  const queryFilters = clonePlain(state.filters);
  state.loading = true;
  state.error = "";
  if (render && !state.loaded) {
    if (section.surface === "memory") renderMemorySurface();
    else renderPluginPage();
  }
  try {
    const result = await runtimePluginController.collection({
      operation: "query",
      pluginId: plugin.plugin_id,
      sectionId: section.section_id,
      collectionId: collection.collection_id,
      cursor: append ? state.nextCursor : null,
      limit: collection.page_size,
      search: querySearch,
      filters: queryFilters,
    });
    if (pluginCollectionState.get(collectionKey) !== state) return;
    if (section.surface === "memory" && (memoryState.rebinding || characterSwitching)) return;
    if (queryRevision !== state.queryRevision) {
      state.queryPending = true;
      return;
    }
    state.items = append ? [...state.items, ...result.items] : result.items;
    state.nextCursor = result.nextCursor;
    state.total = result.total;
    state.loaded = true;
  } catch (error) {
    if (pluginCollectionState.get(collectionKey) !== state) return;
    if (queryRevision === state.queryRevision) state.error = String(error);
    else state.queryPending = true;
  } finally {
    if (pluginCollectionState.get(collectionKey) !== state) return;
    state.loading = false;
    if (section.surface === "memory" && (memoryState.rebinding || characterSwitching)) {
      state.queryPending = false;
      state.queryPendingRender = false;
      return;
    }
    if (state.queryPending) {
      const pendingRender = state.queryPendingRender;
      state.queryPending = false;
      state.queryPendingRender = false;
      window.setTimeout(() => queryPluginCollection(
        plugin,
        section,
        collection,
        { render: pendingRender },
      ), 0);
      return;
    }
    if (render && section.surface === "memory") {
      const active = document.activeElement;
      const restoreFocus = active?.classList.contains("memory-search-input")
        && active.dataset.collectionKey === collectionKey;
      const selectionStart = restoreFocus ? active.selectionStart : null;
      const selectionEnd = restoreFocus ? active.selectionEnd : null;
      renderMemorySurface();
      if (restoreFocus) {
        window.setTimeout(() => {
          const input = Array.from(fields.memorySurface.querySelectorAll(".memory-search-input"))
            .find((element) => element.dataset.collectionKey === collectionKey);
          input?.focus();
          if (input && selectionStart !== null && selectionEnd !== null) {
            input.setSelectionRange(selectionStart, selectionEnd);
          }
        }, 0);
      }
    } else if (render) {
      renderPluginPage();
    }
  }
}

function pluginCollectionFieldControl(field, value, onChange) {
  if (field.type === "boolean") {
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = Boolean(value);
    input.disabled = Boolean(field.readonly);
    input.addEventListener("change", () => onChange(input.checked));
    return input;
  }
  if (field.type === "select") {
    const select = document.createElement("select");
    (field.options || []).forEach((option) => {
      const item = document.createElement("option");
      item.value = String(option.value);
      item.textContent = option.label;
      select.append(item);
    });
    select.value = String(value ?? "");
    select.disabled = Boolean(field.readonly);
    select.addEventListener("change", () => {
      const option = (field.options || []).find((item) => String(item.value) === select.value);
      onChange(option ? option.value : select.value);
    });
    window.setTimeout(() => enhanceSelect(select), 0);
    return select;
  }
  const input = field.type === "string" && !field.readonly
    ? document.createElement("textarea")
    : document.createElement("input");
  if (input.tagName === "INPUT") {
    input.type = ["integer", "number"].includes(field.type)
      ? "number" : field.type === "password" ? "password" : "text";
    if (Number.isSafeInteger(field.maxLength)) input.maxLength = field.maxLength;
  }
  input.value = String(value ?? "");
  input.disabled = Boolean(field.readonly);
  if (typeof field.minimum === "number") input.min = String(field.minimum);
  if (typeof field.maximum === "number") input.max = String(field.maximum);
  if (typeof field.step === "number") input.step = String(field.step);
  input.addEventListener("input", () => {
    if (field.type === "integer") onChange(Number.parseInt(input.value, 10));
    else if (field.type === "number") onChange(Number.parseFloat(input.value));
    else onChange(input.value);
  });
  return input;
}

async function mutatePluginCollection(plugin, section, collection, operation) {
  const state = pluginCollectionRuntimeState(plugin, section, collection);
  const memorySurface = section.surface === "memory";
  if (!runtimePluginController || state.loading || !state.editor) return;
  if (operation !== "delete") {
    const invalid = (collection.fields || []).find((field) => {
      const value = state.editor.values[field.key];
      return field.required && (value === null || value === undefined || String(value).trim() === "");
    });
    if (invalid) {
      state.editorError = `请填写“${invalid.label}”。`;
      if (!memorySurface || !syncMemoryEditorPortalState(state)) {
        renderPluginPage();
        renderMemorySurface();
      }
      return;
    }
  }
  if (operation === "delete") {
    const confirmed = await confirmAction(collection.delete_confirmation, {
      title: "删除记忆",
      confirmText: "删除",
      cancelText: "保留",
      danger: true,
    });
    if (!confirmed) return;
  }
  const editorItemId = state.editor.itemId;
  state.loading = true;
  state.operation = operation;
  state.error = "";
  state.editorError = "";
  if (memorySurface) {
    syncMemoryEditorPortalState(state);
  } else {
    renderPluginPage();
    renderMemorySurface();
  }
  let completed = false;
  try {
    let result;
    if (operation === "delete") {
      result = await runtimePluginController.collection({
        operation,
        pluginId: plugin.plugin_id,
        sectionId: section.section_id,
        collectionId: collection.collection_id,
        itemId: editorItemId,
      });
    } else {
      result = await runtimePluginController.collection({
        operation,
        pluginId: plugin.plugin_id,
        sectionId: section.section_id,
        collectionId: collection.collection_id,
        ...(operation === "update" ? { itemId: editorItemId } : {}),
        values: clonePlain(state.editor.values),
      });
    }
    const affectedItemId = operation === "delete" ? editorItemId : result.itemId;
    state.editor = null;
    state.selectedItemId = operation === "delete" ? "" : affectedItemId;
    state.loading = false;
    if (memorySurface) {
      applyMemoryCollectionMutationResult(state, collection, operation, result, affectedItemId);
      await queryPluginCollection(plugin, section, collection, { render: false });
      await dismissMemoryEditorPortal();
      if (operation === "delete") await animateMemoryRecordRemoval(affectedItemId);
      else state.motion = { kind: operation, itemId: affectedItemId };
      renderMemorySurface();
      completed = true;
      notify(operation === "delete" ? "记忆已删除。" : operation === "create" ? "记忆已新增。" : "记忆已更新。", "success");
    } else {
      state.loaded = false;
      await queryPluginCollection(plugin, section, collection);
      completed = true;
    }
  } catch (error) {
    state.error = String(error);
  } finally {
    state.loading = false;
    state.operation = "";
    if (memorySurface) {
      if (!completed && !syncMemoryEditorPortalState(state)) renderMemorySurface();
    } else {
      renderPluginPage();
      renderMemorySurface();
    }
  }
}

function renderPluginCollection(plugin, section, collection) {
  const state = pluginCollectionRuntimeState(plugin, section, collection);
  const block = document.createElement("div");
  block.className = "plugin-collection";
  const header = document.createElement("div");
  header.className = "plugin-collection-head";
  const heading = document.createElement("h4");
  heading.textContent = collection.title;
  header.append(heading);
  if (collection.can_create) {
    const add = document.createElement("button");
    add.type = "button";
    add.className = "secondary-button";
    add.textContent = "新增";
    add.addEventListener("click", () => {
      state.editor = {
        itemId: null,
        values: Object.fromEntries((collection.fields || [])
          .filter(pluginFieldEditable)
          .map((field) => [field.key, field.default])),
      };
      renderPluginPage();
      renderMemorySurface();
    });
    header.append(add);
  }
  block.append(header);
  if (collection.description) {
    const description = document.createElement("p");
    description.className = "hint";
    description.textContent = collection.description;
    block.append(description);
  }
  const toolbar = document.createElement("div");
  toolbar.className = "plugin-collection-toolbar";
  if (collection.searchable) {
    const search = document.createElement("input");
    search.type = "search";
    search.placeholder = "搜索";
    search.value = state.search;
    search.addEventListener("change", () => {
      state.search = search.value.trim();
      queryPluginCollection(plugin, section, collection);
    });
    toolbar.append(search);
  }
  (collection.filters || []).forEach((filter) => {
    const select = document.createElement("select");
    const all = document.createElement("option");
    all.value = "";
    all.textContent = `全部${filter.label}`;
    select.append(all);
    filter.options.forEach((option) => {
      const item = document.createElement("option");
      item.value = String(option.value);
      item.textContent = option.label;
      select.append(item);
    });
    select.value = Object.hasOwn(state.filters, filter.key) ? String(state.filters[filter.key]) : "";
    select.addEventListener("change", () => {
      const selected = filter.options.find((option) => String(option.value) === select.value);
      if (selected) state.filters[filter.key] = selected.value;
      else delete state.filters[filter.key];
      queryPluginCollection(plugin, section, collection);
    });
    toolbar.append(select);
    window.setTimeout(() => enhanceSelect(select), 0);
  });
  if (toolbar.children.length) block.append(toolbar);
  if (state.error) {
    const error = document.createElement("p");
    error.className = "error";
    error.textContent = state.error;
    block.append(error);
  }
  if (state.loading && !state.loaded) {
    const loading = document.createElement("p");
    loading.className = "page-note";
    loading.textContent = "正在加载…";
    block.append(loading);
  } else if (state.loaded && !state.items.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "暂无数据。";
    block.append(empty);
  } else if (state.items.length) {
    const table = document.createElement("table");
    table.className = "plugin-collection-table";
    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    collection.columns.forEach((column) => {
      const cell = document.createElement("th");
      cell.textContent = column.label;
      headRow.append(cell);
    });
    head.append(headRow);
    const body = document.createElement("tbody");
    state.items.forEach((item) => {
      const row = document.createElement("tr");
      collection.columns.forEach((column) => {
        const cell = document.createElement("td");
        const value = item.values[column.key];
        cell.textContent = column.type === "boolean" ? (value ? "是" : "否") : String(value ?? "");
        row.append(cell);
      });
      if (collection.can_update || collection.can_delete) {
        row.tabIndex = 0;
        row.addEventListener("click", () => {
          state.editor = {
            itemId: item.itemId,
            values: Object.fromEntries((collection.fields || [])
              .filter(pluginFieldEditable)
              .map((field) => [field.key, item.values[field.key] ?? field.default])),
          };
          renderPluginPage();
          renderMemorySurface();
        });
      }
      body.append(row);
    });
    table.append(head, body);
    const scroll = document.createElement("div");
    scroll.className = "plugin-collection-scroll";
    scroll.append(table);
    block.append(scroll);
  }
  if (state.nextCursor) {
    const more = document.createElement("button");
    more.type = "button";
    more.className = "secondary-button";
    more.textContent = state.loading ? "加载中…" : "加载更多";
    more.disabled = state.loading;
    more.addEventListener("click", () => queryPluginCollection(plugin, section, collection, { append: true }));
    block.append(more);
  }
  if (state.editor) {
    const editor = document.createElement("div");
    editor.className = "plugin-collection-editor";
    (collection.fields || []).forEach((field) => {
      const row = document.createElement("div");
      row.className = "form-row";
      const label = document.createElement("label");
      label.textContent = field.label;
      const control = pluginCollectionFieldControl(
        field,
        state.editor.values[field.key] ?? field.default,
        (value) => { state.editor.values[field.key] = value; },
      );
      row.append(label, control);
      editor.append(row);
    });
    const actions = document.createElement("div");
    actions.className = "plugin-setting-actions";
    const save = document.createElement("button");
    save.type = "button";
    save.className = "secondary-button";
    save.textContent = state.editor.itemId ? "更新" : "创建";
    save.disabled = state.loading || (state.editor.itemId ? !collection.can_update : !collection.can_create);
    save.addEventListener("click", () => mutatePluginCollection(
      plugin, section, collection, state.editor.itemId ? "update" : "create",
    ));
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "secondary-button";
    cancel.textContent = "取消";
    cancel.addEventListener("click", () => {
      state.editor = null;
      state.editorError = "";
      renderPluginPage();
      renderMemorySurface();
    });
    actions.append(save, cancel);
    if (state.editor.itemId && collection.can_delete) {
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "danger-button";
      remove.textContent = "删除";
      remove.addEventListener("click", () => mutatePluginCollection(plugin, section, collection, "delete"));
      actions.append(remove);
    }
    editor.append(actions);
    block.append(editor);
  }
  if (!state.loaded && !state.loading && !state.error) {
    window.setTimeout(() => queryPluginCollection(plugin, section, collection), 0);
  }
  return block;
}

function renderPluginSettings(plugin) {
  const allSections = pluginSettingsSections(plugin);
  const knownSurfaces = new Set(["memory", "voice", "about"]);
  const sections = allSections.filter((section) => !knownSurfaces.has(section.surface));
  const container = document.createElement("div");
  container.className = "plugin-settings";
  allSections.filter((section) => ["memory", "voice"].includes(section.surface)).forEach((section) => {
    const link = document.createElement("button");
    link.type = "button";
    link.className = "secondary-button plugin-surface-link";
    link.textContent = section.surface === "memory" ? "前往记忆页管理" : "前往语音页设置";
    link.addEventListener("click", () => showPage(section.surface));
    container.append(link);
  });
  if ((request?.api?.slot_fields || []).some((slot) => slot.owner_id === plugin.plugin_id)) {
    const link = document.createElement("button");
    link.type = "button";
    link.className = "secondary-button plugin-surface-link";
    link.textContent = "前往模型页设置";
    link.addEventListener("click", () => showPage("model"));
    container.append(link);
  }
  if (!sections.length) {
    const empty = document.createElement("p");
    empty.className = "page-note";
    empty.textContent = plugin.enabled
      ? "这个插件没有需要设置的内容。"
      : "应用启用后即可设置此插件。";
    container.append(empty);
    return container;
  }
  sections.forEach((section) => {
    const block = document.createElement("section");
    block.className = "plugin-settings-section";
    const header = document.createElement("div");
    header.className = "plugin-settings-section-head";
    const heading = document.createElement("h3");
    heading.textContent = section.title || section.section_id;
    header.append(heading);
    const headerStatusField = (section.fields || []).find(
      (field) => field.type === "status" && field.placement === "section_header",
    );
    if (headerStatusField) {
      const statusValue = pluginFieldValue(plugin, section, headerStatusField);
      header.append(renderSemanticStatus(statusValue, "plugin-section-status"));
    }
    block.append(header);
    if (
      headerStatusField
      && pluginFieldValue(plugin, section, headerStatusField)?.message
      && !["ready", "neutral"].includes(pluginFieldValue(plugin, section, headerStatusField).state)
    ) {
      const message = document.createElement("p");
      message.className = "plugin-status-message is-section-message";
      message.textContent = pluginFieldValue(plugin, section, headerStatusField).message;
      block.append(message);
    }
    if (section.error || (section.reason_code && section.reason_code !== "READY")) {
      const error = document.createElement("p");
      error.className = "error";
      const stableError = typeof section.error === "string"
        && /^[A-Z0-9_]{1,64}$/.test(section.error)
        ? section.error
        : "";
      const presentation = pluginPresentation.presentPluginReason(
        stableError || section.reason_code,
      );
      error.textContent = section.error && !stableError
        ? section.error
        : [presentation?.message, presentation?.diagnostic].filter(Boolean).join(" ");
      block.append(error);
    }
    (section.fields || []).filter((field) => field !== headerStatusField).forEach((field) => {
      const row = document.createElement("div");
      row.className = field.type === "resource" ? "plugin-resource-row" : "form-row";
      const control = pluginSettingControl(plugin, section, field);
      if (field.type !== "boolean" && field.description) {
        control.title = field.description;
      }
      if (field.type === "resource") {
        row.append(control);
      } else {
        const label = document.createElement("label");
        label.textContent = field.label || field.key;
        row.append(label, control);
      }
      if (field.restart_required) {
        const hint = document.createElement("p");
        hint.className = "hint";
        hint.textContent = "保存时会重新启动插件 Worker。";
        row.append(hint);
      }
      block.append(row);
    });
    const embeddedActionIds = new Set(
      (section.fields || [])
        .filter((field) => field.type === "resource")
        .flatMap((field) => field.actionIds || []),
    );
    const standaloneActions = (section.actions || []).filter(
      (action) => !embeddedActionIds.has(action.action_id),
    );
    if (standaloneActions.length) {
      const actions = document.createElement("div");
      actions.className = "plugin-setting-actions";
      standaloneActions.forEach((action) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = action.danger ? "danger-button" : "secondary-button";
        button.textContent = action.label || action.action_id;
        const busyKey = `${plugin.id}:${section.section_id}:${action.action_id}`;
        button.disabled = pluginState.managementBusy || pluginState.actionBusyKey === busyKey;
        button.addEventListener("click", () => runPluginSettingsAction(plugin, section, action));
        actions.append(button);
      });
      block.append(actions);
    }
    (section.collections || []).forEach((collection) => {
      block.append(renderPluginCollection(plugin, section, collection));
    });
    container.append(block);
  });
  return container;
}

function memorySurfaceIsTransitioning() {
  const snapshot = runtimePluginController?.snapshot();
  if (!snapshot) return false;
  if (["starting", "waiting"].includes(snapshot.state)) return true;
  if (snapshot.plugins.some((plugin) => plugin.enabled
      && ["starting", "waiting"].includes(plugin.state))) return true;
  return (request?.plugins?.items || []).some((plugin) => (
    pluginSettingsSections(plugin).some((section) => section.surface === "memory")
    && projectPluginActivity(plugin).state === "working"
  ));
}

function selectedPluginHasTransientActivity() {
  const plugin = (request?.plugins?.items || []).find((item) => item.id === pluginState.selectedId);
  return Boolean(plugin && projectPluginActivity(plugin).isTransient);
}

function pluginActivityPageVisible() {
  return fields.pages.memory.classList.contains("is-active")
    || fields.pages.plugins.classList.contains("is-active")
    || fields.pages.about.classList.contains("is-active");
}

function visiblePluginActivityIsTransient() {
  const snapshot = runtimePluginController?.snapshot();
  if (["starting", "waiting"].includes(snapshot?.state)) return pluginActivityPageVisible();
  if (fields.pages.memory.classList.contains("is-active")) return memorySurfaceIsTransitioning();
  if (fields.pages.plugins.classList.contains("is-active")) return selectedPluginHasTransientActivity();
  if (fields.pages.about.classList.contains("is-active")) return aboutComponentsRunning();
  return false;
}

function clearPluginActivityRefresh() {
  window.clearTimeout(pluginActivityRefreshTimer);
  pluginActivityRefreshTimer = null;
}

function schedulePluginActivityRefresh() {
  clearPluginActivityRefresh();
  if (!runtimePluginController || !visiblePluginActivityIsTransient()) return;
  pluginActivityRefreshTimer = window.setTimeout(refreshPluginActivityCurrent, 1200);
}

async function refreshPluginActivityCurrent() {
  if (pluginActivityRefreshInFlight || !runtimePluginController || !pluginActivityPageVisible()) return;
  pluginActivityRefreshInFlight = true;
  try {
    await runtimePluginController.refreshCurrent();
    aboutComponentsReadError = "";
  } catch {
    aboutComponentsReadError = "暂时无法读取组件状态，请稍后刷新。";
    renderAboutComponents();
  } finally {
    pluginActivityRefreshInFlight = false;
    schedulePluginActivityRefresh();
  }
}

function memoryCollectionOptionLabel(collection, key, value) {
  const options = collection.filters?.find((filter) => filter.key === key)?.options
    || collection.fields?.find((field) => field.key === key)?.options
    || [];
  return options.find((option) => option.value === value)?.label || String(value || "未分类");
}

function formatMemoryTimestamp(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  }).format(date);
}

function memoryEditorValues(collection, item = null) {
  return Object.fromEntries((collection.fields || [])
    .filter(pluginFieldEditable)
    .map((field) => [field.key, item?.values?.[field.key] ?? field.default ?? ""]));
}

function clearMemoryEditorPortal() {
  document.querySelectorAll(".memory-editor-overlay").forEach((overlay) => overlay.remove());
  document.querySelector(".settings-shell")?.removeAttribute("inert");
}

async function dismissMemoryEditorPortal() {
  const overlays = Array.from(document.querySelectorAll(".memory-editor-overlay"));
  if (!overlays.length) {
    document.querySelector(".settings-shell")?.removeAttribute("inert");
    return;
  }
  await Promise.all(overlays.map((overlay) => removeOverlayAfterExit(overlay)));
  if (!document.querySelector(".memory-editor-overlay")) {
    document.querySelector(".settings-shell")?.removeAttribute("inert");
  }
}

function mountMemoryEditorPortal(overlay) {
  clearMemoryEditorPortal();
  document.querySelector(".settings-shell")?.setAttribute("inert", "");
  document.body.append(overlay);
}

function syncMemoryEditorPortalState(state) {
  const overlay = document.querySelector(".memory-editor-overlay");
  const dialog = overlay?.querySelector(".memory-record-dialog");
  if (!overlay || !dialog) return false;
  const busy = Boolean(state.loading && state.operation);
  dialog.classList.toggle("is-busy", busy);
  dialog.setAttribute("aria-busy", String(busy));
  overlay.querySelectorAll("input, textarea, select, button").forEach((control) => {
    if (busy) {
      if (!control.hasAttribute("data-memory-disabled-before")) {
        control.dataset.memoryDisabledBefore = String(control.disabled);
      }
      control.disabled = true;
    } else if (control.hasAttribute("data-memory-disabled-before")) {
      control.disabled = control.dataset.memoryDisabledBefore === "true";
      delete control.dataset.memoryDisabledBefore;
    }
  });
  dialog.querySelectorAll("[data-memory-action]").forEach((action) => {
    const actionName = action.dataset.memoryAction;
    const isWorkingAction = busy && (
      (state.operation === "delete" && actionName === "delete")
      || (state.operation !== "delete" && actionName === "save")
    );
    action.classList.toggle("is-working", isWorkingAction);
    if (isWorkingAction) {
      action.textContent = state.operation === "delete"
        ? "删除中…" : state.operation === "create" ? "新增中…" : "保存中…";
    } else if (action.dataset.idleLabel) {
      action.textContent = action.dataset.idleLabel;
    }
  });
  const errorMessage = state.editorError || state.error;
  let error = dialog.querySelector(".memory-dialog-error");
  if (errorMessage) {
    if (!error) {
      error = document.createElement("p");
      error.className = "memory-dialog-error";
      error.setAttribute("role", "alert");
      dialog.insertBefore(error, dialog.querySelector(".memory-dialog-actions"));
    }
    error.textContent = errorMessage;
  } else {
    error?.remove();
  }
  return true;
}

function applyMemoryCollectionMutationResult(state, collection, operation, result, itemId) {
  if (operation === "delete") {
    state.items = state.items.filter((item) => item.itemId !== itemId);
    if (state.total !== null) state.total = Math.max(0, state.total - 1);
  } else {
    const item = result;
    const existingIndex = state.items.findIndex((entry) => entry.itemId === item.itemId);
    if (existingIndex >= 0) {
      state.items.splice(existingIndex, 1, item);
    } else {
      state.items.unshift(item);
      if (state.total !== null) state.total += 1;
      if (state.items.length > collection.page_size) state.items.length = collection.page_size;
    }
  }
  state.loaded = true;
}

function memoryRecordCardById(itemId) {
  return Array.from(fields.memorySurface.querySelectorAll(".memory-record-card"))
    .find((card) => card.dataset.itemId === itemId) || null;
}

async function animateMemoryRecordRemoval(itemId) {
  const card = memoryRecordCardById(itemId);
  if (!card || reduceMotionQuery?.matches) return;
  card.style.setProperty("--memory-record-height", `${card.getBoundingClientRect().height}px`);
  card.classList.add("is-removing");
  await new Promise((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      window.clearTimeout(fallbackTimer);
      card.removeEventListener("animationend", onAnimationEnd);
      resolve();
    };
    const onAnimationEnd = (event) => {
      if (event.target === card) finish();
    };
    const fallbackTimer = window.setTimeout(finish, 360);
    card.addEventListener("animationend", onAnimationEnd);
  });
}

function openMemoryCollectionEditor(plugin, section, collection, item = null) {
  const state = pluginCollectionRuntimeState(plugin, section, collection);
  state.editor = {
    itemId: item?.itemId || null,
    values: memoryEditorValues(collection, item),
  };
  state.editorError = "";
  state.selectedItemId = item?.itemId || "";
  renderMemorySurface();
  window.setTimeout(() => document.querySelector(
    ".memory-editor-overlay .memory-record-dialog textarea, .memory-editor-overlay .memory-record-dialog input",
  )?.focus(), 0);
}

function renderMemoryEditor(plugin, section, collection, state) {
  const overlay = document.createElement("div");
  overlay.className = "memory-editor-overlay";
  const dialog = document.createElement("section");
  dialog.className = "memory-record-dialog";
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-labelledby", "memoryRecordDialogTitle");

  const head = document.createElement("header");
  head.className = "memory-dialog-head";
  const headingGroup = document.createElement("div");
  const eyebrow = document.createElement("span");
  eyebrow.className = "memory-eyebrow";
  eyebrow.textContent = state.editor.itemId ? "长期记忆 · 编辑" : "长期记忆 · 新建";
  const heading = document.createElement("h2");
  heading.id = "memoryRecordDialogTitle";
  heading.textContent = state.editor.itemId ? "编辑这条记忆" : "写下一条记忆";
  const subtitle = document.createElement("p");
  subtitle.textContent = state.editor.itemId
    ? "修改后会直接更新当前角色的记忆库。"
    : "只记录未来对话中仍然有用的事实、偏好或协作方式。";
  headingGroup.append(eyebrow, heading, subtitle);
  const closeButton = document.createElement("button");
  closeButton.type = "button";
  closeButton.className = "memory-dialog-close";
  closeButton.setAttribute("aria-label", "关闭编辑器");
  closeButton.textContent = "×";
  head.append(headingGroup, closeButton);

  const form = document.createElement("div");
  form.className = "memory-dialog-form";
  (collection.fields || []).filter(pluginFieldEditable).forEach((field) => {
    const group = document.createElement("label");
    group.className = `memory-dialog-field${field.key === "content" ? " is-content" : ""}`;
    const label = document.createElement("span");
    label.className = "memory-dialog-label";
    label.textContent = field.required ? `${field.label} *` : field.label;
    let control;
    if (field.type === "select") {
      control = document.createElement("select");
      (field.options || []).forEach((option) => {
        const element = document.createElement("option");
        element.value = String(option.value);
        element.textContent = option.label;
        control.append(element);
      });
      control.value = String(state.editor.values[field.key] ?? field.default ?? "");
      control.addEventListener("change", () => {
        const option = (field.options || []).find((item) => String(item.value) === control.value);
        state.editor.values[field.key] = option ? option.value : control.value;
      });
      window.setTimeout(() => enhanceSelect(control), 0);
    } else if (field.key === "content") {
      control = document.createElement("textarea");
      control.rows = 7;
      if (Number.isSafeInteger(field.maxLength)) control.maxLength = field.maxLength;
      control.value = String(state.editor.values[field.key] ?? "");
      control.placeholder = "例如：用户喜欢简洁直接的回答，并希望先给结论。";
      control.addEventListener("input", () => {
        state.editor.values[field.key] = control.value;
        const counter = group.querySelector(".memory-character-count");
        if (counter) counter.textContent = `${control.value.length} / ${field.maxLength}`;
      });
    } else {
      control = document.createElement("input");
      control.type = ["integer", "number"].includes(field.type) ? "number" : "text";
      if (typeof field.minimum === "number") control.min = String(field.minimum);
      if (typeof field.maximum === "number") control.max = String(field.maximum);
      if (typeof field.step === "number") control.step = String(field.step);
      if (Number.isSafeInteger(field.maxLength)) control.maxLength = field.maxLength;
      control.value = String(state.editor.values[field.key] ?? "");
      control.addEventListener("input", () => {
        state.editor.values[field.key] = ["integer", "number"].includes(field.type)
          ? Number(control.value) : control.value;
      });
    }
    group.append(label, control);
    if (field.key === "content" && Number.isSafeInteger(field.maxLength)) {
      const counter = document.createElement("span");
      counter.className = "memory-character-count";
      counter.textContent = `${String(state.editor.values[field.key] ?? "").length} / ${field.maxLength}`;
      group.append(counter);
    } else if (field.description) {
      const description = document.createElement("small");
      description.textContent = field.description;
      group.append(description);
    }
    form.append(group);
  });

  const footer = document.createElement("footer");
  footer.className = "memory-dialog-actions";
  const utilityActions = document.createElement("div");
  if (state.editor.itemId && collection.can_delete) {
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "danger-button";
    remove.textContent = "删除记忆";
    remove.dataset.memoryAction = "delete";
    remove.dataset.idleLabel = "删除记忆";
    remove.disabled = state.loading;
    remove.addEventListener("click", () => mutatePluginCollection(plugin, section, collection, "delete"));
    utilityActions.append(remove);
  }
  const primaryActions = document.createElement("div");
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "secondary-button";
  cancel.textContent = "取消";
  const save = document.createElement("button");
  save.type = "button";
  save.textContent = state.editor.itemId ? "保存修改" : "新增记忆";
  save.dataset.memoryAction = "save";
  save.dataset.idleLabel = save.textContent;
  save.disabled = state.loading || (state.editor.itemId ? !collection.can_update : !collection.can_create);
  save.addEventListener("click", () => mutatePluginCollection(
    plugin, section, collection, state.editor.itemId ? "update" : "create",
  ));
  primaryActions.append(cancel, save);
  footer.append(utilityActions, primaryActions);

  const close = async () => {
    if (state.loading) return;
    state.editor = null;
    state.editorError = "";
    await dismissMemoryEditorPortal();
    renderMemorySurface();
  };
  cancel.addEventListener("click", () => { void close(); });
  closeButton.addEventListener("click", () => { void close(); });
  overlay.addEventListener("click", (event) => { if (event.target === overlay) void close(); });
  dialog.addEventListener("keydown", (event) => { if (event.key === "Escape") void close(); });
  dialog.append(head, form);
  if (state.editorError || state.error) {
    const error = document.createElement("p");
    error.className = "memory-dialog-error";
    error.setAttribute("role", "alert");
    error.textContent = state.editorError || state.error;
    dialog.append(error);
  }
  dialog.append(footer);
  overlay.append(dialog);
  return overlay;
}

function createMemoryPreparingState() {
  const loading = document.createElement("div");
  loading.className = "memory-surface-state memory-preparing-state is-loading";
  loading.setAttribute("role", "status");
  loading.setAttribute("aria-live", "polite");
  loading.setAttribute("aria-busy", "true");
  loading.innerHTML = `
    <svg class="memory-thread-map" viewBox="0 0 220 112" aria-hidden="true" focusable="false">
      <g class="memory-thread-branch is-upper">
        <path class="memory-thread-line" pathLength="1" d="M 12 18 H 58 L 96 56 H 110"></path>
        <path class="memory-thread-flow" pathLength="1" d="M 12 18 H 58 L 96 56 H 110"></path>
        <circle class="memory-thread-origin" cx="12" cy="18" r="4"></circle>
      </g>
      <g class="memory-thread-branch is-middle">
        <path class="memory-thread-line" pathLength="1" d="M 12 56 H 110"></path>
        <path class="memory-thread-flow" pathLength="1" d="M 12 56 H 110"></path>
        <circle class="memory-thread-origin" cx="12" cy="56" r="4"></circle>
      </g>
      <g class="memory-thread-branch is-lower">
        <path class="memory-thread-line" pathLength="1" d="M 12 94 H 58 L 96 56 H 110"></path>
        <path class="memory-thread-flow" pathLength="1" d="M 12 94 H 58 L 96 56 H 110"></path>
        <circle class="memory-thread-origin" cx="12" cy="94" r="4"></circle>
      </g>
      <circle class="memory-thread-core-halo" cx="110" cy="56" r="14"></circle>
      <circle class="memory-thread-core" cx="110" cy="56" r="5"></circle>
      <text class="memory-thread-star" x="110" y="60" text-anchor="middle">✦</text>
    </svg>
    <strong>正在准备长期记忆</strong>
  `;
  return loading;
}

function memoryActivityBlocksCollection(activity) {
  return ["working", "warning", "error", "disabled", "failed"].includes(activity?.state);
}

function memoryActivityNeedsNotice(activity) {
  return ["warning", "error", "disabled", "failed"].includes(activity?.state);
}

function createMemoryActivityNotice(plugin, activity) {
  const pluginFailure = activity.state === "failed" ? pluginStatusCopy(plugin) : null;
  const notice = document.createElement("div");
  notice.className = `memory-surface-state memory-activity-notice is-${activity.state}`;
  notice.setAttribute("role", ["error", "failed"].includes(activity.state) ? "alert" : "status");
  if (activity.state === "warning") notice.setAttribute("aria-live", "polite");
  const heading = document.createElement("strong");
  heading.textContent = pluginFailure?.label || activity.label || (
    activity.state === "warning" ? "长期记忆功能受限" : "长期记忆暂不可用"
  );
  const message = document.createElement("p");
  message.textContent = activity.message || pluginFailure?.message || (
    activity.state === "warning"
      ? "长期记忆当前受限；普通聊天仍可继续。"
      : "长期记忆当前不可用；普通聊天仍可继续。"
  );
  const actions = document.createElement("div");
  const link = document.createElement("button");
  link.type = "button";
  link.className = "secondary-button";
  link.textContent = "前往插件页";
  link.addEventListener("click", () => {
    pluginState.selectedId = plugin.id;
    showPage("plugins");
    renderPluginPage();
  });
  actions.append(link);
  notice.append(heading, message, actions);
  return notice;
}

function renderMemoryPreparingArchive() {
  const archive = document.createElement("section");
  archive.className = "memory-archive is-preparing";
  const head = document.createElement("header");
  head.className = "memory-archive-head";
  const titleGroup = document.createElement("div");
  const title = document.createElement("h3");
  title.textContent = "记忆条目";
  titleGroup.append(title);
  const headActions = document.createElement("div");
  headActions.className = "memory-archive-head-actions";
  const count = document.createElement("span");
  count.className = "memory-result-count";
  count.textContent = "正在初始化";
  const add = document.createElement("button");
  add.type = "button";
  add.className = "memory-add-button";
  add.textContent = "＋ 新增记忆";
  add.disabled = true;
  headActions.append(count, add);
  head.append(titleGroup, headActions);

  const toolbar = document.createElement("div");
  toolbar.className = "memory-archive-toolbar";
  const search = document.createElement("input");
  search.type = "search";
  search.className = "memory-search-input";
  search.setAttribute("aria-label", "搜索记忆");
  search.placeholder = "搜索内容、分类或来源";
  search.disabled = true;
  const layer = document.createElement("select");
  layer.setAttribute("aria-label", "分层");
  layer.disabled = true;
  const allLayers = document.createElement("option");
  allLayers.textContent = "全部分层";
  layer.append(allLayers);
  const refresh = document.createElement("button");
  refresh.type = "button";
  refresh.className = "memory-refresh-button";
  refresh.textContent = "刷新";
  refresh.disabled = true;
  toolbar.append(search, layer, refresh);
  window.setTimeout(() => enhanceSelect(layer), 0);

  const body = document.createElement("div");
  body.className = "memory-archive-list is-preparing";
  body.append(createMemoryPreparingState());
  archive.append(head, toolbar, body);
  return archive;
}

function renderMemoryCollection(plugin, section, collection) {
  const state = pluginCollectionRuntimeState(plugin, section, collection);
  const activity = projectPluginActivity(plugin);
  const initializing = activity.state === "working";
  const activityUnavailable = memoryActivityNeedsNotice(activity);
  const activityControlsDisabled = initializing || activityUnavailable;
  const motion = state.motion;
  const archive = document.createElement("section");
  archive.className = "memory-archive";
  archive.classList.toggle("is-preparing", initializing);

  const head = document.createElement("header");
  head.className = "memory-archive-head";
  const titleGroup = document.createElement("div");
  const title = document.createElement("h3");
  title.textContent = collection.title || section.title;
  titleGroup.append(title);
  const headActions = document.createElement("div");
  headActions.className = "memory-archive-head-actions";
  const count = document.createElement("span");
  count.className = "memory-result-count";
  count.textContent = initializing
    ? "正在初始化"
    : activityUnavailable
      ? activity.label || "暂不可用"
      : state.loaded ? `${state.total ?? state.items.length} 条记忆` : "正在读取";
  const add = document.createElement("button");
  add.type = "button";
  add.className = "memory-add-button";
  add.textContent = "＋ 新增记忆";
  add.disabled = activityControlsDisabled || state.loading || !collection.can_create;
  add.addEventListener("click", () => openMemoryCollectionEditor(plugin, section, collection));
  headActions.append(count, add);
  head.append(titleGroup, headActions);

  const toolbar = document.createElement("div");
  toolbar.className = "memory-archive-toolbar";
  const search = document.createElement("input");
  search.type = "search";
  search.className = "memory-search-input";
  search.dataset.collectionKey = pluginCollectionKey(plugin, section, collection);
  search.setAttribute("aria-label", "搜索记忆");
  search.placeholder = "搜索内容、分类或来源";
  search.value = state.search;
  search.disabled = activityControlsDisabled;
  search.addEventListener("input", () => {
    state.search = search.value.trim();
    state.queryRevision += 1;
    window.clearTimeout(state.searchTimer);
    state.searchTimer = window.setTimeout(() => queryPluginCollection(plugin, section, collection), 220);
  });
  toolbar.append(search);
  (collection.filters || []).forEach((filter) => {
    const select = document.createElement("select");
    select.setAttribute("aria-label", filter.label);
    const all = document.createElement("option");
    all.value = "";
    all.textContent = `全部${filter.label}`;
    select.append(all);
    filter.options.forEach((option) => {
      const item = document.createElement("option");
      item.value = String(option.value);
      item.textContent = option.label;
      select.append(item);
    });
    select.value = Object.hasOwn(state.filters, filter.key) ? String(state.filters[filter.key]) : "";
    select.disabled = activityControlsDisabled;
    select.addEventListener("change", () => {
      const option = filter.options.find((item) => String(item.value) === select.value);
      if (option) state.filters[filter.key] = option.value;
      else delete state.filters[filter.key];
      state.queryRevision += 1;
      queryPluginCollection(plugin, section, collection);
    });
    toolbar.append(select);
    window.setTimeout(() => enhanceSelect(select), 0);
  });
  const refresh = document.createElement("button");
  refresh.type = "button";
  refresh.className = "memory-refresh-button";
  refresh.textContent = state.loading ? "刷新中…" : "刷新";
  refresh.disabled = activityControlsDisabled || state.loading;
  refresh.addEventListener("click", () => queryPluginCollection(plugin, section, collection));
  toolbar.append(refresh);

  const body = document.createElement("div");
  body.className = "memory-archive-list";
  if (initializing) {
    body.classList.add("is-preparing");
    body.append(createMemoryPreparingState());
  } else if (activityUnavailable) {
    body.classList.add("has-activity-notice");
    body.append(createMemoryActivityNotice(plugin, activity));
  } else if (state.error && !state.editor) {
    const error = document.createElement("p");
    error.className = "memory-surface-error";
    error.textContent = state.error;
    body.append(error);
  }
  if (!initializing && !activityUnavailable && state.loading && !state.loaded) {
    const loading = document.createElement("div");
    loading.className = "memory-surface-state is-loading";
    loading.innerHTML = '<span class="memory-state-orbit" aria-hidden="true"></span><strong>正在整理记忆档案</strong><p>插件准备完成后，内容会自动出现在这里。</p>';
    body.append(loading);
  } else if (!initializing && !activityUnavailable && state.loaded && !state.items.length) {
    const empty = document.createElement("div");
    empty.className = "memory-surface-state";
    const mark = document.createElement("span");
    mark.className = "memory-empty-mark";
    mark.textContent = "✦";
    const heading = document.createElement("strong");
    heading.textContent = state.search || Object.keys(state.filters).length ? "没有匹配的记忆" : "还没有长期记忆";
    const hint = document.createElement("p");
    hint.textContent = state.search || Object.keys(state.filters).length
      ? "换一个关键词或清除筛选后再试。"
      : "新增一条值得 Sakura 在未来对话中记住的内容。";
    empty.append(mark, heading, hint);
    body.append(empty);
  } else if (!initializing && !activityUnavailable) {
    state.items.forEach((item) => {
      const values = item.values || {};
      const card = document.createElement("article");
      card.className = "memory-record-card";
      card.dataset.itemId = item.itemId;
      card.tabIndex = 0;
      card.classList.toggle("is-selected", state.selectedItemId === item.itemId);
      if (motion?.itemId === item.itemId) {
        card.classList.add(motion.kind === "create" ? "is-entering" : "is-updated");
      }
      card.setAttribute("aria-label", `记忆：${String(values.content || "空内容").slice(0, 80)}`);
      card.addEventListener("click", () => {
        state.selectedItemId = item.itemId;
        fields.memorySurface.querySelectorAll(".memory-record-card.is-selected")
          .forEach((element) => element.classList.remove("is-selected"));
        card.classList.add("is-selected");
      });
      card.addEventListener("dblclick", () => openMemoryCollectionEditor(plugin, section, collection, item));
      card.addEventListener("keydown", (event) => {
        if (event.key === "Enter") openMemoryCollectionEditor(plugin, section, collection, item);
      });
      const main = document.createElement("div");
      main.className = "memory-record-main";
      const content = document.createElement("p");
      content.className = "memory-record-content";
      content.textContent = String(values.content || "（空记忆）");
      const meta = document.createElement("div");
      meta.className = "memory-record-meta";
      [
        memoryCollectionOptionLabel(collection, "layer", values.layer),
        values.category || "未分类",
        values.source || "未知来源",
        formatMemoryTimestamp(values.updatedAt),
      ].filter(Boolean).forEach((text, index) => {
        const itemMeta = document.createElement("span");
        itemMeta.className = index === 0 ? "memory-layer-chip" : "";
        itemMeta.textContent = text;
        meta.append(itemMeta);
      });
      main.append(content, meta);
      const aside = document.createElement("div");
      aside.className = "memory-record-aside";
      const scores = document.createElement("div");
      scores.className = "memory-score-row";
      [["重要", values.importance], ["置信", values.confidence]].forEach(([label, value]) => {
        const score = document.createElement("span");
        score.textContent = `${label} ${Math.round(Number(value ?? 0) * 100)}`;
        scores.append(score);
      });
      const edit = document.createElement("button");
      edit.type = "button";
      edit.className = "memory-card-edit";
      edit.textContent = "编辑";
      edit.addEventListener("click", (event) => {
        event.stopPropagation();
        openMemoryCollectionEditor(plugin, section, collection, item);
      });
      aside.append(scores, edit);
      card.append(main, aside);
      body.append(card);
    });
  }
  state.motion = null;
  if (!activityControlsDisabled && state.nextCursor) {
    const more = document.createElement("button");
    more.type = "button";
    more.className = "secondary-button memory-load-more";
    more.textContent = state.loading ? "加载中…" : "加载更多";
    more.disabled = state.loading;
    more.addEventListener("click", () => queryPluginCollection(plugin, section, collection, { append: true }));
    body.append(more);
  }
  archive.append(head, toolbar, body);
  // 编辑器属于整个设置窗口，而不是记忆页。挂到 body 可避开页面切换动画建立的
  // containing block，确保 fixed 遮罩覆盖导航、内容和底栏。
  if (!activityControlsDisabled && state.editor) {
    mountMemoryEditorPortal(renderMemoryEditor(plugin, section, collection, state));
    syncMemoryEditorPortalState(state);
  }
  if (!activityControlsDisabled && !state.loaded && !state.loading && !state.error) {
    window.setTimeout(() => queryPluginCollection(plugin, section, collection), 0);
  }
  return archive;
}

function renderMemorySurface() {
  if (!fields.memorySurface) return;
  clearMemoryEditorPortal();
  fields.memorySurface.textContent = "";
  if (memoryState.rebinding || characterSwitching) {
    const switching = document.createElement("div");
    switching.className = "memory-surface-state";
    switching.setAttribute("role", "status");
    switching.textContent = "正在切换角色，记忆将在新角色就绪后重新加载。";
    fields.memorySurface.append(switching);
    return;
  }
  const contributions = [];
  (request?.plugins?.items || []).forEach((plugin) => {
    pluginSettingsSections(plugin)
      .filter((section) => section.surface === "memory")
      .forEach((section) => contributions.push({ plugin, section }));
  });
  if (!contributions.length) {
    if (memorySurfaceIsTransitioning()) {
      fields.memorySurface.append(renderMemoryPreparingArchive());
      schedulePluginActivityRefresh();
      return;
    }
    const empty = document.createElement("div");
    empty.className = "memory-surface-state memory-surface-unavailable";
    const mark = document.createElement("span");
    mark.className = "memory-empty-mark";
    mark.textContent = "✦";
    const heading = document.createElement("strong");
    heading.textContent = "记忆管理暂不可用";
    const message = document.createElement("p");
    message.textContent = "请确认记忆插件已安装并启用。";
    const actions = document.createElement("div");
    const refresh = document.createElement("button");
    refresh.type = "button";
    refresh.className = "secondary-button";
    refresh.textContent = "重新检查";
    refresh.disabled = pluginActivityRefreshInFlight;
    refresh.addEventListener("click", refreshPluginActivityCurrent);
    const link = document.createElement("button");
    link.type = "button";
    link.className = "secondary-button";
    link.textContent = "前往插件页";
    link.addEventListener("click", () => showPage("plugins"));
    actions.append(refresh, link);
    empty.append(mark, heading, message, actions);
    fields.memorySurface.append(empty);
    schedulePluginActivityRefresh();
    return;
  }
  contributions.forEach(({ plugin, section }) => {
    (section.collections || []).forEach((collection) => {
      fields.memorySurface.append(renderMemoryCollection(plugin, section, collection));
    });
  });
  schedulePluginActivityRefresh();
}

async function runPluginSettingsAction(plugin, section, action, focusResourceKey = "") {
  if (pluginState.managementBusy) return;
  const restoreAboutResourceKey = focusResourceKey
    || document.activeElement?.dataset?.aboutResourceKey
    || "";
  const busyKey = `${plugin.id}:${section.section_id}:${action.action_id}`;
  pluginState.actionBusyKey = busyKey;
  renderPluginPage();
  renderAboutComponents();
  setError("");
  try {
    const result = runtimePluginController
      ? await runtimePluginController.action({
        pluginId: plugin.plugin_id,
        sectionId: section.section_id,
        actionId: action.action_id,
        values: clonePlain(editablePluginSectionValues(
          section,
          pluginSectionValues(plugin.id, section.section_id),
        )),
      })
      : await hostCall("plugin.settings_action", {
        plugin_id: plugin.plugin_id,
        section_id: section.section_id,
        action_id: action.action_id,
        values: clonePlain(editablePluginSectionValues(
          section,
          pluginSectionValues(plugin.id, section.section_id),
        )),
      });
    if (result && typeof result.values === "object" && result.values !== null) {
      pluginState.settingsValues[plugin.id][section.section_id] = {
        ...pluginState.settingsValues[plugin.id][section.section_id],
        ...result.values,
      };
      refreshDirty();
    }
    if (result && result.message) {
      notify(String(result.message), "success");
    }
    if (runtimePluginController) {
      await runtimePluginController.refreshCurrent();
    }
  } catch (error) {
    setError(String(error));
  } finally {
    pluginState.actionBusyKey = "";
    renderPluginPage();
    renderAboutComponents({ restoreResourceKey: restoreAboutResourceKey });
  }
}

function renderPluginDetail() {
  const plugin = (request.plugins?.items || []).find((item) => item.id === pluginState.selectedId);
  fields.pluginDetail.textContent = "";
  if (!plugin) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "选择一个插件查看详情。";
    fields.pluginDetail.append(empty);
    return;
  }
  const heading = document.createElement("div");
  heading.className = "plugin-detail-heading";
  const title = document.createElement("h2");
  title.textContent = plugin.name || plugin.id;
  heading.append(title);
  const desc = document.createElement("p");
  desc.className = "detail-desc";
  desc.textContent = plugin.description || "暂无说明。";
  const meta = document.createElement("dl");
  meta.className = "detail-meta";
  const status = pluginStatusCopy(plugin);
  const exceptionalStatus = pluginHasExceptionalStatus(plugin);
  if (exceptionalStatus || pluginStatusIsStarting(plugin)) {
    const statusBadge = document.createElement("span");
    statusBadge.className = exceptionalStatus
      ? "plugin-state-badge is-error"
      : "plugin-state-badge";
    statusBadge.textContent = status.label;
    heading.append(statusBadge);
  }
  const metaRows = [
    ["插件 ID", plugin.plugin_id || "清单无有效 ID"],
    ["版本", plugin.version || "0.0.0"],
    ["作者", plugin.author || "未知"],
    ["来源", plugin.source === "user" ? "自行安装" : "Sakura 内置"],
  ];
  metaRows.forEach(([label, value]) => {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = value;
    meta.append(dt, dd);
  });

  fields.pluginDetail.append(heading, desc, meta);
  if (plugin.required) {
    const requiredNote = document.createElement("p");
    requiredNote.className = "plugin-required-note";
    requiredNote.textContent = "Sakura 运行需要这个插件，因此不能关闭。";
    fields.pluginDetail.append(requiredNote);
  }
  if (exceptionalStatus) {
    const notice = document.createElement("div");
    notice.className = "plugin-health-notice";
    if (status.message) {
      const message = document.createElement("p");
      message.textContent = status.message;
      notice.append(message);
    }
    const diagnostic = document.createElement("p");
    diagnostic.className = "plugin-health-notice__diagnostic";
    diagnostic.textContent = status.diagnostic;
    if (status.diagnostic) notice.append(diagnostic);
    fields.pluginDetail.append(notice);
  }
  fields.pluginDetail.append(renderPluginSettings(plugin));
  if (plugin.can_uninstall) {
    const actions = document.createElement("div");
    actions.className = "detail-actions";
    const uninstall = document.createElement("button");
    uninstall.type = "button";
    uninstall.className = "danger-button";
    uninstall.textContent = pluginState.managementBusy ? "卸载中…" : "卸载插件";
    uninstall.disabled = pluginState.managementBusy;
    uninstall.addEventListener("click", () => uninstallLocalPlugin(plugin));
    actions.append(uninstall);
    fields.pluginDetail.append(actions);
  }
}

function renderPluginPage() {
  fields.pluginInstallMenuButton.disabled = pluginState.managementBusy || !runtimePluginController;
  fields.pluginInstallZipButton.disabled = pluginState.managementBusy || !runtimePluginController;
  fields.pluginInstallFolderButton.disabled = pluginState.managementBusy || !runtimePluginController;
  if (fields.pluginInstallMenuButton.disabled) setPluginInstallMenuOpen(false);
  renderPluginList();
  renderPluginDetail();
  schedulePluginActivityRefresh();
}

async function installLocalPlugin(sourceKind) {
  if (!runtimePluginController || pluginState.managementBusy) return;
  pluginState.managementBusy = true;
  setError("");
  renderPluginPage();
  try {
    const result = await runtimePluginController.install(sourceKind);
    if (!result) return;
    pluginState.selectedId = result.installId;
    notify("安装完成。打开开关并应用后即可使用。", "success");
  } catch (error) {
    setError(String(error));
  } finally {
    pluginState.managementBusy = false;
    renderPluginPage();
  }
}

async function uninstallLocalPlugin(plugin) {
  if (!runtimePluginController || pluginState.managementBusy || !plugin?.can_uninstall) return;
  const confirmed = await confirmAction(
    `卸载“${plugin.name || plugin.id}”？插件设置和数据会保留。`,
    { title: "卸载插件", confirmText: "卸载", cancelText: "取消", danger: true },
  );
  if (!confirmed) return;
  pluginState.managementBusy = true;
  setError("");
  renderPluginPage();
  try {
    await runtimePluginController.uninstall(plugin.install_id);
    notify("插件已卸载，设置和数据已保留。", "success");
  } catch (error) {
    setError(String(error));
  } finally {
    pluginState.managementBusy = false;
    renderPluginPage();
  }
}

function editablePluginSectionValues(section, values) {
  return Object.fromEntries((section.fields || [])
    .filter((field) => pluginFieldEditable(field) && Object.hasOwn(values, field.key))
    .map((field) => [field.key, values[field.key]]));
}

function collectPluginSettings() {
  const enabledById = {};
  const settingsById = {};
  (request.plugins?.items || []).forEach((plugin) => {
    const enabled = plugin.required ? true : Boolean(pluginState.enabledById[plugin.id]);
    if (enabled !== pluginState.initialEnabledById[plugin.id]) {
      if (plugin.plugin_id) enabledById[plugin.plugin_id] = enabled;
    }
    const sections = pluginSettingsSections(plugin);
    if (sections.length) {
      sections.forEach((section) => {
        const values = clonePlain(editablePluginSectionValues(
          section,
          pluginSectionValues(plugin.id, section.section_id),
        ));
        const initial = editablePluginSectionValues(
          section,
          pluginState.initialSettingsValues[plugin.id]?.[section.section_id] || {},
        );
        if (!plainEqual(values, initial)) {
          if (!plugin.plugin_id) return;
          settingsById[plugin.plugin_id] = settingsById[plugin.plugin_id] || {};
          settingsById[plugin.plugin_id][section.section_id] = values;
        }
      });
    }
  });
  return { enabled_by_id: enabledById, settings_by_id: settingsById };
}

function runtimePluginDraft() {
  const legacy = collectPluginSettings();
  return { enabledById: legacy.enabled_by_id, settingsById: legacy.settings_by_id };
}

function applyRuntimePluginSnapshot(snapshot, { preserveDraft = false, draft = null } = {}) {
  request = request || {};
  request.plugins = {
    permission_labels: request.plugins?.permission_labels || {},
    items: snapshot.plugins.map((plugin) => ({
      id: plugin.installId,
      install_id: plugin.installId,
      plugin_id: plugin.pluginId,
      name: plugin.name,
      version: plugin.version,
      author: plugin.author,
      description: plugin.description,
      enabled: plugin.enabled,
      required: plugin.required,
      supported: plugin.supported,
      source: plugin.source,
      can_uninstall: plugin.canUninstall,
      provides: clonePlain(plugin.provides),
      requires: clonePlain(plugin.requires),
      missing_services: clonePlain(plugin.missingServices),
      state: plugin.state,
      reason_code: plugin.reasonCode,
      settings: plugin.sections.map((section) => ({
        section_id: section.sectionId,
        title: section.title,
        surface: section.surface,
        reason_code: section.reasonCode,
        fields: (section.fields || []).map((field) => ({
          ...field,
          restart_required: field.restartRequired,
        })),
        values: clonePlain(section.values),
        actions: (section.actions || []).map((action) => ({
          action_id: action.actionId,
          label: action.label,
          description: action.description,
          danger: action.danger,
        })),
        collections: (section.collections || []).map((collection) => ({
          collection_id: collection.collectionId,
          title: collection.title,
          description: collection.description,
          columns: clonePlain(collection.columns),
          fields: (collection.fields || []).map((field) => ({
            ...field,
            restart_required: field.restartRequired,
          })),
          filters: clonePlain(collection.filters),
          searchable: collection.searchable,
          page_size: collection.pageSize,
          can_create: collection.canCreate,
          can_update: collection.canUpdate,
          can_delete: collection.canDelete,
          delete_confirmation: collection.deleteConfirmation,
        })),
      })),
    })),
  };
  const previousCollections = new Map(pluginCollectionState);
  previousCollections.forEach((state) => window.clearTimeout(state.searchTimer));
  pluginCollectionState.clear();
  if (preserveDraft) {
    for (const plugin of request.plugins.items) {
      for (const section of plugin.settings) {
        for (const collection of section.collections) {
          const key = pluginCollectionKey(plugin, section, collection);
          const previous = previousCollections.get(key);
          if (!previous) continue;
          // A new state object detaches old queries while keeping the editor and filters.
          const current = pluginCollectionRuntimeState(plugin, section, collection);
          current.editor = previous.editor ? clonePlain(previous.editor) : null;
          current.editorError = previous.editorError;
          current.selectedItemId = previous.selectedItemId;
          current.search = previous.search;
          current.filters = clonePlain(previous.filters);
        }
      }
    }
  }
  initializePluginState();
  if (preserveDraft && draft) {
    Object.entries(draft.enabledById || {}).forEach(([id, enabled]) => {
      const plugin = request.plugins.items.find((item) => item.plugin_id === id);
      if (plugin && Object.hasOwn(pluginState.enabledById, plugin.id)) {
        pluginState.enabledById[plugin.id] = Boolean(enabled);
      }
    });
    Object.entries(draft.settingsById || {}).forEach(([id, sections]) => {
      const plugin = request.plugins.items.find((item) => item.plugin_id === id);
      if (!plugin || !pluginState.settingsValues[plugin.id]) return;
      Object.entries(sections || {}).forEach(([sectionId, values]) => {
        if (pluginState.settingsValues[plugin.id][sectionId]) {
          pluginState.settingsValues[plugin.id][sectionId] = clonePlain(values);
        }
      });
    });
  }
  renderPluginPage();
  renderMemorySurface();
  renderAboutComponents();
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
  if (input) {
    const min = Number(input.min || 0);
    const max = Number(input.max || 100);
    const value = Number(input.value);
    const progress = max > min ? ((value - min) / (max - min)) * 100 : 0;
    input.style.setProperty("--slider-progress", `${Math.max(0, Math.min(100, progress))}%`);
  }
}

let layoutPreviewPending = false;
function requestLayoutPreview() {
  if (!request || runtimeSettingsHost || layoutPreviewPending) {
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

let fontPreviewPending = false;
function requestFontPreview() {
  if (!request || runtimeSettingsHost || fontPreviewPending) {
    return;
  }
  fontPreviewPending = true;
  requestAnimationFrame(async () => {
    fontPreviewPending = false;
    try {
      await invoke("preview_layout", {
        layout: {
          speech_font_size: clampInt(
            fields.speechFontSize.value,
            request.limits.speech_font_size,
          ),
          name_font_size: clampInt(
            fields.nameFontSize.value,
            request.limits.name_font_size,
          ),
          input_font_size: clampInt(
            fields.inputFontSize.value,
            request.limits.input_font_size,
          ),
        },
      });
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
    screen_context_resolution: fields.screenResolution.value || "fullscreen",
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

function validateOnboardingBeforeSubmit() {
  if (runtimeSettingsHost) {
    return true;
  }
  if (!isOnboarding()) {
    return true;
  }
  if (!selectedCharacter()) {
    showOnboardingStep("character");
    setError("请先导入并选择一个角色包。");
    return false;
  }
  const profile = onboardingChatProfile();
  if (profile && !profile.api_key) {
    focusProviderValidation(profile, "api_key");
    onboardingStep = "providers";
    updateOnboardingUi();
    setError(`供应商「${providerDisplayName(profile)}」缺少 API Key。`);
    return false;
  }
  return true;
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
  if (missingModels && !runtimeSettingsHost) {
    focusProviderValidation(missingModels, "");
    setError(`供应商「${providerDisplayName(missingModels)}」至少需要一个模型。`);
    return false;
  }
  const missingCredential = providerState.profiles.find((profile) => (
    !profile.api_key?.trim()
    && !(profile.configured && profile.credential_action === "keep")
  ));
  if (missingCredential && !runtimeSettingsHost) {
    focusProviderValidation(missingCredential, "api_key");
    setError(`供应商「${providerDisplayName(missingCredential)}」缺少 API Key。`);
    return false;
  }
  const selection = collectModelSelection();
  if (runtimeSettingsHost) {
    const issue = findProviderModelSelectionIssue({
      providers: profiles,
      modelSlots: selection.slots,
      slotFields: request.api.slot_fields,
    });
    if (!issue) {
      return true;
    }
    showPage("model");
    refreshModelSlots();
    if (issue.type === "incomplete") {
      setError(`${issue.label}必须同时选择供应商和模型。`);
    } else if (issue.type === "required") {
      setError(`请选择可用的${issue.label}。`);
    } else {
      setError(`${issue.label}引用的供应商或模型已不可用，请重新选择。`);
    }
    return false;
  }
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

function runtimeCredential(profile) {
  const value = (profile.api_key || "").trim();
  let action = profile.credential_action || (profile.configured ? "keep" : "clear");
  if (value) action = "replace";
  if (action === "keep" && !profile.configured) action = "clear";
  return { action, value: action === "replace" ? value : "" };
}

function runtimeProbeProfile(profile, model) {
  return {
    profile_id: profile.id,
    base_url: (profile.base_url || "").trim(),
    model: String(model || "").trim(),
    timeout_seconds: clampInt(fields.apiTimeout.value || 15, [1, 60]),
    credential: runtimeCredential(profile),
  };
}

function collectRuntimeProviderModelDraft() {
  const api = collectApiSettings();
  const selection = collectModelSelection().slots;
  return {
    providers: providerState.profiles.map((profile) => ({
      id: profile.id,
      alias: (profile.alias || "").trim() || profile.id,
      base_url: (profile.base_url || "").trim(),
      models: (profile.models || []).map((model) => String(model).trim()).filter(Boolean),
      credential: runtimeCredential(profile),
    })),
    model_slots: selection,
    settings: api.settings,
  };
}

function applyRuntimeProviderModelSnapshot(snapshot) {
  request = request || {};
  request.limits = {
    ...(request.limits || {}),
    api_timeout_seconds: [1, 300],
    api_temperature: [0, 2],
    api_top_p: [0, 1],
    api_max_tokens: [1, 1000000],
  };
  request.api = {
    profiles: snapshot.providers.map((profile) => ({
      ...profile,
      api_key: "",
      credential_action: profile.configured ? "keep" : "clear",
    })),
    settings: snapshot.settings,
    slot_fields: snapshot.model_slots.map((slot) => ({
      id: slot.identity,
      label: slot.label,
      description: slot.description,
      required: slot.required,
      allow_inherit: slot.identity !== "core:chat" && !slot.required,
      owner_type: slot.ownerType,
      owner_id: slot.ownerId,
      reason_code: slot.reasonCode,
    })),
    model_selection: {
      slots: Object.fromEntries(snapshot.model_slots.map((slot) => [slot.identity, slot.selection])),
    },
  };
  initializeProviderState();
  renderProviderPage();
  renderModelSlots(request.api.model_selection);
  setNumericBounds(fields.contextWindowTokens, [4_096, 2_000_000]);
  setNumericBounds(fields.apiTimeout, request.limits.api_timeout_seconds);
  setNumericBounds(fields.apiMaxTokens, request.limits.api_max_tokens);
  fields.apiTimeout.value = snapshot.settings.timeout_seconds;
  fields.apiTemperature.value = snapshot.settings.temperature ?? 0.8;
  fields.apiTopPEnabled.checked = snapshot.settings.top_p !== null;
  fields.apiTopP.value = snapshot.settings.top_p ?? 1;
  fields.apiMaxTokensEnabled.checked = snapshot.settings.max_tokens !== null;
  fields.apiMaxTokens.value = snapshot.settings.max_tokens ?? 2048;
  syncApiAdvancedState();
}

async function refreshRuntimeVoiceCurrent() {
  if (!runtimeVoiceController) return;
  await runtimeVoiceController.refreshCurrent({ preserveDraft: true });
}

async function saveRuntimeSettings() {
  if (
    memoryState.editorDrafts.size > 0
    || countCharacterScopedCollectionDrafts(pluginCollectionState.values()) > 0
  ) {
    throw new Error("请先使用“保存记忆”提交当前记忆草稿，或还原草稿后再关闭设置。");
  }
  if (runtimeAppearanceController?.isDirty()) await runtimeAppearanceController.save();
  let result = null;
  if (runtimeScreenAwarenessController?.isDirty()) {
    result = await runtimeScreenAwarenessController.save();
  }
  if (runtimeProviderModelController?.isDirty()) {
    if (!validateApiSettingsBeforeSubmit()) throw new Error("供应商或模型设置未通过校验。");
    result = await runtimeProviderModelController.save();
    providerState.profiles.forEach((profile) => {
      profile.configured = runtimeCredential(profile).action !== "clear";
      profile.api_key = "";
      profile.credential_action = profile.configured ? "keep" : "clear";
    });
    renderProviderPage();
    runtimeProviderModelController.rebase();
    await runtimeToolsController?.refreshCurrent();
    await runtimePluginController?.refreshCurrent();
    await runtimeMemoryController?.refreshCurrent();
    await refreshRuntimeVoiceCurrent();
  }
  if (runtimeChatTimingController?.isDirty()) {
    result = await runtimeChatTimingController.save();
  }
  if (runtimeBubbleAutoHideController?.isDirty()) {
    result = await runtimeBubbleAutoHideController.save();
  }
  if (runtimeAutostartController?.isDirty()) {
    result = await runtimeAutostartController.save();
  }
  if (runtimeToolsController?.isDirty()) {
    result = await runtimeToolsController.save();
    await runtimePluginController?.refreshCurrent();
    await runtimeMemoryController?.refreshCurrent();
    await runtimeProviderModelController?.refreshCurrent();
    await refreshRuntimeVoiceCurrent();
  }
  if (runtimePluginController?.isDirty()) {
    result = await runtimePluginController.save();
    await runtimeToolsController?.refreshCurrent();
    await runtimeMemoryController?.refreshCurrent();
    await runtimeProviderModelController?.refreshCurrent();
    await refreshRuntimeVoiceCurrent();
  }
  if (runtimeMemoryController?.isDirty()) {
    result = await runtimeMemoryController.save();
    await loadMemories();
  }
  if (runtimeVoiceController?.isDirty()) {
    result = await runtimeVoiceController.save();
    await runtimeToolsController?.refreshCurrent();
    await runtimePluginController?.refreshCurrent();
    await runtimeMemoryController?.refreshCurrent();
    await runtimeProviderModelController?.refreshCurrent();
  }
  const characterResult = await commitCharacterSelection({
    committedCharacterId: runtimeCharacterSnapshot?.currentCharacterId,
    selectedCharacterId: runtimeCharacterDraftId,
    readLifecycle: () => invoke("runtime_lifecycle_snapshot"),
    selectCharacter: (characterId) => rootSettingsClient.characterSelect(characterId),
    applyChange: applyRuntimeCharacterChange,
  });
  if (characterResult !== null) result = characterResult;
  return result;
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
  return {
    // Debug settings no longer have controls. Preserve their compatibility
    // payload without exposing them as product settings.
    debug_log: { ...request.system_basic.debug_log },
    ui: {
      subtitle_typing_interval_ms: clampInt(
        fields.subtitleTypingInterval.value,
        limits.subtitle_typing_interval_ms,
      ),
      reply_segment_pause_ms: clampInt(
        fields.replySegmentPause.value,
        limits.reply_segment_pause_ms,
      ),
      speech_font_size: clampInt(
        fields.speechFontSize.value,
        limits.speech_font_size,
      ),
      name_font_size: clampInt(
        fields.nameFontSize.value,
        limits.name_font_size,
      ),
      input_font_size: clampInt(
        fields.inputFontSize.value,
        limits.input_font_size,
      ),
      // Runtime v2 no longer exposes a text-size setting for the icon-only send control.
      // Preserve the legacy host value while the shared settings document still carries it.
      button_font_size: request.system_basic.ui.button_font_size,
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
      ...request.system_extra.startup,
      launch_at_login: fields.launchAtLogin.checked,
    },
    // Runtime v2 does not expose quick backchannel settings. Preserve the
    // compatibility payload so saving unrelated settings cannot reset it.
    backchannel: { ...request.system_extra.backchannel },
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

function upgradeSliderControls() {
  // 点击 .slider-value 可进入编辑模式，回车/失焦后切回显示并同步滑块。
  document.querySelectorAll(".slider-control").forEach((control) => {
    const output = control.querySelector(".slider-value");
    const slider = control.querySelector("input[type='range']");
    if (!output || !slider || output.dataset.upgraded) return;
    output.dataset.upgraded = "true";

    output.addEventListener("click", () => {
      if (slider.disabled) return;
      const min = Number(slider.min || 0);
      const max = Number(slider.max || 100);
      const editor = document.createElement("input");
      editor.type = "number";
      editor.className = "slider-value-editor";
      editor.min = String(min);
      editor.max = String(max);
      editor.step = slider.step || "1";
      editor.value = slider.value;
      editor.style.width = `${Math.max(40, output.offsetWidth)}px`;
      output.replaceWith(editor);
      editor.focus();
      editor.select();

      function commit() {
        const clamped = clampInt(editor.value, [Number(editor.min), Number(editor.max)]);
        const changed = String(clamped) !== slider.value;
        slider.value = String(clamped);
        if (changed) {
          slider.dispatchEvent(new Event("input", { bubbles: true }));
        }
        output.textContent = slider.value;
        editor.replaceWith(output);
      }

      editor.addEventListener("blur", commit);
      editor.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); commit(); }
        if (e.key === "Escape") { e.preventDefault(); output.textContent = slider.value; editor.replaceWith(output); }
      });
    });
  });
}

async function load() {
  request = await invoke("load_request");
  renderCharacters();
  renderThemeControls();
  initializeProviderState();
  renderProviderPage();
  renderModelSlots(request.api.model_selection);
  renderTtsProviders();
  renderMemoryControls();
  initializePluginState();
  enhanceSelect(fields.characterSelect);
  enhanceSelect(fields.visualEffectMode);
  enhanceSelect(fields.ttsProvider);
  enhanceSelect(fields.screenResolution);
  enhanceSelect(fields.memoryLayerFilter);
  enhanceSelect(fields.memorySort);
  enhanceSelect(fields.memoryLayer);

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
  setNumericBounds(fields.contextWindowTokens, [4_096, 2_000_000]);
  setNumericBounds(fields.apiTimeout, request.limits.api_timeout_seconds);
  setNumericBounds(fields.apiMaxTokens, request.limits.api_max_tokens);
  setNumericBounds(fields.ttsTimeout, request.limits.tts_timeout_seconds);
  setNumericBounds(fields.memoryTriggerTurns, request.limits.memory_trigger_turns);
  setNumericBounds(fields.speechFontSize, request.limits.speech_font_size);
  setNumericBounds(fields.nameFontSize, request.limits.name_font_size);
  setNumericBounds(fields.inputFontSize, request.limits.input_font_size);

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
  fields.screenResolution.value = settings.screen_context_resolution || "fullscreen";
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

  fields.subtitleTypingInterval.value = request.system_basic.ui.subtitle_typing_interval_ms;
  fields.replySegmentPause.value = request.system_basic.ui.reply_segment_pause_ms;
  fields.speechFontSize.value = request.system_basic.ui.speech_font_size;
  fields.nameFontSize.value = request.system_basic.ui.name_font_size;
  fields.inputFontSize.value = request.system_basic.ui.input_font_size;
  updateSliderOutput("speechFontSize");
  updateSliderOutput("nameFontSize");
  updateSliderOutput("inputFontSize");
  fields.bubbleAutoHide.checked = request.system_basic.bubble.auto_hide_enabled;
  fields.bubbleAutoHideDelay.value = request.system_basic.bubble.auto_hide_delay_seconds;
  fields.launchAtLogin.checked = request.system_extra.startup.launch_at_login;
  fields.launchAtLogin.disabled = false;
  fields.memoryTriggerTurns.value = request.memory.curation.trigger_turns;

  setThemeValues(request.theme);
  themeChanged = false;
  updateScreenResolutionEstimate();
  syncEnabledState();
  syncRuntimeLoopState();
  syncBubbleState();
  syncApiAdvancedState();
  syncTtsState();
  syncCharacterArchiveState();
  refreshSelect(fields.characterSelect);
  refreshSelect(fields.ttsProvider);
  refreshSelect(fields.screenResolution);
  renderMemoryPage();
  renderPluginPage();
  initializeOnboarding();

  // 给所有滑块追加数字输入框，滑块粗调 + 数字精确输入。
  upgradeSliderControls();

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
["speechFontSize", "nameFontSize", "inputFontSize"].forEach((fieldKey) => {
  const preview = () => {
    updateSliderOutput(fieldKey);
    requestFontPreview();
  };
  fields[fieldKey].addEventListener("input", preview);
  fields[fieldKey].addEventListener("change", preview);
});
fields.characterSelect.addEventListener("change", syncTtsState);
fields.characterSelect.addEventListener("change", () => {
  if (runtimeSettingsHost) void stageRuntimeCharacterSelection();
  else applySelectedCharacterTheme();
});
fields.characterSelect.addEventListener("change", syncCharacterArchiveState);
fields.characterSelect.addEventListener("change", updateOnboardingUi);
fields.characterImportButton.addEventListener("click", importCharacterArchive);
fields.ttsVoiceImportButton.addEventListener("click", importCharacterVoiceArchive);
fields.characterExportButton.addEventListener("click", exportCharacterArchive);
fields.characterEditorButton.addEventListener("click", launchCharacterStudio);
fields.storageOpenUserRoot.addEventListener("click", () => {
  rootSettingsClient.storageOpenUserRoot().catch((error) => setError(String(error)));
});
fields.storageChooseTtsRoot.addEventListener("click", chooseTtsStorageRoot);
fields.storageResetTtsRoot.addEventListener("click", resetTtsStorageRoot);
fields.legacyRoleDataImportButton.addEventListener("click", importLegacyRoleData);
fields.aboutWebsiteButton.addEventListener("click", () => {
  rootSettingsClient.aboutOpenWebsite().catch((error) => setError(String(error)));
});
fields.aboutRepositoryButton.addEventListener("click", () => {
  rootSettingsClient.aboutOpenRepository().catch((error) => setError(String(error)));
});
fields.aboutChangelogButton.addEventListener("click", () => {
  rootSettingsClient.aboutOpenChangelog().catch((error) => setError(String(error)));
});
fields.aboutSponsorButton.addEventListener("click", () => {
  rootSettingsClient.aboutOpenSponsor().catch((error) => setError(String(error)));
});
fields.systemFirstRunGuideButton.addEventListener("click", () => {
  firstRunGuideController?.start({ persist: false });
});
fields.aboutComponentsRefresh?.addEventListener("click", () => {
  aboutComponentsReadError = "";
  void refreshPluginActivityCurrent();
});
fields.updateCheckButton.addEventListener("click", checkForUpdates);
fields.updateAutoCheck.addEventListener("change", saveUpdatePreferences);
fields.telemetryEnabled.addEventListener("change", setTelemetryEnabled);
fields.telemetryHelpButton.addEventListener("click", () => {
  rootSettingsClient.telemetryOpenDocumentation().catch((error) => setError(String(error)));
});
fields.telemetryCopyButton.addEventListener("click", async () => {
  const value = fields.telemetryInstallationId.textContent?.trim() || "";
  if (!/^[0-9a-f-]{36}$/.test(value)) return;
  try {
    await navigator.clipboard.writeText(value);
    notify("诊断 ID 已复制。", "success");
  } catch {
    setError("TELEMETRY_INSTALLATION_ID_COPY_FAILED");
  }
});
fields.telemetryRegenerateButton.addEventListener("click", regenerateTelemetryInstallationId);
fields.updateActionButton.addEventListener("click", runUpdateAction);
fields.enabled.addEventListener("change", syncEnabledState);
fields.screenResolution.addEventListener("change", updateScreenResolutionEstimate);
fields.toolCallsPerStep.addEventListener("input", syncRuntimeLoopState);
fields.addProviderButton.addEventListener("click", openAddProviderChooser);
fields.onboardingCharacterStep.addEventListener("click", () => showOnboardingStep("character"));
fields.onboardingProviderStep.addEventListener("click", () => showOnboardingStep("providers"));
fields.onboardingBackButton.addEventListener("click", () => showOnboardingStep("character"));
fields.providerSearch.addEventListener("input", () => {
  providerState.search = fields.providerSearch.value;
  renderProviderList();
});
fields.apiTopPEnabled.addEventListener("change", syncApiAdvancedState);
fields.apiMaxTokensEnabled.addEventListener("change", syncApiAdvancedState);
fields.ttsEnabled.addEventListener("change", () => {
  if (!runtimeSettingsHost) syncTtsState();
});
fields.ttsProvider.addEventListener("change", () => {
  if (!runtimeSettingsHost) handleTtsProviderChange();
});
fields.ttsTestButton.addEventListener("click", () => {
  if (!runtimeSettingsHost) testTtsSettings();
});
fields.visualEffectMode.addEventListener("change", markThemeChanged);
fields.visualEffectMode.addEventListener("runtime-value-applied", () => refreshSelect(fields.visualEffectMode));
fields.themeAiButton.addEventListener("click", generateAiTheme);
fields.resetThemeButton.addEventListener("click", () => {
  setThemeValues(selectedCharacterThemeDefaults(), { updateVisualEffect: false, animateTheme: true });
  themeChanged = true;
});
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
fields.memoryRefreshButton.addEventListener("click", () => loadMemories());
fields.memorySaveButton.addEventListener("click", saveMemoryEditor);
fields.memoryRevertButton.addEventListener("click", () => {
  memoryState.editorDrafts.delete(memoryState.selectedId);
  fillMemoryEditor(selectedMemory());
  refreshDirty();
});
fields.memoryDeleteButton.addEventListener("click", deleteSelectedMemory);
fields.memoryTriggerTurns.addEventListener("input", renderMemoryStatus);
fields.memoryContent.addEventListener("compositionstart", () => {
  memoryState.composing = true;
});
fields.memoryContent.addEventListener("compositionend", () => {
  memoryState.composing = false;
  captureMemoryEditorDraft();
});
[
  fields.memoryContent,
  fields.memoryLayer,
  fields.memoryCategory,
  fields.memorySource,
  fields.memoryImportance,
  fields.memoryConfidence,
].forEach((field) => {
  field.addEventListener("input", captureMemoryEditorDraft);
  field.addEventListener("change", captureMemoryEditorDraft);
});
fields.pluginSearch.addEventListener("input", renderPluginPage);
fields.pluginInstallMenuButton.addEventListener("click", () => {
  setPluginInstallMenuOpen(fields.pluginInstallMenu.hidden);
});
fields.pluginInstallMenuButton.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !fields.pluginInstallMenu.hidden) {
    event.preventDefault();
    setPluginInstallMenuOpen(false, { restoreFocus: true });
  } else if (["ArrowDown", "Enter", " "].includes(event.key) && fields.pluginInstallMenu.hidden) {
    event.preventDefault();
    setPluginInstallMenuOpen(true, { focusItem: true });
  }
});
fields.pluginInstallMenu.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    event.preventDefault();
    setPluginInstallMenuOpen(false, { restoreFocus: true });
  } else if (event.key === "ArrowDown") {
    event.preventDefault();
    movePluginInstallMenuFocus(1);
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    movePluginInstallMenuFocus(-1);
  } else if (event.key === "Home") {
    event.preventDefault();
    pluginInstallMenuItems()[0]?.focus();
  } else if (event.key === "End") {
    event.preventDefault();
    pluginInstallMenuItems().at(-1)?.focus();
  }
});
document.addEventListener("pointerdown", (event) => {
  if (!fields.pluginInstallMenu.hidden && !fields.pluginInstallMenuRoot.contains(event.target)) {
    setPluginInstallMenuOpen(false);
  }
});
document.addEventListener("focusin", (event) => {
  if (!fields.pluginInstallMenu.hidden && !fields.pluginInstallMenuRoot.contains(event.target)) {
    setPluginInstallMenuOpen(false);
  }
});
fields.pluginInstallZipButton.addEventListener("click", () => {
  setPluginInstallMenuOpen(false);
  installLocalPlugin("zip");
});
fields.pluginInstallFolderButton.addEventListener("click", () => {
  setPluginInstallMenuOpen(false);
  installLocalPlugin("folder");
});
fields.saveButton.addEventListener("click", async () => {
  if (runtimeSettingsHost) {
    if (characterSwitching) {
      setError("角色切换完成前不能保存设置。");
      return;
    }
    const original = fields.saveButton.textContent;
    setError("");
    setSubmissionBusy(true);
    fields.saveButton.textContent = "保存中…";
    try {
      await saveRuntimeSettings();
      notify("已保存。", "success");
      await closeSettingsWindow();
    } catch (error) {
      bypassCloseGuard = false;
      setError(String(error));
    } finally {
      setSubmissionBusy(false);
      fields.saveButton.textContent = original;
    }
    return;
  }
  if (!request) {
    return;
  }
  setError("");
  if (!validateOnboardingBeforeSubmit() || !validateApiSettingsBeforeSubmit()) {
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
  setSubmissionBusy(true);
  fields.saveButton.textContent = "保存中…";
  try {
    await invoke("save_settings", { settings });
    settingsBaseline = JSON.stringify(settings);
    refreshDirty();
    notify("已保存。", "success");
  } catch (error) {
    bypassCloseGuard = false;
    setSubmissionBusy(false);
    fields.saveButton.textContent = original;
    setError(String(error));
    return;
  }
  window.setTimeout(() => {
    bypassCloseGuard = false;
    setSubmissionBusy(false);
    fields.saveButton.textContent = original;
  }, 800);
});

fields.applyButton.addEventListener("click", async () => {
  if (runtimeSettingsHost) {
    if (characterSwitching) {
      setError("角色切换完成前不能应用设置。");
      return;
    }
    setError("");
    setSubmissionBusy(true);
    try {
      await saveRuntimeSettings();
      notify("已应用。", "success");
    } catch (error) {
      setError(String(error));
    } finally {
      setSubmissionBusy(false);
    }
    return;
  }
  if (!request) {
    return;
  }
  setError("");
  if (!validateOnboardingBeforeSubmit() || !validateApiSettingsBeforeSubmit()) {
    return;
  }
  let settings;
  try {
    settings = collectSettings();
  } catch (error) {
    setError(String(error));
    return;
  }
  setSubmissionBusy(true);
  try {
    await invoke("apply_settings", { settings });
    // 应用同样会持久化（仅不关窗），故重置基线，清掉「未保存」状态。
    settingsBaseline = JSON.stringify(settings);
    refreshDirty();
    notify("已应用。", "success");
  } catch (error) {
    setError(String(error));
  } finally {
    setSubmissionBusy(false);
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
    window.__TAURI__?.event?.listen?.("sakura://settings-exit-requested", requestAppExitClose);
    window.__TAURI__?.event?.listen?.("sakura://settings-exit-timeout", () => {
      notify("退出请求已取消：设置窗口未在 5 秒内响应。", "info");
    });
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

window.addEventListener("beforeunload", () => {
  beginSettingsWindowClose();
  clearPluginActivityRefresh();
  pluginCollectionState.forEach((state) => window.clearTimeout(state.searchTimer));
  runtimeAppearanceController?.dispose();
  runtimeProviderModelController?.dispose();
  runtimeChatTimingController?.dispose();
  runtimeBubbleAutoHideController?.dispose();
  runtimeMemoryController?.dispose();
  runtimeToolsController?.dispose();
  runtimePluginController?.dispose();
  runtimeVoiceController?.dispose();
  runtimeScreenAwarenessController?.dispose();
  runtimeAutostartController?.dispose();
  firstRunGuideController?.dispose();
  runtimeDiagnostics?.dispose({ settings: true });
}, { once: true });

async function initializeRuntimeSettingsSection(initialize) {
  try {
    await initialize();
  } catch (error) {
    if (!settingsWindowClosing) setError(String(error));
  }
}

async function startSettingsFrontend() {
  await runtimeDiagnosticsReady;
  pluginPresentation = await import("./plugin-presentation.js");
  let manifest;
  try {
    manifest = await invoke("settings_capability_manifest");
  } catch {
    await load();
    runtimeDiagnostics?.markReady({ settings: true });
    return;
  }
  runtimeSettingsHost = true;
  window.__TAURI__?.event?.listen?.("sakura://character-catalog-changed", ({ payload } = {}) => {
    if (settingsWindowClosing) return;
    void refreshRuntimeCharacterCatalog(payload);
  });
  const {
    applyCapabilityManifest,
    featureStatus,
    inputVisualEffectModes,
  } = await import("./capability-shell.js");
  manifest = applyCapabilityManifest(document, manifest);
  runtimeCapabilityManifest = manifest;
  runtimeVisualEffectModes = inputVisualEffectModes(manifest);
  if (featureStatus(manifest, "character.manage") === "available") {
    try {
      applyRuntimeCharacterSnapshot(await rootSettingsClient.charactersGet());
    } catch (error) {
      applyRuntimeCharacterSnapshot({
        schemaVersion: 1,
        revision: 0,
        currentCharacterId: null,
        characters: [],
      });
      setError(String(error));
    }
  }
  if (manifest.availableSections.includes("character") || manifest.availableSections.includes("appearance")) {
    const [{ createRuntimeAppearanceController }, { createInteractionLatencyTracer }] = await Promise.all([
      import("./appearance-runtime.js"),
      import("../core/interaction-latency.js"),
    ]);
    const interactionLatencyEnabled = await invoke("interaction_latency_diagnostics_enabled")
      .catch(() => false);
    const interactionLatencyTrace = createInteractionLatencyTracer({
      source: "settings",
      invoke,
      enabled: interactionLatencyEnabled,
    });
    runtimeAppearanceController = createRuntimeAppearanceController({
      document,
      invoke,
      onDirty: refreshDirty,
      onError: setError,
      prepare: prepareRuntimeAppearance,
      fillTheme: (theme) => setThemeValues(theme, { updateVisualEffect: false }),
      trace: interactionLatencyTrace,
    });
    if (runtimeCharacterSnapshot?.currentCharacterId) {
      try {
        const snapshot = await invoke("settings_character_appearance_get");
        await runtimeAppearanceController.initialize(snapshot);
        runtimeAppearanceInitialized = true;
      } catch {
        prepareRuntimeCharacterOnly();
      }
    } else {
      prepareRuntimeCharacterOnly();
    }
    await runtimeFontsReadyPromise;
    // 无角色时页面使用主程序默认浅蓝主题；有角色时由外观快照覆盖。
    await invoke("reveal_settings_window");
    if (!runtimeCharacterSnapshot?.currentCharacterId) showPage("character");
  }
  if (
    featureStatus(manifest, "providers.manage") === "available"
    || featureStatus(manifest, "model.chat_slot") === "available"
  ) {
    await initializeRuntimeSettingsSection(async () => {
      const { createProviderModelController } = await import("./provider-model-runtime.js");
      runtimeProviderModelController = createProviderModelController({
        invoke,
        readDraft: collectRuntimeProviderModelDraft,
        applySnapshot: applyRuntimeProviderModelSnapshot,
        onDirty: refreshDirty,
        onError: setError,
      });
      const snapshot = await invoke("settings_provider_model_get");
      await runtimeProviderModelController.initialize(snapshot);
    });
  }
  if (featureStatus(manifest, "chat.presentation_timing") === "available") {
    await initializeRuntimeSettingsSection(async () => {
      const { createChatTimingController } = await import("./chat-timing-runtime.js");
      runtimeChatTimingController = createChatTimingController({
        document,
        invoke,
        onDirty: refreshDirty,
      });
      const snapshot = await invoke("settings_chat_presentation_timing_get");
      runtimeChatTimingController.initialize(snapshot);
    });
  }
  if (featureStatus(manifest, "chat.bubble_auto_hide") === "available") {
    await initializeRuntimeSettingsSection(async () => {
      const { createBubbleAutoHideSettingsController } = await import("./bubble-auto-hide-runtime.js");
      runtimeBubbleAutoHideController = createBubbleAutoHideSettingsController({
        document,
        invoke,
        onDirty: refreshDirty,
      });
      runtimeBubbleAutoHideController.initialize(await invoke("settings_bubble_auto_hide_get"));
    });
  }
  if (featureStatus(manifest, "privacy.screen_awareness") === "available") {
    await initializeRuntimeSettingsSection(async () => {
      const { createScreenAwarenessSettingsController } = await import("./screen-awareness-runtime.js");
      runtimeScreenAwarenessController = createScreenAwarenessSettingsController({
        document,
        invoke,
        enhanceSelect,
        refreshSelect,
        onDirty: refreshDirty,
      });
      runtimeScreenAwarenessController.initialize(await invoke("settings_screen_awareness_get"));
    });
  }
  if (featureStatus(manifest, "plugins.manage") === "available") {
    await initializeRuntimeSettingsSection(async () => {
      const { createPluginController } = await import("./plugin-runtime.js");
      runtimePluginController = createPluginController({
        invoke,
        applySnapshot: applyRuntimePluginSnapshot,
        readDraft: runtimePluginDraft,
        onDirty: refreshDirty,
      });
      runtimePluginController.initialize(await invoke("settings_plugins_get"));
    });
  }
  if (featureStatus(manifest, "voice.tts") === "available") {
    await initializeRuntimeSettingsSection(async () => {
      const { createVoiceController } = await import("./voice-runtime.js");
      runtimeVoiceController = createVoiceController({
        document,
        invoke,
        enhanceSelect,
        refreshSelect,
        refreshAvailability: async () => { await runtimePluginController?.refreshCurrent(); },
        openPlugins: () => showPage("plugins"),
        onDirty: refreshDirty,
        onStatus: notify,
      });
      await runtimeVoiceController.refreshCurrent();
    });
  }
  if (
    featureStatus(manifest, "tools.runtime_limits") === "available"
  ) {
    await initializeRuntimeSettingsSection(async () => {
      const { createToolsController } = await import("./tools-runtime.js");
      runtimeToolsController = createToolsController({
        document,
        invoke,
        onDirty: refreshDirty,
      });
      runtimeToolsController.initialize(await invoke("settings_tools_get"));
    });
  }
  if (featureStatus(manifest, "storage.tts_root") === "available") {
    await initializeRuntimeSettingsSection(refreshStorageSettings);
  }
  if (featureStatus(manifest, "system.launch_at_login") === "available") {
    await initializeRuntimeSettingsSection(async () => {
      const {
        autostartErrorMessage,
        createAutostartSettingsController,
      } = await import("./autostart-runtime.js");
      runtimeAutostartController = createAutostartSettingsController({
        document,
        invoke,
        onDirty: refreshDirty,
      });
      let snapshot;
      try {
        snapshot = await invoke("settings_autostart_get");
      } catch (error) {
        throw new Error(autostartErrorMessage(error));
      }
      runtimeAutostartController.initialize(snapshot);
    });
  }
  if (featureStatus(manifest, "telemetry.anonymous_statistics") === "available") {
    await initializeRuntimeSettingsSection(refreshTelemetrySettings);
  }
  if (featureStatus(manifest, "storage.legacy_role_data_import") !== "available") {
    fields.legacyRoleDataImportButton.disabled = true;
    fields.legacyRoleDataImportStatus.textContent = "当前运行环境不支持旧数据导入。";
  }
  if (manifest.availableSections.includes("about")) {
    await initializeRuntimeSettingsSection(refreshAboutSettings);
  }
  settingsBaseline = null;
  refreshDirty();
  runtimeDiagnostics?.markReady({ settings: true });
}

startSettingsFrontend()
  .then(async () => {
    const { createFirstRunGuide, firstRunGuideRequested } = await import("./first-run-guide.js");
    firstRunGuideController = createFirstRunGuide({
      document,
      window,
      showPage,
      invoke,
      notify,
    });
    if (firstRunGuideRequested()) firstRunGuideController.start({ persist: true });
  })
  .catch((error) => {
    if (!settingsWindowClosing && error?.code !== "MEMORY_INITIALIZATION_CANCELLED") {
      setError(String(error));
    }
  });
