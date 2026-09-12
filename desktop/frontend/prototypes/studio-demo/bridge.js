import { clone, theme } from "../settings-demo/data.js";
import { makeDoc, summaries, saveDraft, publishDoc, discardDraft, themeFields } from "./model.js";
import { chooseAsset, modal, element, button } from "./dialogs.js";

export function createStudioBridge(state, review) {
  const selectedAssets = new Map();
  const response = value => ({ schemaVersion: 1, ...value });
  const opened = doc => response({ workspaceId: `demo-${doc.id}`, doc: clone(doc), characters: summaries(state), modelFiles: state.modelFiles[doc.id] || [] });
  const idFor = workspace => workspace.replace(/^demo-/, "");
  function exportPreview() {
    const { body, actions, close } = modal("导出演示配置");
    body.append(element("p", "", "可下载本次演示的角色配置；演示配置不包含资源文件，也不能作为 .char 角色包导入。"));
    actions.append(button("取消", close), button("下载 JSON", () => {
      const doc = state.docs[state.selected];
      const url = URL.createObjectURL(new Blob([JSON.stringify(doc, null, 2)], { type: "application/json" }));
      const a = element("a"); a.href = url; a.download = `${doc.id}.studio-demo.json`; a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000); close();
    }, true));
  }
  const invoke = async (command, args = {}) => {
    state.calls.push(command === "studio_request" ? args.method : command);
    if (command === "studio_bootstrap") return response({
      selectedCharacterId: state.selected, currentCharacterId: "navi", characters: summaries(state),
      shellThemeTokens: clone(theme), themeDefaults: clone(theme), themeFields,
    });
    if (command === "show_studio") { review.ready(); return null; }
    if (command === "close_character_studio" || command === "close_character_studio_for_exit") { review.close(); return null; }
    if (command === "studio_choose_source") {
      const picked = await chooseAsset(state, args.kind);
      if (!picked?.length) return null;
      if (args.kind.endsWith("Folder")) {
        const key = crypto.randomUUID(); selectedAssets.set(key, picked); return key;
      }
      picked.forEach(asset => selectedAssets.set(asset.path, asset));
      return args.multiple ? picked.map(a => a.path) : picked[0].path;
    }
    if (command === "studio_choose_export") { await window.__STUDIO_DEMO_SURFACE__.flush(); exportPreview(); return null; }
    if (command === "studio_pick_screen_color") {
      const { body, actions, close } = modal("屏幕取色");
      body.append(element("p", "", "演示中可通过配色面板选择颜色。屏幕取色需要在桌面程序中使用。"));
      actions.append(button("关闭", close)); return response({ cancelled: true });
    }
    if (command !== "studio_request") throw Error(`未模拟的命令：${command}`);
    const { method, params: p } = args;
    if (method === "studio.character.open") {
      if (!state.docs[p.characterId]) throw Error("角色不存在。");
      state.selected = p.characterId; return opened(state.docs[p.characterId]);
    }
    if (method === "studio.character.create") {
      if (state.docs[p.doc.id]) throw Error("角色 ID 已存在。");
      const doc = makeDoc(p.doc.id, p.doc.displayName);
      state.docs[doc.id] = doc; state.selected = doc.id; return opened(doc);
    }
    if (method === "studio.draft.save") {
      saveDraft(state, p.doc); return opened(state.docs[p.doc.id]);
    }
    if (method === "studio.character.publish") {
      publishDoc(state, p.doc); return { ...opened(p.doc), changePlan: "unchanged", runtimeReload: "not_required" };
    }
    if (method === "studio.draft.discard") {
      const doc = discardDraft(state, idFor(p.workspaceId));
      state.selected = doc?.id || "";
      return doc ? opened(doc) : response({ characters: summaries(state) });
    }
    if (method === "studio.workspace.release") return response({ released: true });
    if (method === "studio.operation.cancel") return response({ cancelled: true });
    if (method === "studio.asset.import") {
      const asset = selectedAssets.get(p.path);
      if (!asset) throw Error("请先选择资源。");
      if (Array.isArray(asset)) return response({ items: asset.map(a => ({ relativePath: a.path, suggestedLabel: a.name.replace(/\.[^.]+$/, "") })) });
      if (["gptModel", "sovitsModel"].includes(p.kind)) {
        const id = idFor(p.workspaceId);
        state.modelFiles[id] ||= [];
        state.modelFiles[id].push({ relativePath: asset.path, byteLength: asset.byteLength });
      }
      return response({ relativePath: asset.path, suggestedLabel: asset.name.replace(/\.[^.]+$/, "") });
    }
    if (method === "studio.reference.preview") {
      const url = state.assets[p.relativePath];
      if (!url) throw Error("示例语音没有音频内容，请选择本地音频后试听。");
      return response({ previewUrl: url });
    }
    throw Error(`未模拟的工坊操作：${method}`);
  };
  window.__TAURI__ = { core: { invoke }, event: { listen: async () => () => {} } };
  return { invoke };
}
