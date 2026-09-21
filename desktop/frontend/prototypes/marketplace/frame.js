import { createDemoMarketSource } from "./source.js";
import { createBridge } from "../settings-demo/bridge.js";

const review = parent.marketReview;
const state = review.state;
window.demoMarketSource = createDemoMarketSource(review);
const bridge = createBridge(window, state, {
  version: "current",
  close: () => review.close(),
  info: (title, text) => window.marketDemo?.info(title, text),
  pluginChanged: () => window.marketDemo?.sync(),
});
let downloadSources = [
  { name: "gitproxy.mrhjx.cn", prefix: "https://gitproxy.mrhjx.cn/", enabled: true },
  { name: "ghproxy.vip", prefix: "https://ghproxy.vip/", enabled: true },
  { name: "GitHub 官方", prefix: "", enabled: true },
];
const invoke = window.__TAURI__.core.invoke;
window.__TAURI__.core.invoke = async (name, args) => {
  if (name === "settings_download_sources_get") return structuredClone(downloadSources);
  if (name === "settings_download_sources_save") { downloadSources = structuredClone(args.value); return; }
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
let source = await (await fetch(new URL("../../settings/settings.js", import.meta.url), { cache: "no-store" })).text();
source = source.replace(/\r\n/g, "\n");
const path = new URL("../../settings/settings.js", import.meta.url);
source = source.replace(/(["'])(\.\.?\/[^"'\n]+)\1/g, (_m, q, p) => q + new URL(p, path).href + q);
const anchor = "startSettingsFrontend()\n  .then(async () => {";
if (!source.includes(anchor)) throw Error("正式设置控制器接入点已变化");
source = source.replace('source: createMarketplaceSource({ invoke, Channel: window.__TAURI__.core.Channel, host: runtimePluginController })', 'source: parent.marketReview.proposed ? window.demoMarketSource : null');
source = source.replace(anchor, anchor + '\n    window.marketHost = runtimePluginController;\n    window.marketNotify = notify;\n    window.marketDemo = runtimePluginMarketplace;\n    window.dispatchEvent(new Event("market-demo-ready"));');
window.addEventListener("market-demo-ready", async () => {
  try {
    document.querySelector('[data-page="plugins"]').click();
    window.marketDemo.scenarioChanged = () => { window.demoMarketSource.reset(); window.marketDemo.setView("market", { load: false }); void window.marketDemo.refresh(); };
    window.marketDemo.info = (_title, text) => window.marketNotify(text);
    window.marketDemo.setView("market");
    review.ready();
  } catch (error) { review.failed(error.message); }
}, { once: true });
const blob = URL.createObjectURL(new Blob([source], { type: "text/javascript" }));
await import(blob); URL.revokeObjectURL(blob);
window.marketBridge = bridge;
