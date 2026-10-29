export function mount({ document, window, action, signal, isCurrent, draft, createIcon, onDataChanged }) {
  const element = document.createElement("div");
  element.className = "memory-text-import";
  const node = (tag, className = "", text = "") => {
    const item = document.createElement(tag); item.className = className; item.textContent = text; return item;
  };
  const style = new document.defaultView.CSSStyleSheet();
  style.replaceSync(`
    .memory-text-import { display:grid; gap:16px; min-width:0; }
    .memory-text-import p { margin:0; color:var(--text-secondary); line-height:1.6; }
    .memory-text-import label { display:grid; gap:7px; font-size:13px; min-width:0; }
    .memory-text-import textarea { width:100%; max-width:none; resize:vertical; min-height:112px; box-sizing:border-box; line-height:1.65; }
    .memory-text-import input:not([type=checkbox]), .memory-text-import select { width:100%; max-width:none; min-width:0; box-sizing:border-box; }
    .memory-text-import__actions { display:flex; flex-wrap:wrap; align-items:center; gap:10px; }
    .memory-text-import__actions button { display:flex; align-items:center; gap:7px; }
    .memory-text-import__candidates { display:grid; gap:14px; }
    .memory-text-import__candidate { display:grid; gap:14px; padding:18px; background:var(--bg-page, #f4f6f8); border-radius:14px; }
    .memory-text-import__candidate .memory-text-import__keep { display:flex; align-items:center; gap:9px; font-weight:600; }
    .memory-text-import__fields { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
    .memory-text-import__candidate textarea { min-height:76px; }
    .memory-text-import__candidate blockquote { margin:0; padding:10px 12px; background:var(--bg-card, #fff); border:0; border-radius:8px; white-space:pre-wrap; overflow-wrap:anywhere; color:var(--text-secondary); font-size:12px; line-height:1.7; }
    .memory-text-import details summary { cursor:pointer; color:var(--text-secondary); font-size:12px; }
    .memory-text-import details .memory-text-import__fields { margin-top:12px; }
    .memory-text-import__status { font-size:13px; white-space:pre-wrap; overflow-wrap:anywhere; }
    .memory-text-import [hidden] { display:none !important; }
    @media (max-width:800px) { .memory-text-import__fields { grid-template-columns:1fr; } }
  `);
  document.adoptedStyleSheets = [...document.adoptedStyleSheets, style];
  const description = node("p", "", "粘贴笔记、旧记忆正文或聊天片段。使用记忆整理模型提取，核对并勾选后保存。Sakura JSON 备份请使用下方的导入功能。");
  const sourceLabel = node("label", "", "要提取的文本");
  const source = node("textarea"); source.value = draft.text || "";
  source.placeholder = "例如：叫我小林。我不喝咖啡，喜欢绿茶。小雨是一起做项目的同事。";
  sourceLabel.append(source);
  const controls = node("div", "memory-text-import__actions");
  const extract = node("button", "", "提取候选"); extract.type = "button"; extract.prepend(createIcon(document, "sparkles"));
  const stop = node("button", "secondary-button", "停止提取"); stop.type = "button";
  controls.append(extract, stop);
  const status = node("p", "memory-text-import__status"); status.setAttribute("role", "status");
  const candidates = node("div", "memory-text-import__candidates");
  const footer = node("div", "memory-text-import__actions");
  const save = node("button", "", "保存所选记忆"); save.type = "button";
  const count = node("span", "setting-desc"); footer.append(save, count);
  element.append(description, sourceLabel, controls, status, candidates, footer);
  let revision = 0;
  let saving = false;
  const active = () => !signal.aborted && isCurrent();
  const call = async (name, request) => (await action(name, { request })).values.result;
  const kinds = [["person", "人物"], ["project", "项目"], ["place", "地点"], ["thing", "物品"], ["event", "事件"], ["topic", "话题"]];
  const layers = [["core_profile", "个人画像"], ["procedural", "相处约定"], ["semantic", "人与事"], ["episodic", "共同经历"], ["session", "当前事项"]];

  function sync() {
    const running = draft.state === "running";
    source.disabled = running || saving;
    extract.disabled = running || saving || !source.value.trim();
    stop.hidden = !running;
    footer.hidden = draft.state !== "preview" || !draft.candidates?.length;
    const selected = (draft.candidates || []).filter(item => item.selected).length;
    count.textContent = `已选 ${selected} / ${draft.candidates?.length || 0} 条`;
    save.disabled = saving || selected === 0;
    save.textContent = saving ? "正在保存…" : "保存所选记忆";
    status.textContent = draft.message || "";
  }

  function field(parent, item, key, label, options) {
    const wrapper = node("label", "", label);
    const input = node(options ? "select" : key === "content" ? "textarea" : "input");
    if (input.tagName === "INPUT") input.type = "text";
    if (options) for (const [value, text] of options) {
      const option = node("option", "", text); option.value = value; input.append(option);
    }
    input.value = item[key] || (options ? options[0][0] : "");
    input.addEventListener(options ? "change" : "input", () => { item[key] = input.value; });
    wrapper.append(input); parent.append(wrapper);
  }

  function renderCandidates() {
    candidates.replaceChildren();
    for (const item of draft.candidates || []) {
      const card = node("section", "memory-text-import__candidate");
      const keepLabel = node("label", "memory-text-import__keep");
      const keep = node("input"); keep.type = "checkbox"; keep.checked = item.selected;
      keep.addEventListener("change", () => { item.selected = keep.checked; sync(); });
      keepLabel.append(keep, "保留这条记忆"); card.append(keepLabel);
      const fields = node("div", "memory-text-import__fields");
      field(fields, item, "layer", "保存到", layers);
      field(fields, item, "subject", "关于谁或什么");
      field(fields, item, "predicate", "信息项或联系");
      field(fields, item, "target", "另一方（没有可留空）");
      card.append(fields);
      field(card, item, "content", "记忆内容");
      const details = node("details"); details.append(node("summary", "", "对象类型与事项状态"));
      const extra = node("div", "memory-text-import__fields");
      field(extra, item, "subject_kind", "对象类型", kinds);
      field(extra, item, "target_kind", "另一方类型", kinds);
      field(extra, item, "state", "事项状态", [["active", "有效或进行中"], ["completed", "已完成"], ["cancelled", "已取消"]]);
      details.append(extra); card.append(details);
      card.append(node("blockquote", "", `原文依据：${item.evidence.map(e => e.quote).join("；")}`));
      candidates.append(card);
    }
  }

  async function cancel() {
    revision++;
    if (draft.state !== "running") return;
    const requestId = draft.requestId;
    draft.state = "idle"; draft.message = "已停止提取，原文保留。";
    sync();
    try { await call("cancel", { requestId }); }
    catch (error) { if (active()) { draft.message = String(error); sync(); } }
  }

  source.addEventListener("input", () => {
    draft.text = source.value;
    draft.state = "idle"; draft.candidates = []; draft.message = "";
    candidates.replaceChildren(); sync();
  });
  stop.addEventListener("click", () => { void cancel(); });
  extract.addEventListener("click", async () => {
    const ticket = ++revision;
    const requestId = window.crypto.randomUUID();
    Object.assign(draft, { requestId, state: "running", candidates: [], message: "正在提取候选记忆…" });
    renderCandidates(); sync();
    try {
      let result = await call("extract", { requestId, text: source.value });
      while (active() && ticket === revision) {
        if (result.state !== "running") {
          if (result.state === "failed") throw new Error(result.error);
          Object.assign(draft, { state: "preview", candidates: result.candidates.map(item => ({ ...item, selected: true })),
            message: result.candidates.length ? "核对内容、对象和原文，取消勾选不需要的条目。保存后可在对应分类中查看。" : "没有提取到值得保留的内容。可以补充说明后再试。" });
          renderCandidates(); sync(); return;
        }
        await new Promise(resolve => window.setTimeout(resolve, 350));
        if (!active() || ticket !== revision) return;
        result = await call("status", { requestId });
      }
    } catch (error) {
      if (active() && ticket === revision) { draft.state = "failed"; draft.message = String(error); sync(); }
    }
  });
  save.addEventListener("click", async () => {
    saving = true; sync();
    for (const input of candidates.querySelectorAll("input, select, textarea")) input.disabled = true;
    try {
      const result = await call("save", { requestId: draft.requestId,
        candidates: draft.candidates.filter(item => item.selected) });
      onDataChanged();
      if (!active()) return;
      draft.state = "saved"; draft.message = `已保存 ${result.saved} 条记忆。`; draft.candidates = [];
      renderCandidates();
    } catch (error) {
      if (active()) draft.message = String(error);
    } finally {
      saving = false;
      if (active()) { for (const input of candidates.querySelectorAll("input, select, textarea")) input.disabled = false; sync(); }
    }
  });
  renderCandidates(); sync();
  return { element, cancel, dispose() {
    void cancel();
    document.adoptedStyleSheets = document.adoptedStyleSheets.filter(sheet => sheet !== style);
  } };
}
