const NORMAL_REASONS = new Set(["ACTIVE", "READY"]);
const STARTING_REASONS = new Set([
  "SESSION_NOT_READY",
  "STARTING",
  "WORKER_REBUILDING",
  "WORKER_STARTING",
]);
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
  const safeUnavailable = Array.isArray(unavailable)
    ? unavailable.filter((item) => typeof item === "string" && item)
    : [];

  if (NORMAL_REASONS.has(reasonCode) || ["active", "ready"].includes(state)) {
    return result("运行正常");
  }
  if (reasonCode === "PLUGIN_DISABLED" || state === "disabled") {
    return result("已停用");
  }
  if (STARTING_REASONS.has(reasonCode) || state === "starting") {
    return result("正在启动");
  }
  if (reasonCode === "WORKER_STOPPING" || state === "stopping") {
    return result("正在停止");
  }
  if (reasonCode === "API_VERSION_UNSUPPORTED") {
    return result(
      "版本不兼容",
      "这个插件版本与当前 Sakura 不兼容，无法使用。",
      reasonCode,
      safeUnavailable,
    );
  }
  if (MISSING_REASONS.has(reasonCode) || state === "waiting") {
    return result(
      "缺少所需组件",
      "缺少运行所需的组件，暂时无法使用。",
      reasonCode || "MISSING_SERVICE",
      safeUnavailable,
    );
  }
  if (CONFLICT_REASONS.has(reasonCode) || state === "conflict") {
    return result(
      "与其他插件冲突",
      "这个插件与其他插件冲突，暂时无法使用。",
      reasonCode || "SERVICE_CONFLICT",
      safeUnavailable,
    );
  }
  if (reasonCode === "DEPENDENCY_CYCLE") {
    return result(
      "启动失败",
      "几个插件互相依赖，无法启动。",
      reasonCode,
      safeUnavailable,
    );
  }
  if (reasonCode === "PLUGIN_MANIFEST_INVALID") {
    return result(
      "插件信息有误",
      "插件信息不完整或格式有误，无法使用。",
      reasonCode,
      safeUnavailable,
    );
  }
  if (reasonCode === "SETTINGS_LOAD_FAILED") {
    return result(
      "设置加载失败",
      "暂时无法读取这个插件的设置。",
      reasonCode,
      safeUnavailable,
    );
  }
  if (state === "degraded") {
    return result(
      "部分功能不可用",
      "这个插件没有完全启动，部分功能暂时不可用。",
      reasonCode || "PLUGIN_LOAD_PARTIAL",
      safeUnavailable,
    );
  }
  if (state === "stopped" || reasonCode === "WORKER_STOPPED") {
    return result(
      "已停止",
      "这个插件已停止运行。",
      reasonCode || "WORKER_STOPPED",
      safeUnavailable,
    );
  }
  return result(
    state === "failed" ? "启动失败" : "暂时无法使用",
    "这个插件暂时无法使用。",
    reasonCode || "STATUS_UNKNOWN",
    safeUnavailable,
  );
}

export function presentPluginReason(reasonCode = "") {
  if (!reasonCode || reasonCode === "READY") return null;
  if (reasonCode === "CONFIG_RELOAD_REQUIRED") {
    return result("需要重新加载", "保存后，重新加载插件或重启 Sakura 才会生效。");
  }
  return presentPluginStatus({ reasonCode });
}
