export function requirementSummary(item) {
  const label = item.kind === "tts" ? "语音" : "角色形态";
  const installed = (item.candidates || []).filter(plugin => plugin.compatible);
  const names = plugins => [...new Set(plugins.map(plugin => plugin.name || plugin.id))];
  if (item.reasonCode === "COMPATIBLE") return {
    label, names: names(installed.filter(plugin => plugin.enabled)), status: "已启用", state: "enabled", detail: "",
  };
  if (item.reasonCode === "PLUGIN_DISABLED") return {
    label, names: names(installed), status: "未启用", state: "disabled", detail: "在设置中启用后可使用。",
  };
  if (item.reasonCode === "PLUGIN_INCOMPATIBLE") return {
    label, names: names(item.candidates || []), status: "不可用", state: "unsupported", detail: "请更新插件，或安装支持此资源的其他插件。",
  };
  const suggested = names(item.plugins || []);
  return {
    label, names: suggested, status: "未安装", state: "missing",
    detail: suggested.length > 1 ? "安装并启用其中一个即可。" : "安装并启用后可使用。",
  };
}

export function requirementMessage(item) {
  const summary = requirementSummary(item);
  const names = summary.names.join(summary.state === "missing" ? " 或 " : "、");
  return `${summary.label}：${names || "支持此资源的插件"}（${summary.status}）。${summary.detail}`;
}
