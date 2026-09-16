import { createBridge } from "./bridge.js";
import { createSurface } from "./surface.js";
const review = parent.__SAKURA_SETTINGS_REVIEW__;
// This comparison keeps the reviewed selector and its in-memory draft model.
document.getElementById("visualSetting")?.remove();
document.getElementById("visualStatus")?.remove();
const state = review.state;
const surface = createSurface(window, state, review.version);
window.__DEMO_SURFACE__ = surface;
const bridge = createBridge(window, state, {
  version: review.version,
  info: surface.info,
  studio: surface.studio,
  pluginChanged: surface.render,
  close: () => review.close(),
});
function reportFailure(message) {
  bridge.errors.push(message);
  if (document.body.dataset.demoReady !== "true") {
    review.failed("设置载入失败：" + message);
  }
}
window.addEventListener("error", (event) => reportFailure(event.message));
window.addEventListener("unhandledrejection", (event) =>
  reportFailure(String(event.reason)),
);
let source = await (
  await fetch(new URL("../../settings/settings.js", import.meta.url))
).text();
source = source.replace(/\r\n/g, "\n");
const path = new URL("../../settings/settings.js", import.meta.url);
source = source.replace(
  /(["'])(\.\.?\/[^"'\n]+)\1/g,
  (_m, q, p) => q + new URL(p, path).href + q,
);
function replaceOnce(from, to) {
  if (!source.includes(from))
    throw Error("Demo integration anchor changed: " + from);
  source = source.replace(from, to);
}
replaceOnce(
  "function computeDirty() {\n  return Boolean(",
  "function computeDirty() {\n  return Boolean(window.__DEMO_SURFACE__.isDirty() ||",
);
replaceOnce(
  "async function saveRuntimeSettings() {",
  "async function saveRuntimeSettings() {\n  await window.__DEMO_SURFACE__.validate();",
);
replaceOnce(
  "  const characterResult = await runtimeCharacterFeature?.commit();",
  "  await window.__DEMO_SURFACE__.save();\n  const characterResult = await runtimeCharacterFeature?.commit();",
);
replaceOnce(
  "      discard: async () => {",
  "      discard: async () => {\n        window.__DEMO_SURFACE__.discard();",
);
replaceOnce(
  "startSettingsFrontend()\n  .then(async () => {",
  "startSettingsFrontend()\n  .then(async () => {\n    window.__DEMO_SURFACE__.attach({refreshDirty,showPage,notify,refreshPlugins:()=>runtimePluginController?.refreshCurrent()});",
);
const blob = URL.createObjectURL(
  new Blob([source], { type: "text/javascript" }),
);
await import(blob);
URL.revokeObjectURL(blob);
const observer = new MutationObserver(() => {
  if (
    document.getElementById("pluginList")?.children.length &&
    document.getElementById("ttsProvider")?.children.length &&
    (review.version === "original" || document.getElementById("visualSelect"))
  ) {
    observer.disconnect();
    surface.render();
    document.body.dataset.demoReady = "true";
    review.ready();
  }
});
observer.observe(document.body, { childList: true, subtree: true });
setTimeout(() => {
  if (document.body.dataset.demoReady !== "true") {
    observer.disconnect();
    review.failed("设置尚未完整载入，请重置演示后重试。");
  }
}, 5000);
