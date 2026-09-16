import { createIcon } from "../../core/icons.js";
import { clone } from "../settings-demo/data.js";
import { visualTypes } from "./model.js";
import { element, button, modal, chooseAsset, confirmRemoval } from "./dialogs.js";

export function createStudioSurface(state) {
  let api, characterId = "", resources = [], selectedId = "", defaultId = "", renderPortraits;
  let library, cards, meta, modelEditor, missing, portraitEditor, empty;
  const selected = () => resources.find(v => v.id === selectedId);
  const descriptor = type => visualTypes.find(t => t.id === type);
  const available = visual => Boolean(visual && state.plugins.includes(visual.type));

  function init() {
    const page = document.getElementById("page-portrait");
    document.querySelector('[data-page="portrait"] .nav-item-label').textContent = "角色形态";
    portraitEditor = page.querySelector("fieldset");
    portraitEditor.classList.add("portrait-editor");
    library = element("div", "forms-library");
    const head = element("div", "forms-library-head");
    head.append(element("span", "forms-library-title", "角色包中的形态"), button("添加形态", addVisual, true));
    head.lastElementChild.id = "addVisualButton";
    cards = element("div", "form-cards"); cards.setAttribute("aria-label", "角色形态列表");
    library.append(head, cards);
    meta = element("div", "form-meta");
    missing = element("div", "form-plugin-missing"); missing.setAttribute("role", "status");
    modelEditor = element("fieldset", "settings-group model-editor");
    empty = element("div", "forms-empty");
    empty.append(createIcon(document, "images"), element("h2", "", "还没有角色形态"), button("添加形态", addVisual, true));
    page.prepend(library, meta, missing, empty);
    page.append(modelEditor);
    document.getElementById("addExpressionButton").textContent = "添加图片";
  }

  function capture() {
    if (!api || !characterId) return;
    const doc = api.collect();
    resources = clone(doc.visuals || []);
    defaultId = doc.default_visual_id;
  }
  function changed() { api.changed(); }
  function icon(type) { return createIcon(document, descriptor(type)?.icon || "images"); }
  function imageUrl(visual) { return state.assets[visual?.default_portrait] || ""; }

  function renderCards() {
    cards.replaceChildren();
    for (const visual of resources) {
      const card = button("", () => {
        capture(); selectedId = visual.id; renderSelected();
      });
      card.className = "form-card";
      card.dataset.visualId = visual.id;
      card.setAttribute("aria-pressed", String(visual.id === selectedId));
      const cover = element("span", `form-card-cover type-${visual.type}`);
      const url = imageUrl(visual);
      if (url) { const img = element("img"); img.src = url; img.alt = ""; cover.append(img); }
      else cover.append(icon(visual.type));
      const text = element("span", "form-card-text");
      text.append(element("strong", "", visual.name || "未命名形态"), element("span", "form-card-type", descriptor(visual.type)?.label || visual.type));
      const badges = element("span", "form-card-badges");
      if (visual.id === defaultId) badges.append(element("span", "form-default-badge", "默认"));
      if (!available(visual)) badges.append(element("span", "form-missing-badge", "缺少插件"));
      text.append(badges);
      card.append(cover, text);
      cards.append(card);
    }
  }

  function renderMeta(visual) {
    meta.replaceChildren();
    const field = element("label", "form-name-field");
    field.append(element("span", "", "形态名称"));
    const input = element("input"); input.id = "visualName"; input.value = visual.name; input.maxLength = 40;
    input.oninput = () => { selected().name = input.value; changed(); };
    field.append(input);
    const actions = element("div", "form-meta-actions");
    actions.append(element("span", "form-plugin-label", descriptor(visual.type)?.plugin || "所需插件"));
    const makeDefault = button(visual.id === defaultId ? "默认形态" : "设为默认", () => {
      capture(); defaultId = selectedId; changed(); renderSelected();
    });
    makeDefault.disabled = visual.id === defaultId;
    makeDefault.id = "defaultVisualButton";
    const remove = button("移除", async () => {
      const next = resources.find(v => v.id !== selectedId);
      const consequence = visual.id === defaultId && next ? `默认形态将改为「${next.name}」。` : !next ? "移除后需要添加一种形态才能保存角色。" : "";
      if (!await confirmRemoval(visual.name, `只移除此形态，角色人设和其他形态会保留。${consequence}`)) return;
      capture(); resources = resources.filter(v => v.id !== selectedId);
      if (defaultId === selectedId) defaultId = resources[0]?.id || "";
      selectedId = resources[0]?.id || "";
      renderSelected(); changed();
    });
    remove.id = "removeVisualButton";
    actions.append(makeDefault, remove);
    meta.append(field, actions);
  }

  function decorateExpressions() {
    const visual = selected();
    if (visual?.type !== "portrait") return;
    const rows = document.querySelectorAll("#expressionList .expression-row");
    for (const row of rows) {
      let thumbnail = row.querySelector(".expression-thumbnail");
      if (!thumbnail) {
        thumbnail = button("", () => {
          const path = row.querySelector("[data-expression-path]").value;
          const label = row.querySelector("[data-expression-label]").value || "立绘";
          const { body, actions, close } = modal(label);
          const img = element("img", "full-portrait-preview"); img.src = state.assets[path] || ""; img.alt = label;
          body.append(img); actions.append(button("关闭", close));
        });
        thumbnail.className = "expression-thumbnail";
        row.prepend(thumbnail);
      }
      const label = row.querySelector("[data-expression-label]").value;
      thumbnail.setAttribute("aria-label", `预览${label || "立绘"}`);
      const img = element("img"); img.alt = "";
      const path = row.querySelector("[data-expression-path]").value;
      if (state.assets[path]) { img.src = state.assets[path]; thumbnail.replaceChildren(img); }
      else thumbnail.replaceChildren(icon("portrait"));
      row.querySelector(".icon-button").setAttribute("aria-label", `移除图片${label || "立绘"}`);
      row.querySelector("[data-expression-label]").setAttribute("aria-label", "表情标签");
      row.querySelector("[data-expression-path]").setAttribute("aria-label", "图片文件");
    }
  }

  function renderModel(visual) {
    modelEditor.replaceChildren(element("legend", "", descriptor(visual.type)?.label || "模型"));
    const row = element("div", "model-file-control");
    const label = element("label", "setting-title", "模型文件"); label.htmlFor = "visualModelPath";
    const path = element("input"); path.id = "visualModelPath"; path.readOnly = true;
    path.value = visual.model_file || ""; path.placeholder = visual.type === "live2d" ? "选择 .model3.json 文件" : "选择 .vrm 文件";
    const choose = button("选择模型", async () => {
      const type = descriptor(visual.type);
      const picked = await chooseAsset(state, visual.type, type.extension);
      if (!picked?.[0]) return;
      selected().model_file = picked[0].path; changed(); renderModel(selected());
    });
    choose.id = "chooseVisualModel";
    row.append(label, path, choose); modelEditor.append(row);
    const preview = element("div", "model-cover");
    preview.append(icon(visual.type), element("strong", "", visual.model_file ? visual.model_file.split("/").pop() : "尚未选择模型"));
    preview.append(element("span", "", "模型预览将在对应插件中提供"));
    modelEditor.append(preview);
    const controls = element("div", "model-options");
    for (const [key, title] of [["auto_blink", "自动眨眼"], ["breathing", "呼吸动作"]]) {
      const toggle = element("label", "model-toggle");
      const input = element("input"); input.type = "checkbox"; input.checked = visual[key] !== false;
      input.onchange = () => { selected()[key] = input.checked; changed(); };
      toggle.append(element("span", "", title), input); controls.append(toggle);
    }
    modelEditor.append(controls);
    modelEditor.querySelectorAll("input, button").forEach(control => control.disabled = !available(visual));
  }

  function renderSelected() {
    const visual = selected();
    renderCards();
    library.hidden = !resources.length;
    empty.hidden = Boolean(visual);
    meta.hidden = !visual;
    portraitEditor.hidden = visual?.type !== "portrait";
    modelEditor.hidden = !visual || visual.type === "portrait";
    missing.hidden = !visual || available(visual);
    if (!visual) { renderPortraits({}, ""); return; }
    renderMeta(visual);
    if (!available(visual)) {
      missing.replaceChildren(icon(visual.type), element("span", "", `${descriptor(visual.type)?.plugin || "所需插件"}未启用。资源会保留，启用后可继续编辑。`));
    }
    if (visual.type === "portrait") {
      renderPortraits(visual.expressions || {}, visual.default_portrait || "");
      decorateExpressions();
      portraitEditor.querySelectorAll("input, button").forEach(control => control.disabled = !available(visual));
    } else renderModel(visual);
  }

  function addVisual() {
    capture();
    const { body, actions, close } = modal("添加形态");
    const types = element("div", "visual-type-options");
    types.setAttribute("role", "group"); types.setAttribute("aria-label", "形态类型");
    let chosen = state.plugins[0];
    const nameLabel = element("label", "modal-field"); nameLabel.append(element("span", "", "形态名称"));
    const name = element("input"); name.maxLength = 40; name.id = "newVisualName";
    const uniqueName = type => {
      const base = descriptor(type).label;
      let candidate = base, n = 2;
      while (resources.some(v => v.name === candidate)) candidate = `${base} ${n++}`;
      return candidate;
    };
    name.value = uniqueName(chosen);
    nameLabel.append(name);
    for (const type of visualTypes.filter(t => state.plugins.includes(t.id))) {
      const option = button("", () => {
        chosen = type.id; name.value = uniqueName(chosen);
        types.querySelectorAll("button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.type === chosen)));
      });
      option.className = "visual-type-option"; option.dataset.type = type.id;
      option.setAttribute("aria-pressed", String(chosen === type.id));
      option.append(icon(type.id), element("strong", "", type.label), element("span", "", type.detail));
      types.append(option);
    }
    const error = element("p", "modal-error"); error.setAttribute("role", "alert");
    body.append(types, nameLabel, error);
    const commit = () => {
      const title = name.value.trim();
      if (!title) { error.textContent = "请输入形态名称。"; name.focus(); return; }
      if (resources.some(v => v.name === title)) { error.textContent = "已有同名形态，请换一个名称。"; name.focus(); return; }
      const visual = { id: crypto.randomUUID(), type: chosen, name: title };
      if (chosen === "portrait") Object.assign(visual, { expressions: {}, default_portrait: "" });
      else Object.assign(visual, { model_file: "", auto_blink: true, breathing: true });
      resources.push(visual); selectedId = visual.id; defaultId ||= visual.id;
      close(); renderSelected(); changed();
      (chosen === "portrait" ? document.getElementById("addExpressionButton") : document.getElementById("chooseVisualModel")).focus();
    };
    name.onkeydown = event => { if (event.key === "Enter") { event.preventDefault(); commit(); } };
    actions.append(button("取消", close), button("添加", commit, true));
    name.focus(); name.select();
  }

  function collectPortrait(expressions, defaultPortrait) {
    const visuals = resources.map(v => v.id === selectedId && v.type === "portrait"
      ? { ...clone(v), expressions, default_portrait: defaultPortrait } : clone(v));
    const primary = visuals.find(v => v.id === defaultId);
    return {
      visuals, default_visual_id: defaultId,
      default_portrait: primary?.type === "portrait" ? primary.default_portrait : "",
      expressions: primary?.type === "portrait" ? primary.expressions : {},
    };
  }

  function validate() {
    capture();
    let message = "", invalid = null;
    const names = new Set();
    if (!resources.length) message = "请先添加一种角色形态。";
    for (const visual of resources) {
      if (!visual.name.trim()) message = "请填写形态名称。";
      else if (names.has(visual.name.trim())) message = "形态名称重复，请换一个名称。";
      names.add(visual.name.trim());
      if (available(visual)) {
        if (visual.type === "portrait" && (!Object.keys(visual.expressions || {}).length || !Object.values(visual.expressions).includes(visual.default_portrait))) message ||= `请为「${visual.name}」选择图片和默认表情。`;
        if (visual.type !== "portrait" && !visual.model_file) message ||= `请为「${visual.name}」选择模型文件。`;
      }
      if (message) { invalid = visual; break; }
    }
    if (!message && selected()?.type === "portrait") {
      const labels = new Set();
      for (const row of document.querySelectorAll("#expressionList .expression-row")) {
        const label = row.querySelector("[data-expression-label]").value.trim();
        if (!label || labels.has(label)) { message = label ? `表情标签重复：${label}` : "请填写表情标签。"; break; }
        labels.add(label);
      }
    }
    if (message) {
      if (invalid) { selectedId = invalid.id; renderSelected(); }
      api.switchPage("portrait"); api.setError(message); return false;
    }
    return true;
  }

  return {
    attach(next) { api = next; init(); },
    render(doc, renderer) {
      renderPortraits = renderer;
      if (characterId !== doc.id) selectedId = "";
      characterId = doc.id || "";
      resources = clone(doc.visuals || []); defaultId = doc.default_visual_id || resources[0]?.id || "";
      if (!resources.some(v => v.id === selectedId)) selectedId = defaultId;
      renderSelected();
    },
    collectPortrait, validate,
    refreshPreview() { capture(); renderCards(); decorateExpressions(); },
    setBusy(value) {
      library.querySelectorAll("button").forEach(b => b.disabled = value);
      empty.querySelectorAll("button").forEach(b => b.disabled = value);
      meta.querySelectorAll("input, button").forEach(b => b.disabled = value || (b.id === "defaultVisualButton" && selectedId === defaultId));
      for (const editor of [portraitEditor, modelEditor]) editor.querySelectorAll("input, button").forEach(b => b.disabled = value || !available(selected()));
    },
    flush() { return api?.flush(); },
    showForms() { api.switchPage("portrait"); },
  };
}
