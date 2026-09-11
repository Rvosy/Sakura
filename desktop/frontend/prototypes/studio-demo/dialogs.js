import { sampleImage } from "./model.js";

export function element(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (tag === "input") node.type = "text";
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}
export function button(text, action, primary = false) {
  const node = element("button", primary ? "primary-button" : "secondary-button", text);
  node.type = "button"; node.onclick = action;
  return node;
}
export function modal(title) {
  const previous = document.activeElement;
  const dialog = element("dialog", "studio-demo-dialog");
  const heading = element("h2", "", title);
  heading.id = `dialog-${crypto.randomUUID()}`;
  dialog.setAttribute("aria-labelledby", heading.id);
  const body = element("div", "studio-demo-dialog-body");
  const actions = element("div", "studio-demo-dialog-actions");
  dialog.append(heading, body, actions);
  document.body.append(dialog);
  dialog.addEventListener("close", () => {
    const restoreFocus = document.activeElement === document.body || dialog.contains(document.activeElement);
    dialog.remove();
    if (restoreFocus && previous?.isConnected) previous.focus();
  }, { once: true });
  dialog.showModal();
  return { dialog, body, actions, close: () => dialog.close() };
}

export function chooseAsset(state, kind, extension = "") {
  const portrait = kind === "portrait" || kind === "portraitFolder";
  const folder = kind.endsWith("Folder");
  const audio = kind.startsWith("referenceAudio");
  const title = portrait ? (folder ? "导入立绘文件夹" : "选择立绘") : audio ? "选择参考语音" : "选择模型";
  const sampleName = portrait ? "示例立绘.png" : audio ? "示例语音.wav" : kind === "live2d" ? "sample.model3.json" : kind === "vrm" ? "sample.vrm" : kind === "gptModel" ? "sample.ckpt" : "sample.pth";
  const { dialog, body, actions, close } = modal(title);
  const resourceName = (document.getElementById("visualName")?.value || "资源").replace(/[\\/]/g, "_");
  const folderName = `${portrait ? "portraits" : audio ? "voice/refs" : "models"}/${state.selected}/${resourceName}`;
  function relativePath(name) {
    let path = `${folderName}/${name}`, index = 2;
    const dot = name.lastIndexOf(".");
    const stem = dot > 0 ? name.slice(0, dot) : name;
    const suffix = dot > 0 ? name.slice(dot) : "";
    while (Object.hasOwn(state.assets, path)) {
      path = `${folderName}/${stem}-${index++}${suffix}`;
    }
    return path;
  }
  const sample = element("div", "sample-asset");
  if (portrait) { const img = element("img"); img.src = sampleImage; img.alt = "示例立绘"; sample.append(img); }
  sample.append(element("strong", "", folder ? "示例立绘文件夹" : sampleName));
  body.append(sample);
  const input = element("input"); input.type = "file"; input.hidden = true;
  input.accept = extension || (portrait ? ".png,.jpg,.jpeg,.webp" : audio ? ".wav,.mp3,.ogg" : kind === "gptModel" ? ".ckpt" : ".pth");
  if (folder) { input.webkitdirectory = true; input.multiple = true; }
  body.append(input);
  return new Promise(resolve => {
    let result = null;
    dialog.addEventListener("close", () => resolve(result), { once: true });
    input.onchange = () => {
      const files = Array.from(input.files || []).filter(file => !portrait || /\.(png|jpe?g|webp)$/i.test(file.name));
      if (!files.length) return;
      result = files.map(file => {
        const path = relativePath(file.name);
        const url = parent.URL.createObjectURL(file); state.objectUrls.push(url); state.assets[path] = url;
        return { path, name: file.name, byteLength: file.size };
      });
      close();
    };
    actions.append(button("取消", close), button(folder ? "选择本地文件夹" : "选择本地文件", () => input.click()), button("使用示例资源", () => {
      const labels = folder && portrait ? ["默认", "开心", "惊讶"] : [portrait ? "默认" : sampleName];
      result = labels.map(label => {
        const path = relativePath(portrait ? label + ".png" : sampleName);
        if (portrait) state.assets[path] = sampleImage;
        return { path, name: portrait ? label + ".png" : sampleName, byteLength: 0 };
      });
      close();
    }, true));
  });
}

export function confirmRemoval(name, detail) {
  const { dialog, body, actions, close } = modal(`移除「${name}」？`);
  body.append(element("p", "", detail));
  return new Promise(resolve => {
    let accepted = false;
    dialog.addEventListener("close", () => resolve(accepted), { once: true });
    actions.append(button("取消", close), button("移除", () => { accepted = true; close(); }, true));
  });
}
