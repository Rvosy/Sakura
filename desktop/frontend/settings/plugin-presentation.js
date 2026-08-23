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

export function presentPluginStatus({ state = "", reasonCode = "" } = {}) {
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
      "缺少运行所需的组件，暂时无法使用。",
      reasonCode || "MISSING_SERVICE",
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

export function presentPluginReason(reasonCode = "") {
  if (!reasonCode || reasonCode === "READY") return null;
  return presentPluginStatus({ reasonCode });
}
