export function requirementMessage(item) {
  const label = item.kind === "tts" ? "语音" : "角色形态";
  const candidates = item.candidates || [];
  const installed = candidates.filter(plugin => plugin.compatible);
  const names = plugins => [...new Set(plugins.map(plugin => plugin.name || plugin.id))].join(" 或 ");
  if (item.reasonCode === "COMPATIBLE") return `${label}：已启用兼容插件 ${names(installed.filter(plugin => plugin.enabled))}。`;
  if (item.reasonCode === "PLUGIN_DISABLED") return `${label}：请启用 ${names(installed)}。`;
  if (item.reasonCode === "PLUGIN_INCOMPATIBLE") return `${label}：已安装插件不兼容，请更新或安装兼容版本。`;
  const suggested = names(item.plugins || []);
  return suggested ? `${label}：尚未安装兼容插件，可安装 ${suggested}。` : `${label}：尚未安装支持 ${item.type} 的插件。`;
}
