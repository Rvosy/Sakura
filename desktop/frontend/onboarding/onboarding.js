import { installDevtoolsShortcutGuard } from "../core/devtools-guard.js";
import { createRuntimeDiagnostics } from "../core/runtime-diagnostics.js";

installDevtoolsShortcutGuard();
document.addEventListener("contextmenu", (event) => event.preventDefault());

const runtimeDiagnostics = createRuntimeDiagnostics({ invoke: window.__TAURI__.core.invoke });
const invoke = runtimeDiagnostics.invoke;
window.addEventListener("beforeunload", () => runtimeDiagnostics.dispose({ settings: true }), { once: true });
const routeView = document.getElementById("routeView");
const migrationView = document.getElementById("migrationView");
const firstUseButton = document.getElementById("firstUseButton");
const migrationButton = document.getElementById("migrationButton");
const migrationBackButton = document.getElementById("migrationBackButton");
const migrationChooseButton = document.getElementById("migrationChooseButton");
const migrationStartButton = document.getElementById("migrationStartButton");
const migrationCancelButton = document.getElementById("migrationCancelButton");
const migrationContinueButton = document.getElementById("migrationContinueButton");
const startupStatus = document.getElementById("startupStatus");
const migrationSourceLabel = document.getElementById("migrationSourceLabel");
const migrationInspection = document.getElementById("migrationInspection");
const migrationVersion = document.getElementById("migrationVersion");
const migrationSpace = document.getElementById("migrationSpace");
const migrationDomains = document.getElementById("migrationDomains");
const migrationIssues = document.getElementById("migrationIssues");
const migrationProgress = document.getElementById("migrationProgress");
const migrationStage = document.getElementById("migrationStage");
const migrationPercent = document.getElementById("migrationPercent");
const migrationProgressBar = document.getElementById("migrationProgressBar");
const migrationMessage = document.getElementById("migrationMessage");
const migrationError = document.getElementById("migrationError");
const reduceMotionQuery = window.matchMedia?.("(prefers-reduced-motion: reduce)") || null;

const activeMigrationStates = new Set([
  "inspecting",
  "staging",
  "validating",
  "committing",
  "core_validating",
]);
const terminalMigrationStates = new Set(["completed", "failed", "cancelled"]);
const animationReplayHandles = new WeakMap();

let selectionId = null;
let selectionCompatible = null;
let migrationRunning = false;
let viewTransitionRunning = false;
let previousProgressSnapshot = null;
let migrationPollHandle = 0;
let migrationPollInFlight = false;
let migrationRequiresSetup = false;
let overwriteDomains = [];

const domainLabels = {
  config: "配置",
  characters: "角色",
  history: "对话",
  memory: "记忆",
  tts: "TTS 运行资源",
  ttsBundles: "角色语音模型",
  notes: "笔记",
  reminders: "提醒",
  tasks: "任务",
  characterStudio: "角色工坊",
  pluginData: "插件数据",
  screenState: "视觉摘要",
  visualRecords: "视觉原始记录（隔离）",
  runtimeEvents: "运行事件（隔离）",
  legacyMemoryJson: "旧记忆备份（隔离）",
};

const errorMessages = {
  LEGACY_SOURCE_NOT_DIRECTORY: "选择的目录不可用。",
  LEGACY_LAYOUT_UNRECOGNIZED: "这里不是受支持的 Sakura 0.9.x 目录。",
  LEGACY_VERSION_UNSUPPORTED: "只能迁移 Sakura 0.9.x。",
  LEGACY_PLATFORM_UNSUPPORTED: "无法识别旧版本所属平台，请选择完整的 Windows 或 macOS 安装目录。",
  LEGACY_TARGET_PLATFORM_UNSUPPORTED: "当前系统暂不支持旧版本迁移。",
  LEGACY_CROSS_PLATFORM_UNSUPPORTED: "旧版本与当前 Sakura 不在同一平台，无法安全迁移运行资源。",
  LEGACY_SOURCE_ACTIVE: "检测到 Sakura 0.9.x 仍在运行，请先完全退出旧版本后再导入。",
  LEGACY_TARGET_SPACE_INSUFFICIENT: "可用磁盘空间不足。",
  LEGACY_TTS_LINK_BROKEN: "旧版 TTS 外置目录已经断开。",
  LEGACY_TTS_LAYOUT_UNRECOGNIZED: "无法识别旧版 TTS 目录结构。",
  LEGACY_TTS_TARGET_OVERLAP: "旧版 TTS 目录与 v2 数据目录重叠，无法安全迁移。",
  LEGACY_NESTED_LINK_UNSUPPORTED: "旧数据中存在嵌套链接，无法确认复制边界。",
  LEGACY_TTS_ABSOLUTE_LINKS_SKIPPED: "旧版 TTS 中指向原安装位置的绝对链接已跳过；角色模型原文件仍会迁移。",
  LEGACY_COPY_CONFLICT: "两个旧数据文件映射到了同一位置，但内容不同。",
  LEGACY_TTS_CONFIG_VALIDATION_FAILED: "旧版 TTS 配置无法转换为当前格式。",
  LEGACY_SETTINGS_VALIDATION_FAILED: "旧版配置无法转换为当前设置格式。",
  LEGACY_HISTORY_JSON_INVALID: "聊天历史中存在损坏的记录。",
  LEGACY_HISTORY_ROLE_UNSUPPORTED: "聊天历史中存在无法识别的记录类型。",
  LEGACY_HISTORY_TIMESTAMP_INVALID: "聊天历史中存在无效时间。",
  LEGACY_MEMORY_DATABASE_INVALID: "旧版长期记忆数据库损坏。",
  LEGACY_MEMORY_SCHEMA_INVALID: "旧版长期记忆数据库结构不兼容。",
  LEGACY_MEMORY_DIMENSION_UNSUPPORTED: "旧版记忆向量维度不是当前支持的 384 维。",
  LEGACY_MEMORY_OPEN_FAILED: "当前记忆插件无法打开迁移后的旧记忆库。",
  LEGACY_MCP_VALIDATION_FAILED: "旧版 MCP 配置无法转换为当前格式。",
  LEGACY_REMINDERS_VALIDATION_FAILED: "旧版提醒数据无法转换为当前格式。",
  LEGACY_TASKS_VALIDATION_FAILED: "旧版任务数据无法转换为当前格式。",
  LEGACY_NOTE_VALIDATION_FAILED: "旧版笔记包含当前版本无法读取的文件。",
  LEGACY_CHARACTER_STUDIO_VALIDATION_FAILED: "旧版角色工坊草稿无法转换为当前格式。",
  LEGACY_SCREEN_STATE_VALIDATION_FAILED: "旧版视觉摘要状态无法转换为当前格式。",
  LEGACY_IMPORT_FIRST_RUN_ONLY: "只有尚未完成首次设置时才能迁移旧版本。",
  LEGACY_IMPORT_CORE_RUNNING: "Sakura Core 已启动，请重启应用后先执行旧版本迁移。",
  LEGACY_IMPORT_CONFIRMATION_STALE: "目标数据在确认后发生了变化，请重新检查并确认覆盖范围。",
  LEGACY_IMPORT_CANCELLED: "迁移已取消，现有数据没有改变。",
  LEGACY_CORE_VALIDATION_FAILED: "迁移数据未通过 Core 校验，已恢复到迁移前状态。",
  LEGACY_ROLLBACK_FAILED: "自动恢复失败，请保留旧目录并查看诊断信息。",
};

function replayAnimation(element, className) {
  const previousHandle = animationReplayHandles.get(element);
  if (previousHandle) {
    window.cancelAnimationFrame(previousHandle.frameId);
    window.clearTimeout(previousHandle.timeoutId);
    element.classList.remove(previousHandle.className);
  }
  element.classList.remove(className);
  if (reduceMotionQuery?.matches) {
    animationReplayHandles.delete(element);
    return;
  }
  const handle = { className, frameId: 0, timeoutId: 0 };
  handle.frameId = window.requestAnimationFrame(() => {
    element.classList.add(className);
    handle.timeoutId = window.setTimeout(() => {
      if (animationReplayHandles.get(element) !== handle) return;
      element.classList.remove(className);
      animationReplayHandles.delete(element);
    }, 520);
  });
  animationReplayHandles.set(element, handle);
}

function setAnimatedText(element, value, className = "is-updating") {
  if (element.textContent === value) return false;
  element.textContent = value;
  replayAnimation(element, className);
  return true;
}

function waitForViewAnimation(element, fallbackMs) {
  if (reduceMotionQuery?.matches) return Promise.resolve();
  return new Promise((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      element.removeEventListener("animationend", onAnimationEnd);
      window.clearTimeout(timeoutId);
      resolve();
    };
    const onAnimationEnd = (event) => {
      if (event.target === element) finish();
    };
    const timeoutId = window.setTimeout(finish, fallbackMs);
    element.addEventListener("animationend", onAnimationEnd);
  });
}

async function transitionViews(fromView, toView, direction, focusTarget) {
  if (viewTransitionRunning || !toView.hidden) return;
  viewTransitionRunning = true;
  const leavingClass = `is-leaving-${direction}`;
  const enteringClass = `is-entering-${direction}`;
  fromView.inert = true;
  toView.inert = true;
  try {
    if (!reduceMotionQuery?.matches) {
      fromView.classList.add(leavingClass);
      await waitForViewAnimation(fromView, 240);
    }
    fromView.hidden = true;
    fromView.classList.remove(leavingClass);
    toView.classList.add(enteringClass);
    toView.hidden = false;
    toView.inert = false;
    focusTarget.focus({ preventScroll: true });
    await waitForViewAnimation(toView, 320);
  } finally {
    fromView.classList.remove(leavingClass);
    toView.classList.remove(enteringClass);
    if (!fromView.hidden) fromView.inert = false;
    if (!toView.hidden) toView.inert = false;
    viewTransitionRunning = false;
  }
}

async function openFirstUseGuide() {
  firstUseButton.disabled = true;
  setAnimatedText(startupStatus, "正在启动 Sakura…");
  try {
    await invoke("first_run_start_core");
    window.location.replace("../settings/index.html?guide=first-run");
  } catch (error) {
    firstUseButton.disabled = false;
    setAnimatedText(startupStatus, `无法启动 Sakura：${String(error)}`);
  }
}

async function showMigrationView() {
  await transitionViews(routeView, migrationView, "forward", migrationBackButton);
}

async function showRouteView() {
  if (migrationRunning) return;
  await transitionViews(migrationView, routeView, "backward", migrationButton);
}

firstUseButton.addEventListener("click", openFirstUseGuide);
migrationContinueButton.addEventListener("click", async () => {
  if (migrationRequiresSetup) {
    window.location.replace("../settings/index.html?guide=first-run");
    return;
  }
  migrationContinueButton.disabled = true;
  try {
    await invoke("resolve_settings_close", { discard: true });
  } catch (error) {
    migrationContinueButton.disabled = false;
    setAnimatedText(migrationError, `无法关闭迁移窗口：${String(error)}`);
  }
});
migrationButton.addEventListener("click", showMigrationView);
migrationBackButton.addEventListener("click", showRouteView);
migrationChooseButton.addEventListener("click", chooseLegacySource);
migrationStartButton.addEventListener("click", startMigration);
migrationCancelButton.addEventListener("click", cancelMigration);

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let number = bytes;
  let unit = "B";
  for (const candidate of units) {
    number /= 1024;
    unit = candidate;
    if (number < 1024) break;
  }
  return `${number.toFixed(number >= 10 ? 1 : 2)} ${unit}`;
}

function publicError(error) {
  const code = error?.code || String(error || "LEGACY_IMPORT_FAILED").split(":", 1)[0];
  const base = errorMessages[code] || `迁移失败（${code}）`;
  const location = error?.relativePath
    ? ` 文件：${error.relativePath}${error.line ? `:${error.line}` : ""}`
    : "";
  const diagnostic = error?.diagnosticLog ? ` 诊断日志：${error.diagnosticLog}` : "";
  return `${base}${location}${diagnostic}`;
}

function renderInspection(snapshot) {
  const inspection = snapshot?.inspection;
  if (!inspection) return;
  const inspectionWasHidden = migrationInspection.hidden;
  selectionId = snapshot.selectionId || null;
  selectionCompatible = inspection.compatible === true;
  overwriteDomains = Array.isArray(inspection.overwriteDomains)
    ? inspection.overwriteDomains.filter((value) => typeof value === "string")
    : [];
  migrationInspection.hidden = false;
  migrationSourceLabel.textContent = inspection.sourceLabel || "已选择旧版本";
  migrationVersion.textContent = `Sakura ${inspection.detectedVersion || "0.9.x"}`;
  migrationSpace.textContent = `核心数据至少需 ${formatBytes(inspection.requiredBytes)} · 可用 ${formatBytes(inspection.availableBytes)}`;
  migrationDomains.replaceChildren();
  let domainOrder = 0;
  for (const [name, value] of Object.entries(inspection.domains || {})) {
    if (!value?.present || !domainLabels[name]) continue;
    const title = document.createElement("dt");
    title.textContent = domainLabels[name];
    const detail = document.createElement("dd");
    const count = value.items || value.files || 0;
    detail.textContent = `${count} 项 · ${formatBytes(value.bytes)}`;
    const boundedOrder = Math.min(domainOrder, 6);
    title.style.setProperty("--domain-order", boundedOrder);
    detail.style.setProperty("--domain-order", boundedOrder);
    migrationDomains.append(title, detail);
    domainOrder += 1;
  }
  migrationIssues.replaceChildren();
  const issues = [...(inspection.blockers || []), ...(inspection.warnings || [])];
  for (const [index, issue] of issues.entries()) {
    const item = document.createElement("li");
    item.className = (inspection.blockers || []).includes(issue) ? "blocking" : "warning";
    item.style.setProperty("--issue-order", Math.min(index, 4));
    item.textContent = issue.code === "LEGACY_TTS_EXTERNAL_COPY"
      ? "检测到外置 TTS 目录；迁移时会复制到 v2，旧版删除后仍可使用。"
      : publicError(issue);
    migrationIssues.append(item);
  }
  migrationStartButton.disabled = !inspection.compatible || !selectionId;
  setAnimatedText(migrationError, inspection.compatible ? "" : "请先解决上面的阻断问题。");
  if (inspectionWasHidden) replayAnimation(migrationInspection, "is-revealing");
}

function renderSelection(snapshot) {
  selectionId = snapshot?.selectionId || null;
  selectionCompatible = null;
  overwriteDomains = [];
  migrationView.dataset.migrationState = "selected";
  previousProgressSnapshot = null;
  setAnimatedText(migrationSourceLabel, snapshot?.sourceLabel || "已选择旧版本");
  migrationInspection.hidden = true;
  migrationProgress.hidden = true;
  migrationProgress.classList.remove("is-revealing", "is-completing");
  setAnimatedText(migrationError, "");
  migrationStartButton.hidden = false;
  migrationStartButton.disabled = !selectionId;
  migrationCancelButton.hidden = true;
  migrationContinueButton.hidden = true;
  migrationContinueButton.disabled = false;
  migrationBackButton.hidden = false;
  migrationRequiresSetup = false;
}

function renderProgress(snapshot) {
  if (!snapshot) return;
  if (snapshot.inspection) renderInspection(snapshot);
  const state = snapshot.state || "idle";
  const active = activeMigrationStates.has(state);
  const progressVisible = active || terminalMigrationStates.has(state);
  const progressWasHidden = migrationProgress.hidden;
  const previous = previousProgressSnapshot;
  const stageText = state === "completed" ? "迁移完成" : (snapshot.message || "正在迁移");
  const percent = Number(snapshot.percent || 0);
  if (isProgressRegression(previous, state, percent)) return;
  const continueWasHidden = migrationContinueButton.hidden;
  const cancelWasHidden = migrationCancelButton.hidden;

  migrationRunning = active;
  migrationView.dataset.migrationState = state;
  migrationProgress.hidden = !progressVisible;
  migrationStage.textContent = stageText;
  migrationPercent.textContent = `${percent}%`;
  migrationProgressBar.value = percent;
  migrationMessage.textContent = snapshot.message || "";
  migrationChooseButton.disabled = active;
  migrationBackButton.disabled = active;
  migrationBackButton.hidden = state === "completed";
  migrationStartButton.hidden = active || state === "completed";
  migrationStartButton.disabled = active || !["selected", "ready"].includes(state) || !selectionId || selectionCompatible === false;
  migrationCancelButton.hidden = !snapshot.cancellable;
  migrationRequiresSetup = state === "completed" && snapshot.requiresSetup === true;
  migrationContinueButton.textContent = migrationRequiresSetup ? "继续首次设置" : "完成";
  migrationContinueButton.hidden = state !== "completed";
  migrationContinueButton.disabled = false;

  if (progressVisible && progressWasHidden && state !== "completed") {
    replayAnimation(migrationProgress, "is-revealing");
  }
  if (previous && !progressWasHidden && (
    previous.state !== state
    || previous.percent !== percent
    || previous.message !== (snapshot.message || "")
  )) {
    replayAnimation(migrationProgress.querySelector(".progress-copy"), "is-updating");
    replayAnimation(migrationMessage, "is-updating");
  }
  if (state === "completed" && previous?.state !== "completed") {
    migrationProgress.classList.remove("is-revealing");
    replayAnimation(migrationProgress, "is-completing");
  }
  if (!migrationContinueButton.hidden && continueWasHidden) {
    replayAnimation(migrationContinueButton, "is-revealing");
  }
  if (!migrationCancelButton.hidden && cancelWasHidden) {
    replayAnimation(migrationCancelButton, "is-revealing");
  }

  if (state === "failed") setAnimatedText(migrationError, publicError(snapshot.error));
  if (state === "cancelled") {
    setAnimatedText(migrationError, "迁移已取消，旧目录和当前 v2 数据均未改变。");
  }
  if (state === "completed") setAnimatedText(migrationError, "");

  previousProgressSnapshot = {
    state,
    percent,
    message: snapshot.message || "",
  };
  syncMigrationPolling(active);
}

function isProgressRegression(previous, state, percent) {
  if (!previous) return false;
  if (terminalMigrationStates.has(previous.state) && !terminalMigrationStates.has(state)) {
    return true;
  }
  if (!activeMigrationStates.has(previous.state) || !activeMigrationStates.has(state)) {
    return false;
  }
  // The stage label can legitimately return from `validating` to `staging`
  // when the already-validated small data is followed by the large TTS copy.
  // Overall percent is monotonic and is the authoritative ordering signal.
  return percent < previous.percent;
}

function syncMigrationPolling(active) {
  if (!active) {
    if (migrationPollHandle) window.clearTimeout(migrationPollHandle);
    migrationPollHandle = 0;
    return;
  }
  if (migrationPollHandle || migrationPollInFlight) return;
  migrationPollHandle = window.setTimeout(async () => {
    migrationPollHandle = 0;
    migrationPollInFlight = true;
    try {
      renderProgress(await invoke("legacy_import_state"));
    } catch {
      // The progress event remains the primary channel; the poll is only a
      // lost-event/race fallback and should not replace a useful UI state.
    } finally {
      migrationPollInFlight = false;
      if (migrationRunning) syncMigrationPolling(true);
    }
  }, 1000);
}

async function chooseLegacySource() {
  setAnimatedText(migrationError, "");
  migrationChooseButton.disabled = true;
  try {
    const snapshot = await invoke("legacy_import_choose_source");
    if (snapshot?.state === "selected") {
      renderSelection(snapshot);
      renderProgress(await invoke("legacy_import_inspect", { selectionId: snapshot.selectionId }));
    }
  } catch (error) {
    setAnimatedText(migrationError, publicError(error));
  } finally {
    migrationChooseButton.disabled = false;
  }
}

async function startMigration() {
  if (!selectionId || selectionCompatible === false) return;
  let confirmedOverwriteDomains = [];
  if (overwriteDomains.length) {
    const confirmed = window.confirm(
      `旧版本数据将覆盖当前的以下内容：\n\n${overwriteDomains.map((item) => `• ${item}`).join("\n")}\n\n聊天历史和长期记忆会优先保留。是否继续？`,
    );
    if (!confirmed) return;
    confirmedOverwriteDomains = [...overwriteDomains];
  }
  setAnimatedText(migrationError, "");
  try {
    renderProgress(await invoke("legacy_import_start", {
      selectionId,
      confirmedOverwriteDomains,
    }));
  } catch (error) {
    setAnimatedText(migrationError, publicError(error));
  }
}

async function cancelMigration() {
  migrationCancelButton.disabled = true;
  try {
    renderProgress(await invoke("legacy_import_cancel"));
  } catch (error) {
    setAnimatedText(migrationError, publicError(error));
  } finally {
    migrationCancelButton.disabled = false;
  }
}

async function bindWindowLifecycle() {
  const listen = window.__TAURI__?.event?.listen;
  if (!listen) return;
  await listen("sakura://settings-close-requested", () => {
    invoke("resolve_settings_close", { discard: true }).catch(() => {});
  });
  await listen("sakura://settings-exit-requested", () => {
    invoke("resolve_settings_exit", { discard: true }).catch(() => {});
  });
  await listen("sakura://settings-exit-timeout", () => {
    setAnimatedText(startupStatus, "退出请求已取消，请重试。");
  });
  await listen("sakura://legacy-import-progress", (event) => renderProgress(event.payload));
}

async function start() {
  runtimeDiagnostics.markReady({ settings: true });
  await bindWindowLifecycle();
  try {
    const snapshot = await invoke("first_run_guide_get");
    if (snapshot?.completed === true) {
      window.location.replace("../settings/index.html");
      return;
    }
  } catch (error) {
    setAnimatedText(startupStatus, `无法读取首次启动状态：${String(error)}`);
  }
  await invoke("reveal_settings_window");
  firstUseButton.focus();
}

start().catch((error) => {
  setAnimatedText(startupStatus, `欢迎页暂时无法启动：${String(error)}`);
  invoke("reveal_settings_window").catch(() => {});
});
