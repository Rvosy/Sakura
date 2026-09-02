const clone = (value) => JSON.parse(JSON.stringify(value));

export function autostartErrorMessage(error) {
  const value = String(error ?? "");
  if (value.includes("AUTOSTART_SETTINGS_READ_FAILED")) {
    return "无法读取系统的开机启动状态。";
  }
  if (value.includes("AUTOSTART_SETTINGS_UPDATE_FAILED")) {
    return "无法修改开机启动设置，请检查系统权限后重试。";
  }
  if (value.includes("AUTOSTART_SETTINGS_VERIFY_FAILED")) {
    return "系统没有确认开机启动设置，请重试。";
  }
  return value || "开机启动设置操作失败。";
}

export function validateAutostartSnapshot(snapshot) {
  const keys = snapshot && typeof snapshot === "object" ? Object.keys(snapshot).sort() : [];
  const expected = ["launchAtLogin", "schemaVersion", "windowGeneration"];
  if (
    snapshot?.schemaVersion !== 1
    || !Number.isSafeInteger(snapshot.windowGeneration)
    || snapshot.windowGeneration < 1
    || typeof snapshot.launchAtLogin !== "boolean"
    || keys.length !== expected.length
    || keys.some((key, index) => key !== expected[index])
  ) throw new Error("不支持的开机启动设置响应");
  return Object.freeze({ ...snapshot });
}

export function createAutostartSettingsController({ document, invoke, onDirty }) {
  const control = document.getElementById("launchAtLogin");
  let snapshot = null;
  let baseline = null;
  let draft = null;
  let disposed = false;

  function changed() {
    if (disposed || !snapshot) return;
    draft = Boolean(control.checked);
    onDirty();
  }

  function fill(value) {
    control.checked = value;
    control.disabled = false;
  }

  return Object.freeze({
    initialize(input) {
      snapshot = validateAutostartSnapshot(input);
      baseline = snapshot.launchAtLogin;
      draft = baseline;
      fill(draft);
      control.addEventListener("change", changed);
      onDirty();
    },
    isDirty: () => baseline !== null && draft !== baseline,
    async save() {
      if (!snapshot) throw new Error("开机启动设置尚未加载");
      draft = Boolean(control.checked);
      let response;
      try {
        response = await invoke("settings_autostart_save", {
          windowGeneration: snapshot.windowGeneration,
          launchAtLogin: draft,
        });
      } catch (error) {
        throw new Error(autostartErrorMessage(error));
      }
      const result = validateAutostartSnapshot(response);
      snapshot = result;
      baseline = result.launchAtLogin;
      draft = baseline;
      fill(draft);
      onDirty();
      return clone(result);
    },
    discard() {
      if (baseline === null) return;
      draft = baseline;
      fill(draft);
      onDirty();
    },
    dispose() {
      disposed = true;
      control.removeEventListener("change", changed);
    },
  });
}
