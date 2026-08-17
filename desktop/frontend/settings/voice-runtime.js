const PROVIDERS = Object.freeze([
  Object.freeze({ id: "gpt-sovits", label: "GPT-SoVITS" }),
  Object.freeze({ id: "genie-tts", label: "Genie TTS" }),
]);

const RUNTIME_STATES = Object.freeze([
  "disabled", "waiting_for_session", "starting", "ready", "failed", "stopping",
]);
const PROVIDER_DEFAULTS = Object.freeze({
  "gpt-sovits": Object.freeze({ apiUrl: "http://127.0.0.1:9880/tts", customBaseUrl: "", ttsPath: "/tts", remoteReferenceRoot: "", workDir: "", pythonPath: "" }),
  "genie-tts": Object.freeze({ apiUrl: "http://127.0.0.1:9881/", customBaseUrl: "", ttsPath: "/tts", remoteReferenceRoot: "", workDir: "", pythonPath: "" }),
});

function exactKeys(value, keys, code) {
  if (!value || typeof value !== "object" || Object.keys(value).sort().join("|") !== [...keys].sort().join("|")) {
    throw new Error(code);
  }
  return value;
}

export function exactSettings(value) {
  exactKeys(value, [
    "enabled", "provider", "apiUrl", "customBaseUrl", "ttsPath", "remoteReferenceRoot",
    "workDir", "pythonPath", "timeoutSeconds",
  ], "TTS_SETTINGS_RESPONSE_INVALID");
  if (
    typeof value.enabled !== "boolean"
    || !PROVIDERS.some(({ id }) => id === value.provider)
    || typeof value.apiUrl !== "string"
    || typeof value.customBaseUrl !== "string"
    || typeof value.ttsPath !== "string"
    || typeof value.remoteReferenceRoot !== "string"
    || typeof value.workDir !== "string"
    || typeof value.pythonPath !== "string"
    || !Number.isSafeInteger(value.timeoutSeconds)
    || value.timeoutSeconds < 3
    || value.timeoutSeconds > 300
  ) throw new Error("TTS_SETTINGS_RESPONSE_INVALID");
  return Object.freeze({ ...value });
}

function settingsSignature(settings) {
  return JSON.stringify({
    enabled: settings.enabled,
    provider: settings.provider,
    apiUrl: settings.apiUrl,
    customBaseUrl: settings.customBaseUrl,
    ttsPath: settings.ttsPath,
    remoteReferenceRoot: settings.remoteReferenceRoot,
    workDir: settings.workDir,
    pythonPath: settings.pythonPath,
    timeoutSeconds: settings.timeoutSeconds,
  });
}

function exactTask(value) {
  if (value === null || value === undefined) return null;
  exactKeys(value, ["bundleKey", "cancellable", "error", "progress", "result", "state", "taskId"], "TTS_STATUS_RESPONSE_INVALID");
  if (
    typeof value.taskId !== "string"
    || typeof value.bundleKey !== "string"
    || !["starting", "running", "completed", "cancelled", "failed"].includes(value.state)
    || !Number.isSafeInteger(value.progress)
    || typeof value.cancellable !== "boolean"
  ) throw new Error("TTS_STATUS_RESPONSE_INVALID");
  return Object.freeze({ ...value });
}

function exactBundle(bundle, { recommendedRequired }) {
  const keys = ["installed", "key", "label", "provider", "size"];
  if (recommendedRequired) keys.push("recommended");
  exactKeys(bundle, keys, "TTS_STATUS_RESPONSE_INVALID");
  if (
    typeof bundle.key !== "string"
    || typeof bundle.label !== "string"
    || typeof bundle.provider !== "string"
    || typeof bundle.installed !== "boolean"
    || !Number.isSafeInteger(bundle.size)
    || (recommendedRequired && typeof bundle.recommended !== "boolean")
  ) throw new Error("TTS_STATUS_RESPONSE_INVALID");
  return Object.freeze(recommendedRequired
    ? { ...bundle, recommended: Boolean(bundle.recommended) }
    : { ...bundle });
}

// Kept for the existing bundle boundary tests; Runtime v2 UI now consumes the
// unified status DTO below.
export function exactBundleStatus(value) {
  exactKeys(value, ["activeTask", "bundles", "coreGenerationId", "windowGeneration"], "TTS_BUNDLE_RESPONSE_INVALID");
  if (!Array.isArray(value.bundles) || !Number.isSafeInteger(value.windowGeneration) || typeof value.coreGenerationId !== "string") {
    throw new Error("TTS_BUNDLE_RESPONSE_INVALID");
  }
  return Object.freeze({
    ...value,
    bundles: Object.freeze(value.bundles.map((bundle) => exactBundle(bundle, { recommendedRequired: false }))),
    activeTask: exactTask(value.activeTask),
  });
}

export function exactVoiceStatus(value) {
  exactKeys(value, [
    "schemaVersion", "enabled", "selectedProvider", "providers", "bundles", "runtime",
    "activeTask", "windowGeneration", "coreGenerationId",
  ], "TTS_STATUS_RESPONSE_INVALID");
  if (
    value.schemaVersion !== 1
    || typeof value.enabled !== "boolean"
    || !PROVIDERS.some(({ id }) => id === value.selectedProvider)
    || !Array.isArray(value.providers)
    || !Array.isArray(value.bundles)
    || !Number.isSafeInteger(value.windowGeneration)
    || typeof value.coreGenerationId !== "string"
  ) throw new Error("TTS_STATUS_RESPONSE_INVALID");
  const providers = value.providers.map((provider) => {
    exactKeys(provider, ["id", "label", "availability"], "TTS_STATUS_RESPONSE_INVALID");
    if (!PROVIDERS.some(({ id }) => id === provider.id) || !["installed", "not_installed", "unsupported", "configured"].includes(provider.availability)) {
      throw new Error("TTS_STATUS_RESPONSE_INVALID");
    }
    return Object.freeze({ ...provider });
  });
  exactKeys(value.runtime, ["provider", "endpointKind", "state", "errorCode", "updatedAt"], "TTS_STATUS_RESPONSE_INVALID");
  if (
    !PROVIDERS.some(({ id }) => id === value.runtime.provider)
    || !["managed", "custom"].includes(value.runtime.endpointKind)
    || !RUNTIME_STATES.includes(value.runtime.state)
    || (value.runtime.errorCode !== null && typeof value.runtime.errorCode !== "string")
    || typeof value.runtime.updatedAt !== "string"
  ) throw new Error("TTS_STATUS_RESPONSE_INVALID");
  return Object.freeze({
    ...value,
    providers: Object.freeze(providers),
    bundles: Object.freeze(value.bundles.map((bundle) => exactBundle(bundle, { recommendedRequired: true }))),
    runtime: Object.freeze({ ...value.runtime }),
    activeTask: exactTask(value.activeTask),
  });
}

function exactVoiceSnapshot(value) {
  exactKeys(value, ["coreGenerationId", "coreRestartRequired", "settings", "windowGeneration"], "TTS_SETTINGS_RESPONSE_INVALID");
  if (!Number.isSafeInteger(value.windowGeneration) || value.windowGeneration < 1 || typeof value.coreGenerationId !== "string" || !value.coreGenerationId || typeof value.coreRestartRequired !== "boolean") {
    throw new Error("TTS_SETTINGS_RESPONSE_INVALID");
  }
  return Object.freeze({ ...value, settings: exactSettings(value.settings) });
}

function exactSaveResult(value) {
  exactKeys(value, ["coreRestartRequired", "settings"], "TTS_SETTINGS_CHANGE_PLAN_INVALID");
  if (value.coreRestartRequired !== true) throw new Error("TTS_SETTINGS_CHANGE_PLAN_INVALID");
  return Object.freeze({ ...value, settings: exactSettings(value.settings) });
}

function transitionError(error) {
  const message = String(error?.message || error || "");
  return ["SETTINGS_CORE_GENERATION_MISMATCH", "SETTINGS_CORE_UNAVAILABLE", "CORE_RESTART", "CORE_GENERATION"]
    .some((code) => message.includes(code));
}

function statusLabel(status, ready, readyLabel = "已就绪") {
  if (status === "running") return "处理中";
  if (status === "failed") return "失败";
  if (status === "cancelled") return "可继续";
  return ready ? readyLabel : "未安装";
}

function statusClass(status, ready) {
  if (status === "running") return "is-running";
  if (status === "failed") return "is-failed";
  if (status === "cancelled") return "is-paused";
  return ready ? "is-ready" : "is-missing";
}

export function voiceResourcePresentation({ availability, taskState, runtimeFailed = false }) {
  const ready = ["installed", "configured"].includes(availability);
  return Object.freeze({
    status: ["starting", "running"].includes(taskState)
      ? "running"
      : taskState === "failed"
        ? "failed"
        : taskState === "cancelled"
          ? "cancelled"
          : "",
    ready,
    readyLabel: availability === "configured" ? "已配置" : "已安装",
    runtimeFailed: Boolean(runtimeFailed),
    installationFailed: taskState === "failed",
  });
}

function renderResourceCard(document, container, model) {
  if (!container) return;
  container.textContent = "";
  container.classList?.toggle?.("is-muted", Boolean(model.muted));
  container.classList?.toggle?.("is-running", model.status === "running");
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
  badge.className = `resource-badge ${statusClass(model.status, model.ready)}`;
  badge.textContent = statusLabel(model.status, model.ready, model.readyLabel);
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
    bar.style = bar.style || {};
    bar.style.width = `${Math.max(0, Math.min(100, Number(model.progress || 0)))}%`;
    progress.append(bar);
    body.append(progress);
  }
  if (model.select?.options?.length) {
    const row = document.createElement("label");
    row.className = "resource-select-row";
    const label = document.createElement("span");
    label.textContent = model.select.label;
    const select = document.createElement("select");
    select.disabled = Boolean(model.select.disabled);
    for (const optionModel of model.select.options) {
      const option = document.createElement("option");
      option.value = optionModel.value;
      option.textContent = optionModel.label;
      select.append(option);
    }
    select.value = model.select.value;
    select.addEventListener("change", () => model.select.onChange(select.value));
    row.append(label, select);
    body.append(row);
  }
  if (model.meta?.length) {
    const meta = document.createElement("dl");
    meta.className = "resource-meta";
    for (const [labelText, value] of model.meta) {
      if (!value) continue;
      const dt = document.createElement("dt");
      const dd = document.createElement("dd");
      dt.textContent = labelText;
      dd.textContent = value;
      meta.append(dt, dd);
    }
    body.append(meta);
  }
  const actions = document.createElement("div");
  actions.className = "resource-actions";
  for (const action of model.actions || []) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = action.primary ? "" : action.danger ? "danger-button" : "secondary-button";
    button.textContent = action.label;
    button.disabled = Boolean(action.disabled);
    button.addEventListener("click", action.onClick);
    actions.append(button);
  }
  if (actions.childNodes?.length || (model.actions || []).length) body.append(actions);
  container.append(head, body);
}

function availabilityText(provider) {
  return ({
    installed: "已安装",
    not_installed: "未安装",
    unsupported: "当前平台不支持",
    configured: "已配置",
    not_configured: "未配置",
  })[provider?.availability] || "未知";
}

export function createVoiceController({
  document,
  invoke,
  onDirty = () => {},
  onStatus = () => {},
  wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
}) {
  const fields = {
    enabled: document.getElementById("ttsEnabled"),
    provider: document.getElementById("ttsProvider"),
    apiUrl: document.getElementById("ttsApiUrl"),
    apiUrlRow: document.getElementById("ttsApiUrlRow"),
    customBaseUrl: document.getElementById("ttsCustomBaseUrl"),
    customBaseUrlRow: document.getElementById("ttsCustomBaseUrlRow"),
    ttsPath: document.getElementById("ttsPath"),
    ttsPathRow: document.getElementById("ttsPathRow"),
    remoteReferenceRoot: document.getElementById("ttsRemoteReferenceRoot"),
    remoteReferenceRootRow: document.getElementById("ttsRemoteReferenceRootRow"),
    workDir: document.getElementById("ttsWorkDir"),
    pythonPath: document.getElementById("ttsPythonPath"),
    timeout: document.getElementById("ttsTimeout"),
    test: document.getElementById("ttsTestButton"),
    bundle: document.getElementById("ttsResourceCard"),
  };
  let identity = null;
  let baseline = "";
  let disposed = false;
  let status = null;
  let statusTimer = null;
  let appliedTaskId = "";
  let selectedBundleKey = "";
  let rebindPromise = null;
  let testRunning = false;
  let lastProvider = "gpt-sovits";
  const providerDrafts = new Map();

  function draft() {
    return Object.freeze({
      enabled: Boolean(fields.enabled.checked),
      provider: fields.provider.value,
      apiUrl: fields.apiUrl.value.trim(),
      customBaseUrl: fields.customBaseUrl.value.trim(),
      ttsPath: fields.ttsPath.value.trim() || "/tts",
      remoteReferenceRoot: fields.remoteReferenceRoot.value.trim(),
      workDir: fields.workDir.value.trim(),
      pythonPath: fields.pythonPath.value.trim(),
      timeoutSeconds: Math.max(3, Math.min(300, Number.parseInt(fields.timeout.value, 10) || 60)),
    });
  }

  function signature() { return settingsSignature(draft()); }

  function syncEnabled() {
    // Provider/configuration/test remain available while chat TTS is off.
    for (const field of [fields.provider, fields.apiUrl, fields.customBaseUrl, fields.ttsPath, fields.remoteReferenceRoot, fields.workDir, fields.pythonPath, fields.timeout]) {
      field.disabled = false;
    }
    const gpt = fields.provider.value === "gpt-sovits";
    const custom = gpt && Boolean(fields.customBaseUrl.value.trim());
    fields.apiUrlRow.hidden = gpt;
    fields.customBaseUrlRow.hidden = !gpt;
    fields.ttsPathRow.hidden = !gpt;
    fields.remoteReferenceRootRow.hidden = !gpt;
    fields.remoteReferenceRoot.disabled = !gpt || !custom;
    fields.workDir.disabled = custom;
    fields.pythonPath.disabled = custom;
    fields.test.disabled = testRunning;
    fields.provider.__customSelect?.refresh?.();
  }

  function apply(settings) {
    fields.enabled.checked = settings.enabled;
    fields.provider.textContent = "";
    for (const provider of PROVIDERS) {
      const option = document.createElement("option");
      option.value = provider.id;
      option.textContent = provider.label;
      fields.provider.append(option);
    }
    fields.provider.value = settings.provider;
    fields.apiUrl.value = settings.apiUrl;
    fields.customBaseUrl.value = settings.customBaseUrl;
    fields.ttsPath.value = settings.ttsPath;
    fields.remoteReferenceRoot.value = settings.remoteReferenceRoot;
    fields.workDir.value = settings.workDir;
    fields.pythonPath.value = settings.pythonPath;
    fields.timeout.value = String(settings.timeoutSeconds);
    lastProvider = settings.provider;
    providerDrafts.set(lastProvider, {
      apiUrl: settings.apiUrl,
      customBaseUrl: settings.customBaseUrl,
      ttsPath: settings.ttsPath,
      remoteReferenceRoot: settings.remoteReferenceRoot,
      workDir: settings.workDir,
      pythonPath: settings.pythonPath,
    });
    syncEnabled();
  }

  function initialize(snapshot, { preserveDraft = false } = {}) {
    let currentDraft = null;
    if (preserveDraft && identity) {
      try { currentDraft = draft(); } catch { currentDraft = null; }
    }
    const next = exactVoiceSnapshot(snapshot);
    identity = Object.freeze({ windowGeneration: next.windowGeneration, coreGenerationId: next.coreGenerationId });
    apply(currentDraft || next.settings);
    baseline = settingsSignature(next.settings);
    status = null;
    stopPolling();
    if (fields.bundle) fields.bundle.textContent = "";
    onDirty();
  }

  async function bindCurrent(previousGeneration, { requireChange, preserveDraft }) {
    if (rebindPromise) return rebindPromise;
    // A settings restart received during Assistant initialization is deferred
    // until Core reaches stable readiness (bounded by the 30s lifecycle gate).
    const deadline = Date.now() + 35_000;
    rebindPromise = (async () => {
      let lastError = null;
      while (!disposed && Date.now() < deadline) {
        try {
          const next = exactVoiceSnapshot(await invoke("settings_voice_get"));
          if (!requireChange || next.coreGenerationId !== previousGeneration) {
            initialize(next, { preserveDraft });
            await refreshStatus().catch(() => {});
            return next;
          }
        } catch (error) { lastError = error; }
        await wait(100);
      }
      throw new Error(`TTS_CORE_RESTART_NOT_READY${lastError ? `: ${String(lastError)}` : ""}`);
    })().finally(() => { rebindPromise = null; });
    return rebindPromise;
  }

  function stopPolling() {
    if (statusTimer !== null) globalThis.clearInterval(statusTimer);
    statusTimer = null;
  }

  function syncPolling() {
    const running = testRunning
      || status?.runtime?.state === "starting"
      || ["starting", "running"].includes(status?.activeTask?.state);
    if (!running) {
      stopPolling();
      return;
    }
    if (statusTimer === null && !disposed) {
      statusTimer = globalThis.setInterval(() => refreshStatus().catch(() => {}), 1000);
    }
  }

  function providerStatus(providerId) {
    return status?.providers.find((provider) => provider.id === providerId) || null;
  }

  function providerBundles(providerId) {
    return status?.bundles.filter((bundle) => bundle.provider === providerId) || [];
  }

  function selectedBundle() {
    const bundles = providerBundles(fields.provider.value);
    let selected = bundles.find((bundle) => bundle.key === selectedBundleKey);
    if (!selected) selected = bundles.find((bundle) => bundle.recommended) || bundles[0] || null;
    if (selected) selectedBundleKey = selected.key;
    return selected;
  }

  function applyCompletedBundle(task) {
    if (!task || task.state !== "completed" || task.taskId === appliedTaskId || !task.result) return;
    appliedTaskId = task.taskId;
    if (PROVIDERS.some(({ id }) => id === task.result.provider)) fields.provider.value = task.result.provider;
    fields.workDir.value = String(task.result.workDir || "");
    fields.pythonPath.value = String(task.result.pythonPath || "");
    lastProvider = fields.provider.value;
    providerDrafts.set(lastProvider, {
      apiUrl: fields.apiUrl.value,
      customBaseUrl: fields.customBaseUrl.value,
      ttsPath: fields.ttsPath.value,
      remoteReferenceRoot: fields.remoteReferenceRoot.value,
      workDir: fields.workDir.value,
      pythonPath: fields.pythonPath.value,
    });
    syncEnabled();
    onDirty();
    onStatus("TTS 整合包已安装，配置已回填；启用并保存后随应用启动。", "success");
  }

  async function copyDiagnostic() {
    const diagnostic = JSON.stringify({
      errorCode: status?.runtime?.errorCode || status?.activeTask?.error?.code || "",
      provider: status?.runtime?.provider || fields.provider.value,
      endpointKind: status?.runtime?.endpointKind || "",
      state: status?.runtime?.state || status?.activeTask?.state || "",
      updatedAt: status?.runtime?.updatedAt || "",
    }, null, 2);
    try {
      await globalThis.navigator.clipboard.writeText(diagnostic);
      onStatus("诊断信息已复制。", "success");
    } catch {
      globalThis.prompt?.("复制以下诊断信息：", diagnostic);
    }
  }

  function renderStatus() {
    if (!fields.bundle || !status) return;
    const providerId = fields.provider.value;
    const provider = providerStatus(providerId);
    const customEndpoint = providerId === "gpt-sovits" && Boolean(fields.customBaseUrl.value.trim());
    const bundles = customEndpoint ? [] : providerBundles(providerId);
    const bundle = customEndpoint ? null : selectedBundle();
    const task = status.activeTask;
    const running = Boolean(task && ["starting", "running"].includes(task.state));
    const presentation = voiceResourcePresentation({
      availability: provider?.availability,
      taskState: task?.state,
      runtimeFailed: status.runtime.provider === providerId && status.runtime.state === "failed",
    });
    const { installationFailed, ready, readyLabel, runtimeFailed } = presentation;
    let message = `${provider?.label || providerId}：${availabilityText(provider)}。`;
    if (providerId === "gpt-sovits") {
      message += fields.customBaseUrl.value.trim()
        ? " 当前使用用户管理的自定义服务，Sakura 不会启动或停止该服务。"
        : " 当前使用 Sakura 内置 GPT-SoVITS。";
    }
    if (status.runtime.provider === providerId && status.enabled) {
      const runtimeText = ({
        waiting_for_session: "等待 Assistant session",
        starting: "后台启动中",
        ready: "运行服务已就绪",
        failed: `启动失败（${status.runtime.errorCode || "TTS_SERVICE_UNAVAILABLE"}）`,
        stopping: "正在停止",
        disabled: "已关闭",
      })[status.runtime.state];
      if (runtimeText) message += ` ${runtimeText}。`;
    }
    if (running) message = `正在安装 TTS 整合包 · ${task.progress}%`;
    const actions = [];
    if (bundle) {
      actions.push({
        label: bundle.installed ? "重新安装" : task?.state === "cancelled" ? "继续安装" : "安装",
        primary: !bundle.installed,
        disabled: running,
        onClick: async () => {
          try {
            await invoke("settings_voice_bundle_install", {
              windowGeneration: identity.windowGeneration,
              coreGenerationId: identity.coreGenerationId,
              bundleKey: bundle.key,
            });
            await refreshStatus();
          } catch (error) { onStatus(String(error || "整合包安装启动失败。"), "error"); }
        },
      });
    }
    if (running && task.cancellable) {
      actions.push({
        label: "暂停",
        danger: true,
        onClick: async () => {
          try {
            await invoke("settings_voice_bundle_cancel", {
              windowGeneration: identity.windowGeneration,
              coreGenerationId: identity.coreGenerationId,
              taskId: task.taskId,
            });
            await refreshStatus();
          } catch (error) { onStatus(String(error || "整合包暂停失败。"), "error"); }
        },
      });
    }
    if (runtimeFailed || installationFailed) actions.push({ label: "复制诊断", onClick: copyDiagnostic });
    actions.push({ label: "刷新", onClick: () => refreshStatus().catch((error) => onStatus(String(error), "error")) });
    renderResourceCard(document, fields.bundle, {
      title: `${provider?.label || "TTS"} 状态`,
      subtitle: fields.enabled.checked ? "聊天朗读已启用" : "可先配置、安装或测试",
      status: presentation.status,
      ready: presentation.ready,
      readyLabel: presentation.readyLabel,
      muted: false,
      message,
      detail: bundle ? `${bundle.label} · ${bundle.installed ? "已安装" : "未安装"}` : providerId === "gpt-sovits" && fields.customBaseUrl.value.trim() ? "自定义 Endpoint 仅进行连接与合成请求，不管理远端进程或模型。" : "当前平台没有可安装的整合包。",
      progressVisible: running,
      progress: task?.progress || 0,
      select: bundles.length > 1 ? {
        label: "整合包",
        value: bundle?.key || "",
        disabled: running,
        options: bundles.map((item) => ({ value: item.key, label: `${item.label}${item.installed ? "（已安装）" : ""}${item.recommended ? "（推荐）" : ""}` })),
        onChange: (value) => { selectedBundleKey = value; renderStatus(); },
      } : null,
      meta: status.providers.map((item) => [item.label, availabilityText(item)]),
      actions,
    });
    syncPolling();
  }

  async function refreshStatus({ confirmCompleted = true } = {}) {
    if (!identity || disposed) return null;
    const previousTaskState = status?.activeTask?.state || null;
    const next = exactVoiceStatus(await invoke("settings_voice_status_get"));
    if (next.windowGeneration !== identity.windowGeneration || next.coreGenerationId !== identity.coreGenerationId) {
      throw new Error("STALE_GENERATION");
    }
    status = next;
    applyCompletedBundle(status.activeTask);
    renderStatus();
    if (
      confirmCompleted
      && next.activeTask?.state === "completed"
      && ["starting", "running"].includes(previousTaskState)
    ) {
      await wait(0);
      return refreshStatus({ confirmCompleted: false });
    }
    return status;
  }

  function changed() { syncEnabled(); renderStatus(); onDirty(); }
  for (const field of [fields.enabled, fields.apiUrl, fields.customBaseUrl, fields.ttsPath, fields.remoteReferenceRoot, fields.workDir, fields.pythonPath, fields.timeout]) {
    field.addEventListener("input", changed);
    field.addEventListener("change", changed);
  }
  function providerChanged() {
    providerDrafts.set(lastProvider, {
      apiUrl: fields.apiUrl.value,
      customBaseUrl: fields.customBaseUrl.value,
      ttsPath: fields.ttsPath.value,
      remoteReferenceRoot: fields.remoteReferenceRoot.value,
      workDir: fields.workDir.value,
      pythonPath: fields.pythonPath.value,
    });
    const nextProvider = fields.provider.value;
    const next = providerDrafts.get(nextProvider) || PROVIDER_DEFAULTS[nextProvider];
    fields.apiUrl.value = next.apiUrl;
    fields.customBaseUrl.value = next.customBaseUrl;
    fields.ttsPath.value = next.ttsPath;
    fields.remoteReferenceRoot.value = next.remoteReferenceRoot;
    fields.workDir.value = next.workDir;
    fields.pythonPath.value = next.pythonPath;
    lastProvider = nextProvider;
    changed();
  }
  fields.provider.addEventListener("input", providerChanged);
  fields.provider.addEventListener("change", providerChanged);
  fields.test.addEventListener("click", async () => {
    if (!identity || disposed || testRunning) return;
    testRunning = true;
    syncEnabled();
    const original = fields.test.textContent;
    fields.test.textContent = "播放中…";
    syncPolling();
    try {
      const result = await invoke("settings_voice_test", {
        windowGeneration: identity.windowGeneration,
        coreGenerationId: identity.coreGenerationId,
        draft: draft(),
      });
      exactKeys(result, ["provider", "status", "errorCode"], "TTS_TEST_RESPONSE_INVALID");
      if (result.status === "finished") {
        onStatus(
          fields.provider.value === "gpt-sovits" && fields.customBaseUrl.value.trim()
            ? "自定义 GPT-SoVITS 服务已连接，测试语音播放完成。"
            : "Sakura 管理的 TTS 服务已就绪，测试语音播放完成。",
          "success",
        );
      } else if (result.status === "stopped") {
        onStatus("测试语音已停止。", "error");
      } else {
        onStatus(`测试语音失败：${result.errorCode || "AUDIO_PLAYBACK_FAILED"}`, "error");
      }
    } catch (error) {
      onStatus(String(error || "测试语音失败。"), "error");
    } finally {
      testRunning = false;
      fields.test.textContent = original;
      syncEnabled();
      await refreshStatus().catch(() => {});
    }
  });

  return Object.freeze({
    initialize,
    refreshBundles: refreshStatus,
    refreshStatus,
    isDirty: () => Boolean(identity) && signature() !== baseline,
    async save() {
      if (!identity || disposed) throw new Error("TTS_SETTINGS_NOT_READY");
      const previousGeneration = identity.coreGenerationId;
      try {
        exactSaveResult(await invoke("settings_voice_save", {
          windowGeneration: identity.windowGeneration,
          coreGenerationId: previousGeneration,
          draft: draft(),
        }));
      } catch (error) {
        if (transitionError(error)) await bindCurrent(previousGeneration, { requireChange: false, preserveDraft: true });
        throw error;
      }
      return bindCurrent(previousGeneration, { requireChange: true, preserveDraft: false });
    },
    refreshCurrent() {
      return bindCurrent(identity?.coreGenerationId || "", { requireChange: false, preserveDraft: true });
    },
    dispose() {
      disposed = true;
      identity = null;
      rebindPromise = null;
      stopPolling();
    },
  });
}
