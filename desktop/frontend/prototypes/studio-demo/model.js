import { clone, theme } from "../settings-demo/data.js";

export const visualTypes = [
  { id: "portrait", label: "立绘", plugin: "立绘插件", icon: "images", detail: "图片与表情", extension: ".png,.jpg,.jpeg,.webp" },
  { id: "live2d", label: "Live2D", plugin: "Live2D 插件", icon: "sparkles", detail: "Live2D 模型", extension: ".json" },
  { id: "vrm", label: "3D 模型", plugin: "3D 模型插件", icon: "layers", detail: "VRM 模型", extension: ".vrm" },
];
export const sampleImage = new URL("../asr/assets/navi.png", import.meta.url).href;
export const themeFields = [
  ["primary", "主色"], ["primaryHover", "主色悬停"], ["accent", "强调色"],
  ["text", "正文"], ["secondaryText", "次要文字"], ["mutedText", "辅助文字"],
  ["pageBackground", "页面背景"], ["panelBackground", "面板背景"],
  ["inputBackground", "输入框背景"], ["bubbleBackground", "气泡背景"], ["border", "边框"],
].map(([id, label]) => ({ id, label }));

export function makeVisual(type, name, id = crypto.randomUUID()) {
  return type === "portrait"
    ? { id, type, name, expressions: {}, defaultPortrait: "" }
    : { id, type, name, modelFile: "", autoBlink: true, breathing: true, idleMotion: "默认" };
}

export function makeDoc(id, displayName, withPortrait = false) {
  const visual = makeVisual("portrait", "日常立绘", `${id}-portrait`);
  if (withPortrait) {
    visual.expressions = { 默认: `portraits/${id}/default.png` };
    visual.defaultPortrait = `portraits/${id}/default.png`;
  }
  return {
    id, displayName, initialMessage: "早上好，今天想聊些什么？",
    cardText: `你是${displayName}，一位友善、好奇的伙伴。自然地交流，认真回应对方的问题。`,
    defaultPortrait: visual.defaultPortrait, expressions: clone(visual.expressions),
    visuals: withPortrait ? [visual] : [], defaultVisualId: withPortrait ? visual.id : "",
    voice: null, referenceAudios: [], replyTones: [], theme: clone(theme),
  };
}

export function createStudioState() {
  const docs = { navi: makeDoc("navi", "N.A.V.I.", true), sakura: makeDoc("sakura", "樱", true) };
  return {
    docs, published: clone(docs), selected: "navi", plugins: ["portrait", "live2d"],
    assets: Object.fromEntries(Object.values(docs).map(doc => [doc.defaultPortrait, sampleImage])),
    objectUrls: [], modelFiles: {}, calls: [],
  };
}

export function summaries(state) {
  return Object.values(state.docs).map(doc => ({
    id: doc.id, displayName: doc.displayName, isInstalled: Boolean(state.published[doc.id]),
    hasDraft: JSON.stringify(doc) !== JSON.stringify(state.published[doc.id]),
    isDirty: JSON.stringify(doc) !== JSON.stringify(state.published[doc.id]),
    source: state.published[doc.id] ? "installed" : "draft", draftKind: state.published[doc.id] ? "edit" : "new",
  }));
}

export function saveDraft(state, doc) {
  if (!state.docs[doc.id]) throw Error("角色草稿不存在。");
  state.docs[doc.id] = clone(doc);
}

export function publishDoc(state, doc) {
  saveDraft(state, doc);
  state.published[doc.id] = clone(doc);
}

export function discardDraft(state, id) {
  if (state.published[id]) state.docs[id] = clone(state.published[id]);
  else delete state.docs[id];
  return state.docs[id] || Object.values(state.docs)[0] || null;
}

export function toSettingsCharacters(state) {
  return Object.values(state.published).map(doc => ({
    id: doc.id, name: doc.displayName, hasVoice: Boolean(doc.voice),
    defaultVisual: doc.defaultVisualId,
    visuals: doc.visuals.map(v => ({
      id: v.id, type: v.type, label: v.name,
      pluginId: v.type === "portrait" ? "sakura.visual.portrait" : `example.${v.type}`,
    })),
  }));
}
