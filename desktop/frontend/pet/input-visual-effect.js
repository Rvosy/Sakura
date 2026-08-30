const FALLBACK_NOTICE = "输入栏视觉效果不可用，已回退为纯色。请右键桌宠打开“运行日志”查看原因。";

export function inputVisualEffectFallbackNotice(values, status) {
  const requestedMode = values?.visualEffectMode || "solid";
  const effectiveMode = status?.effectiveMode || "solid";
  if (requestedMode === "solid" || effectiveMode !== "solid") return "";
  return FALLBACK_NOTICE;
}
