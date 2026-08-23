const NORMAL_REASONS = new Set(["ACTIVE", "READY"]);
const MISSING_REASONS = new Set([
  "DECLARED_SERVICE_MISSING",
  "MISSING_SERVICE",
  "SERVICE_MISSING",
]);
const CONFLICT_REASONS = new Set([
  "CONTEXT_PROVIDER_CONFLICT",
  "PLUGIN_ID_CONFLICT",
  "SERVICE_CONFLICT",
  "SETTINGS_SECTION_CONFLICT",
  "TOOL_NAME_CONFLICT",
]);
const ACTIVITY_STATE_PRIORITY = Object.freeze({
  neutral: 0,
  ready: 1,
  working: 2,
  warning: 3,
  error: 4,
});

function diagnostic(reasonCode, unavailable = []) {
  const parts = [`诊断代码：${reasonCode}`];
  if (unavailable.length) {
    parts.push(`缺少组件：${unavailable.join("、")}`);
  }
  return parts.join("；");
}

function result(label, message = "", reasonCode = "", unavailable = []) {
  return Object.freeze({
    label,
    message,
    diagnostic: reasonCode ? diagnostic(reasonCode, unavailable) : "",
  });
}

export function presentPluginStatus({ state = "", reasonCode = "", unavailable = [] } = {}) {
  if (NORMAL_REASONS.has(reasonCode) || state === "active") {
    return result("运行正常");
  }
  if (reasonCode === "PLUGIN_DISABLED" || state === "disabled") {
    return result("已停用");
  }
  if (reasonCode === "API_VERSION_UNSUPPORTED") {
    return result(
      "版本不兼容",
      "这个插件版本与当前 Sakura 不兼容，无法使用。",
      reasonCode,
    );
  }
  if (MISSING_REASONS.has(reasonCode)) {
    return result(
      "缺少所需组件",
      unavailable.length
        ? `缺少运行所需的组件：${unavailable.join("、")}。`
        : "缺少运行所需的组件，暂时无法使用。",
      reasonCode || "MISSING_SERVICE",
      unavailable,
    );
  }
  if (CONFLICT_REASONS.has(reasonCode)) {
    return result(
      "与其他插件冲突",
      "这个插件与其他插件冲突，暂时无法使用。",
      reasonCode || "SERVICE_CONFLICT",
    );
  }
  if (reasonCode === "DEPENDENCY_CYCLE") {
    return result(
      "启动失败",
      "几个插件互相依赖，无法启动。",
      reasonCode,
    );
  }
  if (reasonCode === "PLUGIN_MANIFEST_INVALID") {
    return result(
      "插件信息有误",
      "插件信息不完整或格式有误，无法使用。",
      reasonCode,
    );
  }
  if (reasonCode === "SETTINGS_LOAD_FAILED") {
    return result(
      "设置加载失败",
      "暂时无法读取这个插件的设置。",
      reasonCode,
    );
  }
  return result(
    state === "failed" ? "启动失败" : "暂时无法使用",
    "这个插件暂时无法使用。",
    reasonCode || "STATUS_UNKNOWN",
  );
}

function pluginId(plugin) {
  return plugin?.plugin_id || plugin?.pluginId || "";
}

function pluginInstallId(plugin) {
  return plugin?.id || plugin?.install_id || plugin?.installId || pluginId(plugin);
}

function serviceKeys(plugin, key) {
  const value = plugin?.[key];
  return Array.isArray(value) ? value : [];
}

function uniqueProvider(serviceKey, plugins, { runnableOnly = true } = {}) {
  const candidates = plugins.filter((candidate) => pluginId(candidate)
    && (!runnableOnly || candidate.supported !== false)
    && serviceKeys(candidate, "provides").includes(serviceKey));
  return candidates.length === 1 ? candidates[0] : null;
}

export function requiredPluginProviders(plugin, plugins = []) {
  const result = [];
  const visited = new Set([pluginId(plugin)]);
  function visit(consumer) {
    serviceKeys(consumer, "requires").forEach((serviceKey) => {
      const provider = uniqueProvider(serviceKey, plugins);
      const providerId = pluginId(provider);
      if (!provider || visited.has(providerId)) return;
      visited.add(providerId);
      visit(provider);
      result.push(provider);
    });
  }
  visit(plugin);
  return Object.freeze(result);
}

export function disabledRequiredPluginProviders(plugin, plugins = [], enabledById = {}) {
  return Object.freeze(requiredPluginProviders(plugin, plugins).filter((provider) => {
    const installId = pluginInstallId(provider);
    return Object.hasOwn(enabledById, installId)
      ? !enabledById[installId]
      : !provider.enabled;
  }));
}

export function enabledPluginDependents(plugin, plugins = [], enabledById = {}) {
  const targetId = pluginId(plugin);
  return Object.freeze(plugins.filter((candidate) => {
    if (!targetId || pluginId(candidate) === targetId) return false;
    const installId = pluginInstallId(candidate);
    const enabled = Object.hasOwn(enabledById, installId)
      ? Boolean(enabledById[installId])
      : Boolean(candidate.enabled);
    return enabled && requiredPluginProviders(candidate, plugins)
      .some((provider) => pluginId(provider) === targetId);
  }));
}

export function presentPluginComponent(serviceKey, plugins = []) {
  const provider = uniqueProvider(serviceKey, plugins, { runnableOnly: false });
  return provider
    ? `${provider.name || pluginId(provider)}（${serviceKey}）`
    : serviceKey;
}

export function presentPluginReason(reasonCode = "") {
  if (!reasonCode || reasonCode === "READY") return null;
  return presentPluginStatus({ reasonCode });
}

function pluginSections(plugin) {
  if (Array.isArray(plugin?.sections)) return plugin.sections;
  if (Array.isArray(plugin?.settings)) return plugin.settings;
  return [];
}

function projectedFieldValue(section, field) {
  if (section?.values && Object.hasOwn(section.values, field?.key)) {
    return section.values[field.key];
  }
  if (field && Object.hasOwn(field, "value")) return field.value;
  return null;
}

export function projectPluginActivity(plugin = {}) {
  const outerState = String(plugin?.state || "");
  if (outerState === "disabled") {
    return Object.freeze({
      state: "disabled",
      label: "已停用",
      message: "",
      hasRunningResource: false,
      isTransient: false,
    });
  }
  if (["failed", "stopped"].includes(outerState)) {
    return Object.freeze({
      state: "failed",
      label: "运行失败",
      message: "",
      hasRunningResource: false,
      isTransient: false,
    });
  }
  if (["starting", "waiting", "stopping"].includes(outerState)) {
    return Object.freeze({
      state: "working",
      label: "正在启动",
      message: "",
      hasRunningResource: false,
      isTransient: true,
    });
  }

  let projectedStatus = null;
  let hasRunningResource = false;
  pluginSections(plugin).forEach((section) => {
    (section.fields || []).forEach((field) => {
      const value = projectedFieldValue(section, field);
      if (field.type === "status" && Object.hasOwn(ACTIVITY_STATE_PRIORITY, value?.state)) {
        if (!projectedStatus
            || ACTIVITY_STATE_PRIORITY[value.state] > ACTIVITY_STATE_PRIORITY[projectedStatus.state]) {
          projectedStatus = value;
        }
      }
      if (field.type === "resource" && ["queued", "running"].includes(value?.taskState)) {
        hasRunningResource = true;
      }
    });
  });

  return Object.freeze({
    state: projectedStatus?.state || "neutral",
    label: String(projectedStatus?.label || ""),
    message: String(projectedStatus?.message || ""),
    hasRunningResource,
    isTransient: projectedStatus?.state === "working" || hasRunningResource,
  });
}
