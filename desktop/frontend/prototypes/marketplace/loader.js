import { createState, plugin } from "../settings-demo/data.js";

let proposed = true;
let state;
const frame = document.getElementById("settings");
const loading = document.getElementById("loading");
async function makeState() {
  const next = createState("portrait");
  next.provider.schema_version = 2;
  next.provider.providers = [{ serviceKey: "sakura.model.openai-compatible", label: "OpenAI 兼容服务", profiles: [
    { profileId: "demo", label: "演示服务", models: [{ modelId: "demo-chat", label: "演示模型" }] },
  ] }];
  next.provider.model_slots = next.provider.model_slots.map(slot => ({ ...slot, selection: slot.required
    ? { serviceKey: "sakura.model.openai-compatible", profileId: "demo", modelId: "demo-chat" }
    : { serviceKey: "", profileId: "", modelId: "" } }));
  const response = await fetch("./installed-fixture.json");
  if (!response.ok) throw Error("无法读取插件示例清单");
  const manifests = await response.json();
  next.plugins = manifests.map(manifest => {
    const previous = next.plugins.find(p => p.pluginId === manifest.id);
    const p = previous || plugin(manifest.id, manifest.name, manifest.presentation.category, manifest.presentation.icon);
    Object.assign(p, {
      name: manifest.name, author: manifest.author, version: manifest.version,
      description: manifest.description, presentation: manifest.presentation,
      provides: manifest.provides, requires: manifest.requires,
      enabled: manifest.enabled, state: manifest.enabled ? "active" : "disabled",
      reasonCode: manifest.enabled ? "ACTIVE" : "PLUGIN_DISABLED",
    });
    if (p.pluginId === "sakura.visual.portrait") p.sections = [];
    return p;
  });
  return next;
}
window.marketReview = {
  get state() { return state; },
  get scenario() { return document.getElementById("scenario").value; },
  get proposed() { return proposed; },
  ready() { loading.hidden = true; },
  failed(error) { loading.hidden = false; loading.textContent = `Demo 载入失败：${error}`; },
  reconnect() { document.getElementById("scenario").value = "online"; },
  close() {
    loading.replaceChildren(); loading.hidden = false;
    const button = document.createElement("button"); button.textContent = "重新打开设置";
    button.onclick = load; loading.append(button);
  },
};
async function load() {
  loading.hidden = false; loading.textContent = "正在载入当前设置页面…";
  try {
    state ??= await makeState();
    const response = await fetch("../../settings/index.html", { cache: "no-store" });
    if (!response.ok) throw Error(`HTTP ${response.status}`);
    const doc = new DOMParser().parseFromString(await response.text(), "text/html");
    const base = doc.createElement("base"); base.href = new URL("../../settings/", location.href).href; doc.head.prepend(base);
    const controller = doc.querySelector('script[src="./settings.js"]');
    if (!controller || !doc.getElementById("page-plugins")) throw Error("正式设置页面接入点已变化");
    controller.remove();
    const script = doc.createElement("script"); script.type = "module"; script.src = new URL("./frame.js", location.href).href; doc.body.append(script);
    frame.srcdoc = "<!doctype html>\n" + doc.documentElement.outerHTML;
  } catch (error) { window.marketReview.failed(error.message); }
}
for (const name of ["proposed", "original"]) document.getElementById(name).onclick = () => {
  proposed = name === "proposed";
  for (const other of ["proposed", "original"]) document.getElementById(other).setAttribute("aria-pressed", String(other === name));
  document.getElementById("scenario").disabled = !proposed;
  load();
};
document.getElementById("scenario").onchange = () => frame.contentWindow.marketDemo?.scenarioChanged();
document.getElementById("reset").onclick = async () => { state = await makeState(); document.getElementById("scenario").value = "online"; load(); };
await load();
