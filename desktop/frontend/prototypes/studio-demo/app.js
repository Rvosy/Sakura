import { createStudioState, toSettingsCharacters } from "./model.js";
import { createState, clone, visualPlugin } from "../settings-demo/data.js";

const frame = document.getElementById("settings");
const loading = document.getElementById("loading");
let state = createStudioState();
let mode = "studio";
let previewState = null;
const requested = new URLSearchParams(location.search).get("character");
if (state.docs[requested]) state.selected = requested;

function closed() {
  loading.replaceChildren();
  const box = document.createElement("div"); box.className = "closed-state";
  const title = document.createElement("p"); title.textContent = "角色工坊已关闭";
  const button = document.createElement("button"); button.textContent = "重新打开工坊";
  button.onclick = () => load("studio");
  box.append(title, button); loading.append(box); loading.hidden = false;
}

window.__STUDIO_REVIEW__ = {
  get state() { return state; },
  ready() { loading.hidden = true; },
  failed(message) { loading.textContent = message; loading.hidden = false; },
  close: closed,
};
window.__SAKURA_SETTINGS_REVIEW__ = {
  get state() { return previewState; }, version: "proposed",
  ready() { loading.hidden = true; },
  failed(message) { loading.textContent = message; loading.hidden = false; },
  close() { load("studio"); },
  openStudio(id) { if (state.docs[id]) state.selected = id; return load("studio"); },
};

async function flush() {
  if (mode === "studio") await frame.contentWindow.__STUDIO_DEMO_SURFACE__?.flush();
}
function makeSettingsPreview() {
  const next = createState("multiple");
  next.characters = toSettingsCharacters(state);
  next.current = state.published[state.selected] ? state.selected : next.characters[0]?.id;
  next.plugins = next.plugins.filter(p => !["example.live2d", "example.vrm"].includes(p.pluginId));
  for (const type of state.plugins.filter(t => t !== "portrait")) next.plugins.push(visualPlugin(type));
  for (const c of next.characters) {
    const source = next.voices[c.id] || next.voices.navi;
    next.voices[c.id] = clone(source);
    next.memories[c.id] ||= [];
  }
  return next;
}
async function load(nextMode) {
  try {
    await flush();
    mode = nextMode;
    loading.textContent = mode === "studio" ? "正在载入角色工坊…" : "正在载入设置…";
    loading.hidden = false;
    document.getElementById("studioTab").classList.toggle("selected", mode === "studio");
    document.getElementById("settingsTab").classList.toggle("selected", mode === "settings");
    for (const id of ["studio", "settings"]) document.getElementById(`${id}Tab`).setAttribute("aria-pressed", String(id === mode));
    const entry = mode === "studio" ? "../../studio/" : "../../settings/";
    if (mode === "settings") previewState = makeSettingsPreview();
    const source = await (await fetch(new URL(mode === "studio" ? "./snapshot/index.html" : `${entry}index.html`, import.meta.url), { cache: "no-store" })).text();
    const doc = new DOMParser().parseFromString(source, "text/html");
    const base = doc.createElement("base"); base.href = new URL(entry, import.meta.url).href; doc.head.prepend(base);
    doc.querySelector(`script[src="./${mode === "studio" ? "studio" : "settings"}.js"]`).remove();
    const css = doc.createElement("link"); css.rel = "stylesheet";
    css.href = new URL(mode === "studio" ? "./surface.css" : "../settings-demo/surface.css", import.meta.url).href;
    doc.head.append(css);
    const script = doc.createElement("script"); script.type = "module";
    script.src = new URL(mode === "studio" ? "./frame.js" : "../settings-demo/frame.js", import.meta.url).href;
    doc.body.append(script);
    frame.title = mode === "studio" ? "Sakura 角色工坊" : "Sakura 设置预览";
    frame.srcdoc = "<!doctype html>\n" + doc.documentElement.outerHTML;
  } catch (error) { window.__STUDIO_REVIEW__.failed(`载入失败：${error.message}`); }
}
document.getElementById("studioTab").onclick = () => load("studio");
document.getElementById("settingsTab").onclick = () => load("settings");
document.getElementById("pluginScenario").onchange = async event => {
  await flush();
  state.plugins = event.target.value === "portrait" ? ["portrait"] : event.target.value === "live2d" ? ["portrait", "live2d"] : ["portrait", "live2d", "vrm"];
  await load(mode);
};
document.getElementById("resetDemo").onclick = async () => {
  await flush();
  state.objectUrls.forEach(url => URL.revokeObjectURL(url));
  state = createStudioState();
  document.getElementById("pluginScenario").value = "live2d";
  // The old frame belongs to the previous in-memory dataset.
  frame.srcdoc = "";
  await load("studio");
};
await load("studio");
