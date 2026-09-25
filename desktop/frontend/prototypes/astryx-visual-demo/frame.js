import { createBridge } from "../settings-demo/bridge.js";
import { createSurface } from "../settings-demo/surface.js";

const review = parent.__SAKURA_ASTRYX_VISUAL_DEMO__;
const state = review.state;
const surface = createSurface(window, state, "proposed");

window.__DEMO_SURFACE__ = surface;

const bridge = createBridge(window, state, {
  version: "proposed",
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
  (_match, quote, relativePath) =>
    quote + new URL(relativePath, path).href + quote,
);

function replaceOnce(from, to) {
  if (!source.includes(from)) {
    throw Error("Demo integration anchor changed: " + from);
  }
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

const blob = URL.createObjectURL(new Blob([source], { type: "text/javascript" }));
await import(blob);
URL.revokeObjectURL(blob);

if (review.mode === "visual") {
  document.body.dataset.visualStudy = "astryx-inspired";

  const descriptions = new Map([
    ["角色与布局", "调整角色的表现方式、尺寸与桌面交互区域。"],
    ["外观", "让界面保持安静，把视觉焦点留给角色本身。"],
    ["模型服务", "管理 Sakura 连接的模型服务与连接配置。"],
    ["模型", "选择对话与任务所使用的模型能力。"],
    ["语音", "配置语音识别、语音合成与角色音色。"],
    ["记忆", "管理角色可以使用的记忆能力与数据来源。"],
    ["交互", "调整 Sakura 主动回应与你交互的方式。"],
    ["工具", "管理 Agent 可使用的工具与调用限制。"],
    ["系统", "管理启动、下载来源与其他系统行为。"],
    ["关于", "查看 Sakura 版本、组件与项目信息。"],
  ]);

  const title = document.getElementById("pageTitle");
  const subtitle = document.getElementById("pageSubtitle");

  const syncHeader = () => {
    if (!title || !subtitle) return;
    const text = descriptions.get(title.textContent.trim());
    if (text) {
      subtitle.textContent = text;
      subtitle.hidden = false;
    }
  };

  syncHeader();
  if (title) {
    new MutationObserver(syncHeader).observe(title, {
      childList: true,
      characterData: true,
      subtree: true,
    });
  }
}

const observer = new MutationObserver(() => {
  if (
    document.getElementById("pluginList")?.children.length &&
    document.getElementById("ttsProvider")?.children.length &&
    document.getElementById("visualSelect")
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
