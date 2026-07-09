const invoke = window.__TAURI__.core.invoke;

const fields = {
  pageTitle: document.getElementById("pageTitle"),
  pageSubtitle: document.getElementById("pageSubtitle"),
  navItems: Array.from(document.querySelectorAll(".nav-item[data-page]")),
  pages: {
    library: document.getElementById("page-library"),
    basic: document.getElementById("page-basic"),
    card: document.getElementById("page-card"),
    portrait: document.getElementById("page-portrait"),
    theme: document.getElementById("page-theme"),
  },
  characterSearch: document.getElementById("characterSearch"),
  refreshCharactersButton: document.getElementById("refreshCharactersButton"),
  characterList: document.getElementById("characterList"),
  newCharacterButton: document.getElementById("newCharacterButton"),
  characterId: document.getElementById("characterId"),
  displayName: document.getElementById("displayName"),
  initialMessage: document.getElementById("initialMessage"),
  voiceStatusRow: document.getElementById("voiceStatusRow"),
  voiceStatus: document.getElementById("voiceStatus"),
  cardText: document.getElementById("cardText"),
  replyToneInput: document.getElementById("replyToneInput"),
  defaultPortrait: document.getElementById("defaultPortrait"),
  importDefaultPortraitButton: document.getElementById("importDefaultPortraitButton"),
  expressionList: document.getElementById("expressionList"),
  addExpressionButton: document.getElementById("addExpressionButton"),
  themeFields: document.getElementById("themeFields"),
  errorText: document.getElementById("errorText"),
  exportButton: document.getElementById("exportButton"),
  cancelButton: document.getElementById("cancelButton"),
  saveButton: document.getElementById("saveButton"),
  pageHead: document.querySelector(".page-head"),
};

const pageMeta = {
  library: { title: "角色列表", subtitle: "打开本地角色或创建新角色" },
  basic: { title: "基础信息", subtitle: "名称、开场白与语音状态" },
  card: { title: "人设卡", subtitle: "系统人设与回复语气" },
  portrait: { title: "立绘", subtitle: "默认立绘与表情映射" },
  theme: { title: "配色", subtitle: "角色包自带主题色" },
};

const themeLabels = {
  primary_color: "主色",
  primary_hover_color: "主色悬停",
  accent_color: "强调色",
  text_color: "正文",
  secondary_text_color: "次级文字",
  muted_text_color: "弱文字",
  page_background_color: "页面背景",
  panel_background_color: "面板背景",
  input_background_color: "输入背景",
  bubble_background_color: "气泡背景",
  border_color: "边框",
};

const themeVars = {
  primary_color: "--sakura-primary",
  primary_hover_color: "--sakura-primary-hover",
  accent_color: "--sakura-accent",
  text_color: "--sakura-text",
  secondary_text_color: "--sakura-secondary-text",
  muted_text_color: "--sakura-muted-text",
  page_background_color: "--sakura-page-bg",
  panel_background_color: "--sakura-panel-bg",
  input_background_color: "--sakura-input-bg",
  bubble_background_color: "--sakura-bubble-bg",
  border_color: "--sakura-border",
};

let request = null;
let currentPackageDir = "";
let currentDoc = null;
let baseline = "";
let busy = false;

function setError(message) {
  fields.errorText.textContent = message || "";
}

function notify(message, type = "info") {
  const text = String(message || "").trim();
  if (!text) {
    return;
  }
  if (type === "error") {
    setError(text);
    return;
  }
  const stack = document.getElementById("toastStack");
  const toast = document.createElement("div");
  toast.className = `toast is-${type}`;
  toast.textContent = text;
  stack.append(toast);
  window.setTimeout(() => {
    toast.classList.add("is-leaving");
    window.setTimeout(() => toast.remove(), 220);
  }, 2600);
}

async function hostCall(method, params = {}) {
  return invoke("host_call", { method, params });
}

function switchPage(page) {
  if (!fields.pages[page]) {
    return;
  }
  fields.navItems.forEach((item) => {
    const active = item.dataset.page === page;
    item.classList.toggle("is-active", active);
    item.toggleAttribute("aria-current", active);
  });
  Object.entries(fields.pages).forEach(([key, element]) => {
    element.classList.toggle("is-active", key === page);
  });
  const meta = pageMeta[page];
  fields.pageTitle.textContent = meta.title;
  fields.pageSubtitle.textContent = meta.subtitle;
  fields.pageHead.classList.remove("is-switching");
  void fields.pageHead.offsetWidth;
  fields.pageHead.classList.add("is-switching");
}

async function refreshCharacters() {
  const result = await hostCall("studio.list_characters", {
    current_character_id: request?.initial_character_id || "",
  });
  request.characters = Array.isArray(result.characters) ? result.characters : [];
  renderCharacters();
}

function renderCharacters() {
  const query = fields.characterSearch.value.trim().toLowerCase();
  const items = (request?.characters || []).filter((item) => {
    const haystack = `${item.display_name || ""} ${item.id || ""}`.toLowerCase();
    return !query || haystack.includes(query);
  });
  fields.characterList.textContent = "";
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "empty-text";
    empty.textContent = "没有匹配的角色。";
    fields.characterList.append(empty);
    return;
  }
  items.forEach((character) => {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "character-row";
    row.dataset.id = character.id;
    const title = document.createElement("span");
    title.className = "character-row-title";
    title.textContent = character.display_name || character.id;
    const meta = document.createElement("span");
    meta.className = "character-row-meta";
    meta.textContent = `${character.id}${character.is_current ? " · 当前" : ""}${character.has_voice ? " · 含语音" : ""}`;
    row.append(title, meta);
    row.addEventListener("click", () => openCharacter(character.id));
    fields.characterList.append(row);
  });
}

function collectDoc() {
  const theme = { ...(currentDoc?.theme || {}) };
  fields.themeFields.querySelectorAll("[data-theme-key]").forEach((input) => {
    theme[input.dataset.themeKey] = input.value.trim();
  });
  const expressions = {};
  fields.expressionList.querySelectorAll(".expression-row").forEach((row) => {
    const label = row.querySelector("[data-expression-label]").value.trim();
    const path = row.querySelector("[data-expression-path]").value.trim();
    if (label && path) {
      expressions[label] = path;
    }
  });
  return {
    ...(currentDoc || {}),
    id: fields.characterId.value.trim(),
    display_name: fields.displayName.value.trim(),
    initial_message: fields.initialMessage.value,
    card_text: fields.cardText.value,
    reply_tones: fields.replyToneInput.value.split(/[,，]/).map((tone) => tone.trim()).filter(Boolean),
    default_portrait: fields.defaultPortrait.value.trim(),
    expressions,
    theme,
  };
}

function markBaseline() {
  baseline = JSON.stringify(collectDoc());
  refreshDirty();
}

function refreshDirty() {
  const dirty = currentDoc && JSON.stringify(collectDoc()) !== baseline;
  document.body.classList.toggle("is-dirty", Boolean(dirty));
  fields.saveButton.classList.toggle("has-changes", Boolean(dirty));
}

function setCurrentDoc(payload) {
  currentPackageDir = payload.package_dir || "";
  currentDoc = payload.doc || null;
  if (Array.isArray(payload.characters)) {
    request.characters = payload.characters;
    renderCharacters();
  }
  renderEditor();
  switchPage("basic");
  markBaseline();
}

function renderEditor() {
  const doc = currentDoc || {};
  fields.characterId.value = doc.id || "";
  fields.characterId.disabled = Boolean(doc.id);
  fields.displayName.value = doc.display_name || "";
  fields.initialMessage.value = doc.initial_message || "";
  fields.cardText.value = doc.card_text || "";
  fields.replyToneInput.value = Array.isArray(doc.reply_tones) ? doc.reply_tones.join("，") : "";
  fields.defaultPortrait.value = doc.default_portrait || "";
  fields.voiceStatusRow.hidden = false;
  fields.voiceStatus.textContent = doc.voice ? "已保留现有语音配置" : "未配置语音";
  renderExpressions(doc.expressions || {});
  renderTheme(doc.theme || request.theme || {});
  refreshControls();
}

function renderExpressions(expressions) {
  fields.expressionList.textContent = "";
  Object.entries(expressions).forEach(([label, path]) => addExpressionRow(label, path));
}

function addExpressionRow(label = "", path = "") {
  const row = document.createElement("div");
  row.className = "expression-row";
  const labelInput = document.createElement("input");
  labelInput.type = "text";
  labelInput.value = label;
  labelInput.placeholder = "标签";
  labelInput.dataset.expressionLabel = "1";
  const pathInput = document.createElement("input");
  pathInput.type = "text";
  pathInput.value = path;
  pathInput.placeholder = "portraits/example.png";
  pathInput.dataset.expressionPath = "1";
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "secondary-button icon-button";
  remove.textContent = "×";
  remove.addEventListener("click", () => {
    row.remove();
    refreshDirty();
  });
  row.append(labelInput, pathInput, remove);
  row.addEventListener("input", refreshDirty);
  fields.expressionList.append(row);
}

function renderTheme(theme) {
  fields.themeFields.textContent = "";
  Object.keys(themeVars).forEach((key) => {
    const row = document.createElement("label");
    row.className = "theme-field";
    const swatch = document.createElement("span");
    swatch.className = "theme-swatch";
    const text = document.createElement("span");
    text.textContent = themeLabels[key] || key;
    const input = document.createElement("input");
    input.type = "text";
    input.value = theme[key] || "";
    input.dataset.themeKey = key;
    const update = () => {
      const color = input.value.trim() || "transparent";
      swatch.style.background = color;
      if (themeVars[key] && color !== "transparent") {
        document.documentElement.style.setProperty(themeVars[key], color);
      }
      refreshDirty();
    };
    input.addEventListener("input", update);
    row.append(swatch, text, input);
    fields.themeFields.append(row);
    update();
  });
}

async function openCharacter(characterId) {
  await runBusy(async () => {
    const payload = await hostCall("studio.open_character", { character_id: characterId });
    setCurrentDoc(payload);
  });
}

async function createCharacter() {
  const id = window.prompt("角色 ID：", "");
  if (!id) {
    return;
  }
  const displayName = window.prompt("显示名称：", id) || id;
  await runBusy(async () => {
    const payload = await hostCall("studio.create_character", {
      doc: { id, display_name: displayName },
    });
    setCurrentDoc(payload);
  });
}

async function importDefaultPortrait() {
  if (!currentDoc || !currentPackageDir) {
    setError("请先打开或新建角色。");
    return;
  }
  const selected = await window.__TAURI__?.dialog?.open({
    title: "导入默认立绘",
    multiple: false,
    filters: [{ name: "图片", extensions: ["png", "jpg", "jpeg", "webp", "gif"] }],
  });
  const path = Array.isArray(selected) ? selected[0] : selected;
  if (!path) {
    return;
  }
  await runBusy(async () => {
    const result = await hostCall("studio.import_portrait", {
      package_dir: currentPackageDir,
      path,
      label: "default",
    });
    fields.defaultPortrait.value = result.relative_path;
    refreshDirty();
  });
}

async function saveCharacter() {
  if (!currentDoc || !currentPackageDir) {
    setError("请先打开或新建角色。");
    return;
  }
  await runBusy(async () => {
    const payload = await hostCall("studio.save_character", {
      package_dir: currentPackageDir,
      current_character_id: request.initial_character_id || "",
      doc: collectDoc(),
    });
    if (Array.isArray(payload.characters)) {
      request.characters = payload.characters;
      renderCharacters();
    }
    currentDoc = payload.doc || collectDoc();
    markBaseline();
    notify(payload.message || "已保存。", "success");
  });
}

async function exportCharacter() {
  if (!currentDoc || !currentPackageDir) {
    setError("请先打开或新建角色。");
    return;
  }
  const defaultPath = `${fields.characterId.value.trim() || "character"}.char`;
  const path = await window.__TAURI__?.dialog?.save({
    title: "导出 Sakura 角色包",
    defaultPath,
    filters: [{ name: "Sakura 角色包", extensions: ["char"] }],
  });
  if (!path) {
    return;
  }
  await runBusy(async () => {
    await hostCall("studio.save_draft", { package_dir: currentPackageDir, doc: collectDoc() });
    const result = await hostCall("studio.export_archive", {
      package_dir: currentPackageDir,
      path,
      include_voice: false,
    });
    notify(result.message || "角色包已导出。", "success");
  });
}

async function runBusy(action) {
  if (busy) {
    return;
  }
  busy = true;
  refreshControls();
  setError("");
  try {
    await action();
  } catch (error) {
    setError(String(error));
  } finally {
    busy = false;
    refreshControls();
  }
}

function refreshControls() {
  const hasDoc = Boolean(currentDoc);
  fields.saveButton.disabled = busy || !hasDoc;
  fields.exportButton.disabled = busy || !hasDoc;
  fields.importDefaultPortraitButton.disabled = busy || !hasDoc;
  fields.refreshCharactersButton.disabled = busy;
  fields.newCharacterButton.disabled = busy;
}

async function closeStudio() {
  await invoke("close_studio");
}

async function load() {
  request = await invoke("load_request");
  Object.entries(themeVars).forEach(([key, cssVar]) => {
    if (request.theme?.[key]) {
      document.documentElement.style.setProperty(cssVar, request.theme[key]);
    }
  });
  renderCharacters();
  if (request.initial_character_id) {
    await openCharacter(request.initial_character_id);
  } else {
    refreshControls();
  }
}

fields.navItems.forEach((item) => item.addEventListener("click", () => switchPage(item.dataset.page)));
fields.characterSearch.addEventListener("input", renderCharacters);
fields.refreshCharactersButton.addEventListener("click", () => runBusy(refreshCharacters));
fields.newCharacterButton.addEventListener("click", createCharacter);
fields.importDefaultPortraitButton.addEventListener("click", importDefaultPortrait);
fields.addExpressionButton.addEventListener("click", () => {
  addExpressionRow();
  refreshDirty();
});
fields.saveButton.addEventListener("click", saveCharacter);
fields.exportButton.addEventListener("click", exportCharacter);
fields.cancelButton.addEventListener("click", closeStudio);
[
  fields.characterId,
  fields.displayName,
  fields.initialMessage,
  fields.cardText,
  fields.replyToneInput,
  fields.defaultPortrait,
].forEach((element) => element.addEventListener("input", refreshDirty));

window.__TAURI__?.event?.listen?.("sakura://studio-close-requested", closeStudio);
load().catch((error) => setError(String(error)));
