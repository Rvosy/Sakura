import { installClickIconMotion } from "../core/icons.js";
import { enhanceSelect, refreshSelect, closeSelects, focusSelect } from "./select-control.js";
import {
  createRootSettingsClient,
  formatSettingsError,
  legacyDataImportPlanHasWork,
} from "./root-settings-runtime.js";
import {
  hasCharacterScopedDrafts,
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
installClickIconMotion(document);

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
  themeColors: document.getElementById("themeColors"),
  visualEffectMode: document.getElementById("visualEffectMode"),
  themeAiButton: document.getElementById("themeAiButton"),
  resetThemeButton: document.getElementById("resetThemeButton"),
  bubbleAutoHide: document.getElementById("bubbleAutoHide"),
  bubbleAutoHideDelay: document.getElementById("bubbleAutoHideDelay"),
  speechFontSize: document.getElementById("speechFontSize"),
  nameFontSize: document.getElementById("nameFontSize"),
  inputFontSize: document.getElementById("inputFontSize"),
  storageUserRoot: document.getElementById("storageUserRoot"),
  storageTtsRoot: document.getElementById("storageTtsRoot"),
  storageTtsStatus: document.getElementById("storageTtsStatus"),
  storageOpenUserRoot: document.getElementById("storageOpenUserRoot"),
  storageChooseTtsRoot: document.getElementById("storageChooseTtsRoot"),
  storageResetTtsRoot: document.getElementById("storageResetTtsRoot"),
  legacyRoleDataImportButton: document.getElementById("legacyRoleDataImportButton"),
  legacyRoleDataImportStatus: document.getElementById("legacyRoleDataImportStatus"),
  systemFirstRunGuideButton: document.getElementById("systemFirstRunGuideButton"),
  macosSettingsOpenSystemSettingsButton: document.getElementById("macosSettingsOpenSystemSettingsButton"),
  macosSettingsOpenAppleSupportButton: document.getElementById("macosSettingsOpenAppleSupportButton"),
  macosSettingsOpenHelpStatus: document.getElementById("macosSettingsOpenHelpStatus"),
  updateStatus: document.getElementById("updateStatus"),
  updateNotes: document.getElementById("updateNotes"),
  updateFeedback: document.getElementById("updateFeedback"),
  updateCheckButton: document.getElementById("updateCheckButton"),
  updateCheckLabel: document.getElementById("updateCheckLabel"),
  updateAutoCheck: document.getElementById("updateAutoCheck"),
  updateActionButton: document.getElementById("updateActionButton"),
  updateActionIcon: document.getElementById("updateActionIcon"),
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
  errorText: document.getElementById("errorText"),
  saveButton: document.getElementById("saveButton"),
  applyButton: document.getElementById("applyButton"),
  cancelButton: document.getElementById("cancelButton"),
  pageHead: document.querySelector(".page-head"),
  pageTitle: document.getElementById("pageTitle"),
  pageSubtitle: document.getElementById("pageSubtitle"),
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
    "open-help": document.getElementById("page-open-help"),
    about: document.getElementById("page-about"),
    memory: document.getElementById("page-memory"),
  },
};

let request = null;
let runtimeAppearanceController = null;
let runtimeCharacterFeature = null;
let runtimeProviderFeature = null;
let runtimeChatTimingController = null;
let runtimeBubbleAutoHideController = null;
let runtimeToolsController = null;
let runtimePluginController = null;
let latestUpdateSnapshot = null;
let updateActionBusy = false;
let runtimeVoiceController = null;
let runtimeAsrController = null;
let runtimeScreenAwarenessController = null;
let runtimeAutostartController = null;
let firstRunGuideController = null;
let runtimeAppearanceInitialized = false;
let runtimeCapabilityManifest = null;
let runtimeVisualEffectModes = Object.freeze([
  Object.freeze({ id: "solid", label: "纯色块", disabled: false, reason: "" }),
  Object.freeze({ id: "gaussian_blur", label: "高斯模糊", disabled: false, reason: "" }),
  Object.freeze({ id: "liquid_glass", label: "液态玻璃", disabled: false, reason: "" }),
]);
let themeChanged = false;
// 程序化关窗（保存/取消）前置真，避免关窗拦截器把正常关闭误判成「放弃改动」。
let bypassCloseGuard = false;
let settingsWindowClosing = false;


let activeThemeField = "";
let themeEditor = {};
const RUNTIME_UNAVAILABLE_REASON = "此设置暂不可用";
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
  request = {
    ...(request || {}),
    theme: { ...theme, visual_effect_mode: snapshot.appearance.values.visualEffectMode },
    theme_defaults: themeDefaults,
    theme_fields: themeFields.map(([, id, label]) => ({ id, label })),
    visual_effect_modes: runtimeVisualEffectModes.map((mode) => ({ ...mode })),
  };

  runtimeCharacterFeature?.applyAppearancePresentation(snapshot.presentation, theme, themeDefaults);

  renderThemeControls();
  setThemeValues(theme);
  for (const [fieldKey, [bounds, value]] of Object.entries(RUNTIME_LAYOUT_DEFAULTS)) {
    setNumericBounds(fields[fieldKey], bounds);
    fields[fieldKey].value = String(value);
    updateSliderOutput(fieldKey);
  }

  for (const control of [
    fields.themeAiButton,
    themeEditor.pick,
  ]) {
    // Each of these shares a row with a migrated control. Disable only the
    // unavailable button so the active character/import/theme controls do not
    // inherit the legacy grey unavailable treatment.
    disableRuntimeControl(control, { markRow: false });
  }
  enhanceSelect(fields.visualEffectMode);
  refreshSelect(fields.visualEffectMode);
  upgradeSliderControls();
  runtimeCharacterFeature?.prepareControls();
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
function computeDirty() {
  return Boolean(
    runtimeAppearanceController?.isDirty()
    || runtimeProviderFeature?.isDirty()
    || runtimeChatTimingController?.isDirty()
    || runtimeBubbleAutoHideController?.isDirty()
    || runtimeToolsController?.isDirty()
    || runtimePluginController?.isDirty()
    || runtimeVoiceController?.isDirty()
    || runtimeAsrController?.isDirty()
    || runtimeScreenAwarenessController?.isDirty()
    || runtimeAutostartController?.isDirty()
    || runtimeCharacterFeature?.isDirty()
  );
}

function refreshDirty() {
  const dirty = computeDirty();
  document.body.classList.toggle("is-dirty", dirty);
  fields.saveButton.classList.toggle("has-changes", dirty);
  runtimeCharacterFeature?.syncControls();
}

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

async function closeSettingsWindow() {
  bypassCloseGuard = true;
  beginSettingsWindowClose();
  try {
    await runtimeCharacterFeature?.waitForPreview();
    await runtimeProviderFeature?.cancelOperations();
    await invoke("resolve_settings_close", { discard: true });
  } catch (error) {
    settingsWindowClosing = false;
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
    const { executeSettingsClose } = await settingsCloseFlowPromise;
    setError("");
    await executeSettingsClose({
      dirty: computeDirty(),
      choose: () => chooseUnsavedClose("关闭"),
      save: async () => {
        setSubmissionBusy(true);
        await saveRuntimeSettings();
        notify("已保存。", "success");
      },
      discard: async () => {
        setSubmissionBusy(true);
        await runtimeAppearanceController?.cancelPreview();
        await runtimeProviderFeature?.cancelOperations();
        runtimeChatTimingController?.discard();
        runtimeBubbleAutoHideController?.discard();
        runtimeAutostartController?.discard();
        runtimeToolsController?.discard();
        await runtimeCharacterFeature?.discard();
      },
      close: closeSettingsWindow,
      stay: async () => {
        await invoke("resolve_settings_close", { discard: false });
      },
    });
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
      choose: () => chooseUnsavedClose("退出"),
      save: async () => {
        setSubmissionBusy(true);
        await saveRuntimeSettings();
        notify("已保存。", "success");
      },
      discard: async () => {
        setSubmissionBusy(true);
        await runtimeAppearanceController?.cancelPreview();
        await runtimeProviderFeature?.cancelOperations();
        runtimeChatTimingController?.discard();
        runtimeBubbleAutoHideController?.discard();
        runtimeAutostartController?.discard();
        runtimeToolsController?.discard();
        await runtimeCharacterFeature?.discard();
      },
      close: async () => {
        beginSettingsWindowClose();
        try {
          await runtimeCharacterFeature?.waitForPreview();
          await runtimeProviderFeature?.cancelOperations();
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

function removeOverlayAfterExit(overlay) {
  if (!overlay?.isConnected) return Promise.resolve();
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
        event.preventDefault();
        event.stopImmediatePropagation();
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
    (runtimePluginController?.dialogElement() || document.body).append(overlay);
    confirm.focus();
  });
}

async function chooseUnsavedClose(action) {
  const { CloseDecision } = await settingsCloseFlowPromise;
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "confirm-overlay";
    const dialog = document.createElement("section");
    dialog.className = "confirm-dialog settings-close-dialog";
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    const heading = document.createElement("h2");
    heading.textContent = `${action}前保存设置？`;
    const actions = document.createElement("div");
    actions.className = "confirm-actions";
    const stay = document.createElement("button");
    stay.type = "button";
    stay.className = "secondary-button";
    stay.textContent = "继续编辑";
    const discard = document.createElement("button");
    discard.type = "button";
    discard.className = "danger-button";
    discard.textContent = `不保存并${action}`;
    const save = document.createElement("button");
    save.type = "button";
    save.textContent = `保存并${action}`;
    actions.append(stay, discard, save);
    dialog.append(heading, actions);
    overlay.append(dialog);

    function close(decision) {
      document.removeEventListener("keydown", onKey, true);
      overlay.remove();
      resolve(decision);
    }
    function onKey(event) {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopImmediatePropagation();
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
    (runtimePluginController?.dialogElement() || document.body).append(overlay);
    save.focus();
  });
}

function runThemeTransition(update) {
  if (typeof document.startViewTransition !== "function") {
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
  if (!element) {
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
  character: { title: "角色与布局" },
  appearance: { title: "外观" },
  providers: { title: "模型服务" },
  model: { title: "模型" },
  voice: { title: "语音" },
  interaction: { title: "交互" },
  tools: { title: "工具" },
  plugins: { title: "插件" },
  system: { title: "系统" },
  about: { title: "关于" },
  memory: { title: "记忆" },
  "open-help": { title: "应用打开遇到问题？" },
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
  const meta = pageMeta[page];
  if (meta) {
    fields.pageTitle.textContent = meta.title;
    fields.pageSubtitle.textContent = "";
    fields.pageSubtitle.hidden = true;
    replayMotion(fields.pageHead, "is-switching");
  }
  runtimeProviderFeature?.onPageChanged(page);
  runtimePluginController?.onPageChanged(page);
  runtimeAsrController?.onPageChanged(page);
}

function syncEnabledState() {
  const enabled = fields.enabled.checked;
  setControlDisabled(fields.checkInterval, !enabled);
  setControlDisabled(fields.cooldown, !enabled);
  setControlDisabled(fields.batchLimit, !enabled);
  setControlDisabled(fields.screenResolution, !enabled);
}

function syncBubbleState() {
  setControlDisabled(fields.bubbleAutoHideDelay, !fields.bubbleAutoHide.checked);
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
  runtimeCharacterFeature?.prepareControls();
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
  fields.legacyRoleDataImportStatus.textContent = "正在检查旧数据…";
  try {
    const plan = await rootSettingsClient.legacyRoleDataImportChoose();
    if (!plan) {
      fields.legacyRoleDataImportStatus.textContent = "";
      return;
    }
    if (plan.blocked) {
      throw new Error("角色身份冲突，无法导入。");
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
        fields.legacyRoleDataImportStatus.textContent = "已取消导入。";
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
  fields.updateActionButton.dataset.widthLabel = fields.updateActionLabel.textContent;
  fields.updateActionIcon.classList.toggle("icon-download", snapshot.mode === "portable");
  fields.updateActionIcon.classList.toggle("icon-circle-arrow-up", snapshot.mode !== "portable");
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
      fields.updateStatus.textContent = "已打开新版便携版压缩包的下载链接。";
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

function currentCharacterHasDrafts() {
  return hasCharacterScopedDrafts({
    appearanceDirty: runtimeAppearanceController?.isDirty(),
    voiceDirty: runtimeVoiceController?.isDirty(),
    memoryEditorDraftCount: (runtimePluginController?.characterDraftCount() || 0),
  });
}

async function rebindSettingsAfterCharacterSwitch(generationId) {
  runtimeProviderFeature?.rebindIdentity(generationId);
  runtimeScreenAwarenessController?.rebindIdentity(generationId);
  await runtimeAppearanceController?.rebindGeneration(generationId);
  await runtimeToolsController?.refreshCurrent();
  await runtimePluginController?.refreshCurrent();
  await runtimeVoiceController?.refreshCurrent({ preserveDraft: true });
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

function runtimeFeatureAvailable(feature) {
  return Object.values(runtimeCapabilityManifest?.sections || {})
    .some((section) => section?.features?.[feature] === "available");
}

// 布局滑块的输出显示；预览和保存由 Appearance controller 处理。
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

async function refreshRuntimeVoiceCurrent() {
  await runtimeAsrController?.refresh({ preserveDraft: true });
  if (!runtimeVoiceController) return;
  await runtimeVoiceController.refreshCurrent({ preserveDraft: true });
}

async function saveRuntimeSettings() {
  if (runtimePluginController?.hasCollectionDrafts()) {
    throw new Error("请先保存或还原正在编辑的集合记录，再保存设置。");
  }
  if (runtimeAsrController?.isDirty()) await runtimeAsrController.save();
  if (runtimeAppearanceController?.isDirty()) await runtimeAppearanceController.save();
  let result = null;
  if (runtimeScreenAwarenessController?.isDirty()) {
    result = await runtimeScreenAwarenessController.save();
  }
  if (runtimeProviderFeature?.isDirty()) {
    result = await runtimeProviderFeature.save();
    await runtimePluginController?.refreshCurrent();
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
  }
  if (runtimePluginController?.isDirty()) {
    result = await runtimePluginController.save();
    await runtimeProviderFeature?.refreshCurrent();
    await refreshRuntimeVoiceCurrent();
  }
  if (runtimeVoiceController?.isDirty()) {
    result = await runtimeVoiceController.save();
    await runtimePluginController?.refreshCurrent();
    await runtimeProviderFeature?.refreshCurrent();
  }
  const characterResult = await runtimeCharacterFeature?.commit();
  if (characterResult !== null) result = characterResult;
  return result;
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

fields.navItems.forEach((item) => {
  item.addEventListener("click", () => showPage(item.dataset.page));
});
layoutSliders.forEach((fieldKey) => {
  const preview = () => {
    updateSliderOutput(fieldKey);
  };
  fields[fieldKey].addEventListener("input", preview);
  fields[fieldKey].addEventListener("change", preview);
});
["speechFontSize", "nameFontSize", "inputFontSize"].forEach((fieldKey) => {
  const preview = () => {
    updateSliderOutput(fieldKey);
  };
  fields[fieldKey].addEventListener("input", preview);
  fields[fieldKey].addEventListener("change", preview);
});
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
async function runMacosOpenHelpAction(button, action, successMessage) {
  button.disabled = true;
  fields.macosSettingsOpenHelpStatus.textContent = "";
  try {
    await action();
    fields.macosSettingsOpenHelpStatus.textContent = successMessage;
  } catch (error) {
    fields.macosSettingsOpenHelpStatus.textContent = `无法完成操作：${String(error)}`;
  } finally {
    button.disabled = false;
  }
}
fields.macosSettingsOpenSystemSettingsButton.addEventListener("click", () => {
  void runMacosOpenHelpAction(
    fields.macosSettingsOpenSystemSettingsButton,
    () => rootSettingsClient.macosOpenSystemSettings(),
    "系统设置已打开，请选择“隐私与安全性”。",
  );
});
fields.macosSettingsOpenAppleSupportButton.addEventListener("click", () => {
  void runMacosOpenHelpAction(
    fields.macosSettingsOpenAppleSupportButton,
    () => rootSettingsClient.macosOpenAppleSupport(),
    "已在浏览器中打开 Apple 官方说明。",
  );
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
fields.visualEffectMode.addEventListener("change", markThemeChanged);
fields.visualEffectMode.addEventListener("runtime-value-applied", () => refreshSelect(fields.visualEffectMode));
fields.resetThemeButton.addEventListener("click", () => {
  setThemeValues((runtimeCharacterFeature?.selectedThemeDefaults() || request.theme_defaults), { updateVisualEffect: false, animateTheme: true });
  themeChanged = true;
});
fields.bubbleAutoHide.addEventListener("change", syncBubbleState);
fields.saveButton.addEventListener("click", async () => {
  if (runtimeCharacterFeature?.isSwitching()) {
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
});

fields.applyButton.addEventListener("click", async () => {
  if (runtimeCharacterFeature?.isSwitching()) {
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
});

fields.cancelButton.addEventListener("click", async () => {
  await requestCancelClose();
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
  runtimeAppearanceController?.dispose();
  runtimeCharacterFeature?.dispose();
  runtimeProviderFeature?.dispose();
  runtimeChatTimingController?.dispose();
  runtimeBubbleAutoHideController?.dispose();
  runtimeToolsController?.dispose();
  runtimePluginController?.dispose();
  runtimeVoiceController?.dispose();
  runtimeAsrController?.dispose();
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
  let manifest = await invoke("settings_capability_manifest");
  const { createCharacterSettingsFeature } = await import("./character-settings.js");
  runtimeCharacterFeature = createCharacterSettingsFeature({
    document,
    window,
    invoke,
    onDirty: refreshDirty,
    onError: setError,
    notify,
    enhanceSelect,
    refreshSelect,
    hasCharacterDrafts: currentCharacterHasDrafts,
    isSubmitting: () => submissionBusy,
    applyPreviewTheme: (theme) => runThemeTransition(() => applyThemeTokens(theme)),
    rebindSettings: rebindSettingsAfterCharacterSwitch,
    clearCharacterState: () => runtimePluginController?.clearCharacterState(),
    renderMemorySurface: () => runtimePluginController?.renderMemorySurface(),
  });
  window.__TAURI__?.event?.listen?.("sakura://character-catalog-changed", ({ payload } = {}) => {
    if (settingsWindowClosing) return;
    void runtimeCharacterFeature?.refreshCatalog(payload);
  });
  const {
    applyCapabilityManifest,
    featureStatus,
    inputVisualEffectModes,
  } = await import("./capability-shell.js");
  manifest = applyCapabilityManifest(document, manifest);
  runtimeCapabilityManifest = manifest;
  runtimeVisualEffectModes = inputVisualEffectModes(manifest);
  await initializeRuntimeSettingsSection(async () => {
    const { createAsrSettingsController } = await import("./asr-runtime.js");
    runtimeAsrController = createAsrSettingsController({
      document, invoke, enhanceSelect, refreshSelect,
      listen: (eventName, handler) => window.__TAURI__.event.listen(eventName, handler),
      onDirty: refreshDirty, onStatus: notify,
    });
    await runtimeAsrController.refresh();
  });
  if (featureStatus(manifest, "character.manage") === "available") {
    await runtimeCharacterFeature.initialize();
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
    if (runtimeCharacterFeature?.currentCharacterId()) {
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
    if (!runtimeCharacterFeature?.currentCharacterId()) showPage("character");
  }
  if (
    featureStatus(manifest, "providers.manage") === "available"
    || featureStatus(manifest, "model.chat_slot") === "available"
  ) {
    await initializeRuntimeSettingsSection(async () => {
      const { createProviderSettingsFeature } = await import("./provider-settings.js");
      runtimeProviderFeature = createProviderSettingsFeature({
        document,
        window,
        invoke,
        onDirty: refreshDirty,
        onError: setError,
        notify,
        showPage,
        enhanceSelect,
        refreshSelect,
        markInvalid,
        setControlDisabled,
        setNumericBounds,
      });
      await runtimeProviderFeature.initialize();
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
      const { createPluginSettingsFeature } = await import("./plugin-settings.js");
      runtimePluginController = createPluginSettingsFeature({
        document,
        window,
        invoke,
        onDirty: refreshDirty,
        onError: setError,
        notify,
        confirmAction,
        enhanceSelect,
        refreshSelect,
        closeSelects,
        focusSelect,
        replayMotion,
        getVoiceController: () => runtimeVoiceController,
        getAsrController: () => runtimeAsrController,
        removeOverlayAfterExit,
        showPage,
        isMemoryTransitioning: () => runtimeCharacterFeature?.isTransitioning(),
        hasPendingCharacterSelection: () => Boolean(runtimeCharacterFeature?.pendingCharacterId()),
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
        onSectionsRendered: () => runtimePluginController?.onVoiceSectionsRendered(),
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
