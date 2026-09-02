export function beginLegacyInspection(snapshot) {
  return {
    ...snapshot,
    state: "inspecting",
    stage: "inspecting",
    percent: 0,
    message: "正在扫描旧版本数据，文件较多时可能需要几分钟。",
  };
}

export function legacyInspectionProgress(snapshot) {
  if (snapshot?.state !== "inspecting") return null;
  return {
    indeterminate: true,
    percentText: "扫描中",
    progressValue: null,
    stageText: "正在检查旧版本数据",
  };
}
