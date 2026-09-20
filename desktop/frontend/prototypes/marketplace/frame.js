import { createBridge } from "../settings-demo/bridge.js";

const review = parent.marketReview;
const state = review.state;
const bridge = createBridge(window, state, {
  version: "current",
  close: () => review.close(),
  info: (title, text) => window.marketDemo?.info(title, text),
  pluginChanged: () => window.marketDemo?.sync(),
});
const invoke = window.__TAURI__.core.invoke;
window.__TAURI__.core.invoke = async (name, args) => {
  if (name === "settings_capability_manifest") {
    const manifest = await invoke(name, args);
    manifest.sections["open-help"] = { status: "unavailable", features: { "system.macos_open_help": "unavailable" } };
    return manifest;
  }
  if (name === "settings_character_visuals_get") return {
    characterId: args.characterId, defaultResourceId: "portrait-default", preferenceResourceId: null,
    resources: [{ id: "portrait-default", name: "默认立绘", label: "默认立绘", type: "portrait", reasonCode: "READY", installId: state.plugins.find(p => p.pluginId === "sakura.visual.portrait")?.installId }],
  };
  return invoke(name, args);
};
window.addEventListener("error", event => review.failed(event.message));
window.addEventListener("unhandledrejection", event => review.failed(String(event.reason)));

// The in-memory hook exposes the existing plugin feature to simulated installs.
// It does not change the shipping controller's rendering or business logic.
let source = await (await fetch(new URL("../../settings/settings.js", import.meta.url))).text();
source = source.replace(/\r\n/g, "\n");
const path = new URL("../../settings/settings.js", import.meta.url);
source = source.replace(/(["'])(\.\.?\/[^"'\n]+)\1/g, (_m, q, p) => q + new URL(p, path).href + q);
const anchor = "startSettingsFrontend()\n  .then(async () => {";
if (!source.includes(anchor)) throw Error("正式设置控制器接入点已变化");
source = source.replace(anchor, anchor + '\n    window.marketHost = runtimePluginController;\n    window.marketNotify = notify;\n    window.dispatchEvent(new Event("market-demo-ready"));');
window.addEventListener("market-demo-ready", async () => {
  try {
    document.querySelector('[data-page="plugins"]').click();
    if (review.proposed) {
      const response = await fetch(new URL("./surface.html", import.meta.url));
      if (!response.ok) throw Error("市场布局载入失败");
      const template = document.createElement("template"); template.innerHTML = await response.text();
      const page = document.getElementById("page-plugins");
      page.append(template.content.querySelector("#market-surface"));
      document.body.append(template.content);
      const tabs = document.createElement("div"); tabs.id = "market-tabs"; tabs.className = "plugin-role-tabs"; tabs.setAttribute("role", "tablist"); tabs.setAttribute("aria-label", "插件页面");
      tabs.innerHTML = '<button class="plugin-role-tab" id="installed-tab" role="tab" aria-selected="true" aria-controls="pluginList" data-view="installed">已安装</button><button class="plugin-role-tab" id="market-tab" role="tab" aria-selected="false" aria-controls="market-surface" tabindex="-1" data-view="market">市场</button>';
      page.querySelector(".plugin-page-heading > div").after(tabs);
      const css = document.createElement("link"); css.rel = "stylesheet"; css.href = new URL("./styles.css", import.meta.url).href; document.head.append(css);
      await import("./app.js");
    }
    review.ready();
  } catch (error) { review.failed(error.message); }
}, { once: true });
const blob = URL.createObjectURL(new Blob([source], { type: "text/javascript" }));
await import(blob); URL.revokeObjectURL(blob);
window.marketBridge = bridge;
