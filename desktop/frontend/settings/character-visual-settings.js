import { normalizeVisualSettings } from "./visual-settings-runtime.js";

export function createCharacterVisualSettings({ document, invoke, refreshSelect, onDirty, openPlugin, reportError = () => {} }) {
  const select = document.getElementById("visualSelect");
  if (!select) return { refresh: async () => {}, sync() {}, discard() {}, isDirty: () => false, selections: () => ({}), committed() {}, dispose() {} };
  const status = document.getElementById("visualStatus");
  const message = document.getElementById("visualStatusText");
  const action = document.getElementById("visualPluginAction");
  const configure = document.getElementById("visualConfigure");
  const snapshots = new Map(), drafts = new Map();
  const pendingReads = new Map();
  let characterId = "", revision = 0, busy = false, locked = false, disposed = false, error = "";
  const savedSelection = id => snapshots.get(id)?.preferenceResourceId || snapshots.get(id)?.defaultResourceId || null;
  const preference = () => drafts.has(characterId) ? drafts.get(characterId) : savedSelection(characterId);
  function render() {
    const snapshot = snapshots.get(characterId);
    select.replaceChildren();
    if (!snapshot?.resources.length) {
      const empty = document.createElement("option"); empty.value = ""; empty.textContent = "暂无形态";
      select.append(empty);
    }
    for (const resource of snapshot?.resources || []) {
      const option = document.createElement("option"); option.value = resource.id;
      option.textContent = resource.name; select.append(option);
    }
    const selected = preference() || "";
    if (selected && !snapshot?.resources.some(item => item.id === selected)) {
      const removed = document.createElement("option"); removed.value = selected; removed.textContent = "所选形态已移除"; select.append(removed);
    }
    select.value = selected;
    select.disabled = locked || busy || !snapshot?.resources.length;
    const resource = snapshot?.resources.find(item => item.id === (selected || snapshot.defaultResourceId));
    const reasons = { PLUGIN_DISABLED: "所需插件尚未启用。", VISUAL_PROVIDER_MISSING: "尚未安装所需插件。", VISUAL_PROVIDER_SELECTION_REQUIRED: "多个插件支持此形态，请在角色工坊选择。", API_VERSION_UNSUPPORTED: "所需插件与当前版本不兼容。" };
    message.textContent = error || (resource && resource.reasonCode !== "READY" ? reasons[resource.reasonCode] || "所需插件暂不可用。" : selected && !resource ? "所选形态已移除，请重新选择。" : "");
    status.hidden = !message.textContent;
    action.hidden = !resource || resource.reasonCode === "READY";
    action.textContent = "查看插件";
    action.disabled = locked || busy;
    configure.disabled = locked || busy || !resource?.installId || resource.reasonCode !== "READY";
    refreshSelect(select);
  }
  async function refresh(target = characterId) {
    characterId = target || "";
    const requestedCharacterId = characterId;
    const current = ++revision;
    busy = Boolean(characterId); error = ""; render();
    if (!characterId) return;
    try {
      let pending = pendingReads.get(requestedCharacterId);
      if (!pending) {
        pending = Promise.resolve().then(() => invoke("settings_character_visuals_get", { characterId: requestedCharacterId }))
          .then(value => normalizeVisualSettings(value, requestedCharacterId));
        pendingReads.set(requestedCharacterId, pending);
        const clear = () => { if (pendingReads.get(requestedCharacterId) === pending) pendingReads.delete(requestedCharacterId); };
        pending.then(clear, clear);
      }
      const snapshot = await pending;
      if (disposed || current !== revision) return;
      snapshots.set(characterId, snapshot);
    } catch (failure) {
      if (!disposed && current === revision) {
        reportError(failure, { command: "settings_character_visuals_get", stage: "visual.settings.read", code: "VISUAL_SETTINGS_INVALID" });
        error = "显示方式读取失败，请重新打开设置。";
      }
    }
    finally { if (!disposed && current === revision) { busy = false; render(); onDirty(); } }
  }
  const resource = () => { const snapshot = snapshots.get(characterId); return snapshot?.resources.find(item => item.id === (preference() || snapshot.defaultResourceId)); };
  const change = () => { drafts.set(characterId, select.value || null); render(); onDirty(); };
  const showPlugin = () => openPlugin(resource()?.installId, false);
  const settings = () => openPlugin(resource()?.installId, true);
  select.addEventListener("change", change); action.addEventListener("click", showPlugin); configure.addEventListener("click", settings);
  const selections = () => Object.fromEntries([...drafts].filter(([id, value]) => value !== savedSelection(id)));
  return {
    refresh,
    sync(target, isLocked) { locked = isLocked; if ((target || "") !== characterId) void refresh(target); else render(); },
    isDirty: () => Object.keys(selections()).length > 0,
    selections,
    committed() { for (const [id, value] of drafts) { const snapshot = snapshots.get(id); if (snapshot) snapshots.set(id, { ...snapshot, preferenceResourceId: value }); } drafts.clear(); render(); },
    discard() { drafts.clear(); render(); },
    dispose() { disposed = true; revision++; select.removeEventListener("change", change); action.removeEventListener("click", showPlugin); configure.removeEventListener("click", settings); },
  };
}
