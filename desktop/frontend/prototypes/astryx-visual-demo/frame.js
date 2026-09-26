import { createBridge } from "../settings-demo/bridge.js";
import { field, section, plugin } from "../settings-demo/data.js";

const review = parent.__SAKURA_VISUAL_REVIEW__;
const state = review.state;
if (state.provider.schema_version !== 2) {
const connections = section("connections", "模型服务", [
  field("connections", "连接", structuredClone(state.provider.providers), "data"),
  field("probeRequest", "测试请求", {}, "data"),
  field("probeResult", "测试结果", {}, "data", { readonly: true }),
], "providers");
connections.presentation = { component: "connection-editor", serviceKey: "model.remote", valueField: "connections",
  requestField: "probeRequest", resultField: "probeResult", timeoutSection: "request", timeoutField: "timeout_seconds",
  probeAction: "probe", statusAction: "probeStatus", cancelAction: "cancelProbe" };
connections.actions = ["probe", "probeStatus", "cancelProbe"].map(actionId => ({ actionId, label: actionId }));
const request = section("request", "高级参数", [field("timeout_seconds", "请求超时时间", 60, "integer", { minimum: 1, maximum: 300, unit: "秒" })], "model");
request.presentation = { component: "form", collapsible: true };
state.plugins.push(plugin("sakura.model.openai_compatible", "OpenAI 兼容模型", "model", "brain", [connections, request], true, "infrastructure"));
state.provider = {
  ...state.provider,
  schema_version: 2,
  providers: [{ serviceKey: "model.remote", label: "远程模型", profiles: state.provider.providers.map(provider => ({
    profileId: provider.id, label: provider.alias,
    models: provider.models.map(modelId => ({ modelId, label: modelId })),
  })) }],
  model_slots: state.provider.model_slots.map(slot => ({ ...slot, selection: {
    serviceKey: slot.selection.profile_id ? "model.remote" : "",
    profileId: slot.selection.profile_id, modelId: slot.selection.model,
  } })),
};
}
function info(title, message) {
  if (review.options.version === "proposed" && window.__SAKURA_DEMO_INFO__) {
    window.__SAKURA_DEMO_INFO__(title, message);
    return;
  }
  const dialog = document.createElement("dialog");
  dialog.className = "demo-dialog";
  dialog.setAttribute("aria-label", title);
  const heading = document.createElement("h2");
  heading.textContent = title;
  const body = document.createElement("p");
  body.textContent = message;
  const footer = document.createElement("footer");
  const close = document.createElement("button");
  close.textContent = "关闭";
  close.onclick = () => dialog.close();
  footer.append(close);
  dialog.append(heading, body, footer);
  dialog.addEventListener("close", () => dialog.remove(), { once: true });
  document.body.append(dialog);
  dialog.showModal();
}

createBridge(window, state, {
  version: "proposed",
  info,
  studio: () => info("角色工坊", "此 demo 仅展示设置页，角色编辑请在 Sakura 中打开。"),
  close: () => review.close(),
  revealed: () => { void import("./dist/components.js").then(() => review.ready()).catch(error => review.failed(error.message)); },
});

// Adapt the older demo fixtures at the native transport boundary. The settings
// document and its production modules run unchanged in both visual modes.
const invoke = window.__TAURI__.core.invoke;
window.__TAURI__.core.Channel = class { onmessage = () => {}; };
window.__TAURI__.core.invoke = async (command, args = {}) => {
  if (["settings_chat_presentation_timing_save", "settings_bubble_auto_hide_save"].includes(command)) {
    return (await invoke(command, args)).values;
  }
  if (command === "settings_capability_manifest") {
    const manifest = await invoke(command, args);
    manifest.sections["open-help"] = { status: "unavailable", features: { "system.macos_open_help": "unavailable" } };
    return manifest;
  }
  if (command === "settings_marketplace_catalog") return { catalog: { schema_version: 1, plugins: [] }, context: { api: 4, services: [] }, readmes: [] };
  if (command === "settings_plugins_action" && args.pluginId === "sakura.model.openai_compatible") {
    const probe = args.values?.probeRequest;
    return { values: { probeResult: { requestId: probe?.requestId, state: "completed", models: ["deepseek-chat", "deepseek-reasoner"].map(modelId => ({ modelId })) } } };
  }
  if (command === "settings_plugins_save" && args.pluginId === "sakura.model.openai_compatible" && args.sectionId === "connections") {
    state.provider.providers[0].profiles = args.values.connections.map(item => ({ profileId: item.id, label: item.alias,
      models: item.models.map(modelId => ({ modelId, label: modelId })) }));
  }
  if (command === "settings_provider_model_save") {
    for (const slot of state.provider.model_slots) {
      if (args.draft.model_slots[slot.identity]) slot.selection = structuredClone(args.draft.model_slots[slot.identity]);
    }
    return { change_plan: "applied", save_state: "complete" };
  }
  if (command === "settings_character_visuals_get") {
    const character = state.characters.find(item => item.id === args.characterId);
    return {
      schemaVersion: 1,
      characterId: character.id,
      defaultResourceId: character.defaultVisual,
      preferenceResourceId: state.visuals[character.id] || null,
      resources: character.visuals.map(resource => {
        const plugin = state.plugins.find(item => item.pluginId === resource.pluginId);
        return { id: resource.id, name: resource.label, type: resource.type,
          installId: plugin?.installId || null,
          reasonCode: plugin?.enabled ? "READY" : plugin ? "PLUGIN_DISABLED" : "VISUAL_PROVIDER_MISSING" };
      }),
    };
  }
  if (command === "settings_character_select") Object.assign(state.visuals, args.visualSelections || {});
  if (command === "settings_download_sources_get") return state.downloadSources || [{ name: "官方源", prefix: "", enabled: true }];
  if (command === "settings_download_sources_save") { state.downloadSources = structuredClone(args.value); return null; }
  return invoke(command, args);
};

window.addEventListener("error", event => review.failed("设置载入失败：" + event.message));
window.addEventListener("unhandledrejection", event => review.failed("设置载入失败：" + String(event.reason)));
await import("../../settings/settings.js");
