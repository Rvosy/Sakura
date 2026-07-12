const invoke = window.__TAURI__.core.invoke;

const fields = {
  pageTitle: document.getElementById("pageTitle"),
  pageSubtitle: document.getElementById("pageSubtitle"),
  navItems: Array.from(document.querySelectorAll(".nav-item[data-page]")),
  pages: {
    basic: document.getElementById("page-basic"),
    card: document.getElementById("page-card"),
    portrait: document.getElementById("page-portrait"),
    "voice-model": document.getElementById("page-voice-model"),
    "reference-audio": document.getElementById("page-reference-audio"),
    theme: document.getElementById("page-theme"),
  },
  studioCharacterSelect: document.getElementById("studioCharacterSelect"),
  newCharacterButton: document.getElementById("newCharacterButton"),
  discardDraftButton: document.getElementById("discardDraftButton"),
  characterId: document.getElementById("characterId"),
  displayName: document.getElementById("displayName"),
  initialMessage: document.getElementById("initialMessage"),
  cardText: document.getElementById("cardText"),
  expressionList: document.getElementById("expressionList"),
  addExpressionButton: document.getElementById("addExpressionButton"),
  importPortraitFolderButton: document.getElementById("importPortraitFolderButton"),
  voiceEnabled: document.getElementById("voiceEnabled"),
  voiceEnabledLabel: document.getElementById("voiceEnabledLabel"),
  voiceModelFields: document.getElementById("voiceModelFields"),
  gptModelPath: document.getElementById("gptModelPath"),
  importGptModelButton: document.getElementById("importGptModelButton"),
  clearGptModelButton: document.getElementById("clearGptModelButton"),
  sovitsModelPath: document.getElementById("sovitsModelPath"),
  importSovitsModelButton: document.getElementById("importSovitsModelButton"),
  clearSovitsModelButton: document.getElementById("clearSovitsModelButton"),
  defaultRefLang: document.getElementById("defaultRefLang"),
  textLang: document.getElementById("textLang"),
  referenceAudioList: document.getElementById("referenceAudioList"),
  addReferenceAudioButton: document.getElementById("addReferenceAudioButton"),
  importReferenceAudioFolderButton: document.getElementById("importReferenceAudioFolderButton"),
  themeFields: document.getElementById("themeFields"),
  errorText: document.getElementById("errorText"),
  exportButton: document.getElementById("exportButton"),
  cancelButton: document.getElementById("cancelButton"),
  saveDraftButton: document.getElementById("saveDraftButton"),
  publishButton: document.getElementById("publishButton"),
  saveButton: document.getElementById("saveButton"),
  pageHead: document.querySelector(".page-head"),
};

const pageMeta = {
  basic: { title: "基础信息", subtitle: "名称与开场白" },
  card: { title: "人设卡", subtitle: "系统人设" },
  portrait: { title: "立绘", subtitle: "默认立绘与表情映射" },
  "voice-model": { title: "语音模型", subtitle: "GPT-SoVITS 模型与默认语言" },
  "reference-audio": { title: "参考语音", subtitle: "音频、参考文本与回复语气描述词" },
  theme: { title: "配色", subtitle: "角色包自带主题色" },
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
let currentWorkspaceId = "";
let currentDoc = null;
let baseline = "";
let busy = false;
let editingCharacterId = "";
let temporaryCharacter = null;
let activeThemeField = "";
let themeEditor = {};
let previewAudio = null;
let draftAutosaveTimer = null;
let draftAutosavePromise = null;
let renderingEditor = false;

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

function renderSelectOptionContent(container, option, { includeSource = false } = {}) {
  container.textContent = "";
  const main = document.createElement("span");
  main.className = "custom-select__option-main";
  if (option.dataset.dirty === "true") {
    const dot = document.createElement("span");
    dot.className = "custom-select__dirty-dot";
    dot.setAttribute("aria-hidden", "true");
    main.append(dot);
    const status = document.createElement("span");
    status.className = "visually-hidden";
    status.textContent = "有未发布修改";
    main.append(status);
  }
  const text = document.createElement("span");
  text.className = "custom-select__option-text";
  text.textContent = option.textContent;
  main.append(text);
  container.append(main);
  if (includeSource && option.dataset.sourceLabel) {
    const source = document.createElement("span");
    source.className = "custom-select__source";
    source.textContent = option.dataset.sourceLabel;
    container.append(source);
  }
}

function selectOptionAccessibleLabel(option) {
  const parts = [option.textContent];
  if (option.dataset.sourceLabel) {
    parts.push(option.dataset.sourceLabel);
  }
  if (option.dataset.dirty === "true") {
    parts.push("有未发布修改");
  }
  return parts.filter(Boolean).join("，");
}

function enhanceSelect(select) {
  if (!select || select.__customSelect) {
    return;
  }
  const wrapper = document.createElement("div");
  wrapper.className = "custom-select";
  const trigger = document.createElement("button");
  trigger.type = "button";
  trigger.className = "custom-select__trigger";
  trigger.setAttribute("aria-haspopup", "listbox");
  trigger.setAttribute("aria-expanded", "false");
  const label = document.createElement("span");
  label.className = "custom-select__label";
  const controlLabel = select.id
    ? document.querySelector(`label[for="${select.id}"]`)
    : null;
  if (controlLabel) {
    controlLabel.id = controlLabel.id || `${select.id}-label`;
    label.id = `${select.id}-value`;
    trigger.setAttribute("aria-labelledby", `${controlLabel.id} ${label.id}`);
  }
  const caret = document.createElement("span");
  caret.className = "custom-select__caret";
  caret.setAttribute("aria-hidden", "true");
  trigger.append(label, caret);
  const menu = document.createElement("div");
  menu.className = "custom-select__menu";
  menu.id = "studio-character-menu";
  menu.setAttribute("role", "listbox");
  if (controlLabel) {
    menu.setAttribute("aria-labelledby", controlLabel.id);
  }
  trigger.setAttribute("aria-controls", menu.id);

  select.parentNode.insertBefore(wrapper, select);
  wrapper.append(trigger, select);

  function syncTrigger() {
    const option = select.options[select.selectedIndex];
    if (option) {
      renderSelectOptionContent(label, option, { includeSource: true });
      trigger.setAttribute("aria-label", selectOptionAccessibleLabel(option));
    } else {
      label.textContent = "";
      trigger.removeAttribute("aria-label");
    }
    trigger.disabled = select.disabled;
  }

  function buildMenu() {
    menu.textContent = "";
    let currentGroup = "";
    Array.from(select.options).forEach((option) => {
      const group = option.dataset.group || "";
      if (group && group !== currentGroup) {
        const groupLabel = document.createElement("div");
        groupLabel.className = "custom-select__group";
        groupLabel.setAttribute("role", "presentation");
        groupLabel.textContent = option.dataset.groupLabel || group;
        menu.append(groupLabel);
        currentGroup = group;
      }
      const item = document.createElement("div");
      item.className = "custom-select__option";
      item.setAttribute("role", "option");
      item.tabIndex = -1;
      item.setAttribute("aria-label", selectOptionAccessibleLabel(option));
      renderSelectOptionContent(item, option);
      if (option.value === select.value) {
        item.classList.add("is-selected");
        item.setAttribute("aria-selected", "true");
      } else {
        item.setAttribute("aria-selected", "false");
      }
      if (option.disabled) {
        item.classList.add("is-disabled");
        item.setAttribute("aria-disabled", "true");
      }
      item.addEventListener("click", () => {
        if (option.disabled) {
          return;
        }
        if (select.value !== option.value) {
          select.value = option.value;
          select.dispatchEvent(new Event("change", { bubbles: true }));
        }
        syncTrigger();
        closeMenu();
        trigger.focus();
      });
      item.addEventListener("keydown", (event) => {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          focusRelativeMenuOption(item, event.key === "ArrowDown" ? 1 : -1);
        } else if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          item.click();
        } else if (event.key === "Tab") {
          event.preventDefault();
          closeMenu();
          trigger.focus();
        } else if (event.key === "Escape") {
          event.preventDefault();
          closeMenu();
          trigger.focus();
        }
      });
      menu.append(item);
    });
  }

  function focusRelativeMenuOption(current, direction) {
    const items = Array.from(menu.querySelectorAll(".custom-select__option:not(.is-disabled)"));
    const currentIndex = items.indexOf(current);
    const nextIndex = Math.min(items.length - 1, Math.max(0, currentIndex + direction));
    items[nextIndex]?.focus();
  }

  function positionMenu() {
    const rect = trigger.getBoundingClientRect();
    const maxWidth = Math.max(120, window.innerWidth - 16);
    menu.style.minWidth = `${rect.width}px`;
    menu.style.width = "max-content";
    menu.style.maxWidth = `${maxWidth}px`;
    const menuWidth = Math.min(menu.offsetWidth, maxWidth);
    menu.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - menuWidth - 8))}px`;
    const menuHeight = menu.offsetHeight;
    const spaceBelow = window.innerHeight - rect.bottom;
    menu.style.top = spaceBelow < menuHeight + 12 && rect.top > spaceBelow
      ? `${Math.max(8, rect.top - 6 - menuHeight)}px`
      : `${rect.bottom + 6}px`;
  }

  function onDocumentPointer(event) {
    if (!wrapper.contains(event.target) && !menu.contains(event.target)) {
      closeMenu();
    }
  }

  function onKeydown(event) {
    if (event.key === "Escape") {
      closeMenu();
    }
  }

  menu.addEventListener("focusout", (event) => {
    const next = event.relatedTarget;
    if (!menu.contains(next) && next !== trigger) {
      closeMenu();
    }
  });

  function openMenu() {
    if (select.disabled) {
      return;
    }
    buildMenu();
    document.body.append(menu);
    menu.classList.add("is-open");
    positionMenu();
    wrapper.classList.add("is-open");
    trigger.setAttribute("aria-expanded", "true");
    document.addEventListener("pointerdown", onDocumentPointer, true);
    document.addEventListener("keydown", onKeydown, true);
    window.addEventListener("scroll", closeMenu, true);
    window.addEventListener("resize", closeMenu, true);
    const selected = menu.querySelector(".custom-select__option.is-selected:not(.is-disabled)");
    (selected || menu.querySelector(".custom-select__option:not(.is-disabled)"))?.focus();
  }

  function closeMenu() {
    wrapper.classList.remove("is-open");
    trigger.setAttribute("aria-expanded", "false");
    menu.classList.remove("is-open");
    menu.remove();
    document.removeEventListener("pointerdown", onDocumentPointer, true);
    document.removeEventListener("keydown", onKeydown, true);
    window.removeEventListener("scroll", closeMenu, true);
    window.removeEventListener("resize", closeMenu, true);
  }

  function selectRelativeOption(direction) {
    const options = Array.from(select.options).filter((option) => !option.disabled);
    if (!options.length) {
      return;
    }
    const currentIndex = options.findIndex((option) => option.value === select.value);
    const nextIndex = currentIndex < 0
      ? (direction > 0 ? 0 : options.length - 1)
      : Math.min(options.length - 1, Math.max(0, currentIndex + direction));
    const option = options[nextIndex];
    if (option.value !== select.value) {
      select.value = option.value;
      select.dispatchEvent(new Event("change", { bubbles: true }));
    }
    syncTrigger();
  }

  trigger.addEventListener("click", () => {
    wrapper.classList.contains("is-open") ? closeMenu() : openMenu();
  });
  trigger.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      closeMenu();
      selectRelativeOption(event.key === "ArrowDown" ? 1 : -1);
    }
  });
  select.addEventListener("change", syncTrigger);
  select.__customSelect = {
    refresh: syncTrigger,
    focus: () => trigger.focus(),
  };
  syncTrigger();
}

function refreshSelect(select) {
  select?.__customSelect?.refresh();
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
  Object.values(fields.pages).forEach((element) => {
    element.classList.remove("is-active");
  });
  void fields.pages[page].offsetWidth;
  fields.pages[page].classList.add("is-active");
  const meta = pageMeta[page];
  fields.pageTitle.textContent = meta.title;
  fields.pageSubtitle.textContent = meta.subtitle;
  fields.pageHead.classList.remove("is-switching");
  void fields.pageHead.offsetWidth;
  fields.pageHead.classList.add("is-switching");
}

function isDirty() {
  return Boolean(currentDoc) && editorSnapshot() !== baseline;
}

function confirmDiscardChanges() {
  return !isDirty() || window.confirm("当前修改尚未保存，继续操作将丢失这些修改。是否继续？");
}

function currentCharacterEntry() {
  return (request?.characters || []).find((item) => item.id === editingCharacterId) || null;
}

function isPublishedCharacter(character = currentCharacterEntry()) {
  return Boolean(character?.is_installed);
}

function characterOptionLabel(character) {
  return character.display_name || character.id;
}

function characterOptionGroup(character) {
  return character.is_installed
    ? { id: "published", label: "已发布角色", sourceLabel: "已发布" }
    : { id: "workspace", label: "工作区", sourceLabel: "工作区" };
}

function characterHasPendingChanges(character) {
  return Boolean(
    character?.is_dirty
    || (character?.id === editingCharacterId && isDirty())
  );
}

function characterOptions() {
  const installed = Array.isArray(request?.characters) ? request.characters : [];
  if (!temporaryCharacter || installed.some((item) => item.id === temporaryCharacter.id)) {
    return installed;
  }
  return [temporaryCharacter, ...installed];
}

function renderCharacterOptions() {
  fields.studioCharacterSelect.textContent = "";
  const characters = characterOptions();
  const ordered = [
    ...characters.filter((character) => !character.is_installed),
    ...characters.filter((character) => character.is_installed),
  ];
  ordered.forEach((character) => {
    const group = characterOptionGroup(character);
    const option = document.createElement("option");
    option.value = character.id;
    option.textContent = characterOptionLabel(character);
    option.dataset.group = group.id;
    option.dataset.groupLabel = group.label;
    option.dataset.sourceLabel = group.sourceLabel;
    option.dataset.dirty = String(characterHasPendingChanges(character));
    fields.studioCharacterSelect.append(option);
  });
  fields.studioCharacterSelect.value = editingCharacterId;
  refreshSelect(fields.studioCharacterSelect);
}

function refreshCurrentCharacterOption() {
  const option = Array.from(fields.studioCharacterSelect.options).find(
    (item) => item.value === editingCharacterId,
  );
  if (!option) {
    return;
  }
  const entry = currentCharacterEntry();
  option.dataset.dirty = String(characterHasPendingChanges(entry));
  refreshSelect(fields.studioCharacterSelect);
}

function collectDoc() {
  const theme = { ...(currentDoc?.theme || {}) };
  fields.themeFields.querySelectorAll("[data-theme-field]").forEach((input) => {
    theme[input.dataset.themeField] = input.value.trim();
  });
  const expressions = {};
  let defaultPortrait = "";
  fields.expressionList.querySelectorAll(".expression-row").forEach((row) => {
    const label = row.querySelector("[data-expression-label]").value.trim();
    const path = row.querySelector("[data-expression-path]").value.trim();
    if (row.querySelector("[data-portrait-default]").checked) {
      defaultPortrait = path;
    }
    if (label && path) {
      expressions[label] = path;
    }
  });
  const referenceAudios = collectReferenceAudios();
  const voiceEnabled = fields.voiceEnabled.checked;
  const replyTones = [];
  const seenTones = new Set();
  if (voiceEnabled) {
    referenceAudios.forEach(({ tone }) => {
      if (tone && !seenTones.has(tone)) {
        replyTones.push(tone);
        seenTones.add(tone);
      }
    });
  }
  return {
    ...(currentDoc || {}),
    id: fields.characterId.value.trim(),
    display_name: fields.displayName.value.trim(),
    initial_message: fields.initialMessage.value,
    card_text: fields.cardText.value,
    reply_tones: replyTones,
    default_portrait: defaultPortrait,
    expressions,
    voice: voiceEnabled ? {
      tone_refs: "voice/refs/ref.txt",
      gpt_model: fields.gptModelPath.value.trim(),
      sovits_model: fields.sovitsModelPath.value.trim(),
      ref_lang: fields.defaultRefLang.value.trim() || "ja",
      text_lang: fields.textLang.value.trim() || "ja",
    } : null,
    reference_audios: voiceEnabled ? referenceAudios : [],
    theme,
  };
}

function editorSnapshot() {
  const expressionRows = Array.from(fields.expressionList.querySelectorAll(".expression-row"), (row) => ({
    label: row.querySelector("[data-expression-label]").value,
    path: row.querySelector("[data-expression-path]").value,
  }));
  return JSON.stringify({ doc: collectDoc(), expressionRows });
}

function markBaseline() {
  baseline = editorSnapshot();
  refreshDirty();
}

function refreshDirty() {
  const dirty = isDirty();
  document.body.classList.toggle("is-dirty", Boolean(dirty));
  refreshCurrentCharacterOption();
}

function handleEditorChanged() {
  if (renderingEditor) {
    return;
  }
  refreshDirty();
  refreshControls();
  scheduleDraftAutosave();
}

function scheduleDraftAutosave() {
  if (!currentWorkspaceId || !currentDoc) {
    return;
  }
  window.clearTimeout(draftAutosaveTimer);
  draftAutosaveTimer = window.setTimeout(() => {
    flushDraftAutosave().catch((error) => setError(`草稿自动保存失败：${error}`));
  }, 650);
}

async function flushDraftAutosave() {
  window.clearTimeout(draftAutosaveTimer);
  draftAutosaveTimer = null;
  if (!currentWorkspaceId || !currentDoc || !isDirty()) {
    return null;
  }
  if (draftAutosavePromise) {
    await draftAutosavePromise;
  }
  const doc = collectDoc();
  draftAutosavePromise = hostCall("studio.save_workspace_draft", {
    workspace_id: currentWorkspaceId,
    doc,
  });
  try {
    const result = await draftAutosavePromise;
    currentDoc = result.doc || doc;
    const existing = (request.characters || []).find((item) => item.id === currentDoc.id);
    if (existing) {
      existing.display_name = currentDoc.display_name;
      existing.has_draft = true;
      existing.is_dirty = true;
      existing.source = "draft";
    } else {
      request.characters = [{
        id: currentDoc.id,
        display_name: currentDoc.display_name,
        source: "draft",
        is_installed: false,
        has_draft: true,
        draft_kind: "new",
        is_dirty: true,
      }, ...(request.characters || [])];
    }
    renderCharacterOptions();
    return result;
  } finally {
    draftAutosavePromise = null;
  }
}

function setCurrentDoc(payload, draftCharacter = null, options = {}) {
  stopReferenceAudioPreview();
  currentPackageDir = payload.package_dir || "";
  currentWorkspaceId = payload.workspace_id || payload.doc?.id || "";
  currentDoc = payload.doc || null;
  if (Array.isArray(payload.characters)) {
    request.characters = payload.characters;
  }
  editingCharacterId = currentDoc?.id || "";
  temporaryCharacter = draftCharacter;
  renderCharacterOptions();
  renderEditor();
  switchPage("basic");
  if (options.dirty === true || payload.is_dirty === true) {
    baseline = "";
    refreshDirty();
  } else {
    markBaseline();
  }
}

function renderEditor() {
  renderingEditor = true;
  const doc = currentDoc || {};
  fields.characterId.value = doc.id || "";
  fields.characterId.disabled = Boolean(doc.id);
  fields.displayName.value = doc.display_name || "";
  fields.initialMessage.value = doc.initial_message || "";
  fields.cardText.value = doc.card_text || "";
  fields.voiceEnabled.checked = Boolean(doc.voice);
  fields.gptModelPath.value = doc.voice?.gpt_model || "";
  fields.sovitsModelPath.value = doc.voice?.sovits_model || "";
  fields.defaultRefLang.value = doc.voice?.ref_lang || "ja";
  fields.textLang.value = doc.voice?.text_lang || "ja";
  renderExpressions(doc.expressions || {}, doc.default_portrait || "");
  renderReferenceAudios(doc.reference_audios || []);
  const theme = {
    ...(request.theme_defaults || request.theme || {}),
    ...(doc.theme || {}),
  };
  applyTheme(theme);
  renderTheme(theme);
  syncVoiceEnabledState();
  refreshControls();
  renderingEditor = false;
}

function renderExpressions(expressions, defaultPortrait = "") {
  fields.expressionList.textContent = "";
  let defaultFound = false;
  Object.entries(expressions).forEach(([label, path]) => {
    const isDefault = path === defaultPortrait && !defaultFound;
    defaultFound ||= isDefault;
    addExpressionRow(label, path, isDefault);
  });
  if (defaultPortrait && !defaultFound) {
    addExpressionRow("默认", defaultPortrait, true);
  }
  syncExpressionEmptyState();
}

function addExpressionRow(label = "", path = "", isDefault = false) {
  fields.expressionList.querySelector(".resource-empty")?.remove();
  const row = document.createElement("div");
  row.className = "expression-row";
  const defaultLabel = document.createElement("label");
  defaultLabel.className = "portrait-default-control";
  const defaultInput = document.createElement("input");
  defaultInput.type = "radio";
  defaultInput.name = "defaultPortraitResource";
  defaultInput.dataset.portraitDefault = "1";
  defaultInput.checked = Boolean(isDefault);
  const defaultText = document.createElement("span");
  defaultText.textContent = "默认";
  defaultLabel.append(defaultInput, defaultText);
  const labelInput = document.createElement("input");
  labelInput.type = "text";
  labelInput.value = label;
  labelInput.placeholder = "标签";
  labelInput.dataset.expressionLabel = "1";
  const pathInput = document.createElement("input");
  pathInput.type = "text";
  pathInput.readOnly = true;
  pathInput.value = path;
  pathInput.placeholder = "portraits/example.png";
  pathInput.dataset.expressionPath = "1";
  const replace = document.createElement("button");
  replace.type = "button";
  replace.className = "secondary-button compact-button";
  replace.textContent = path ? "替换" : "选择";
  replace.addEventListener("click", () => importPortrait(row));
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "secondary-button icon-button";
  remove.textContent = "×";
  remove.addEventListener("click", () => {
    const wasDefault = defaultInput.checked;
    row.remove();
    if (wasDefault) {
      fields.expressionList.querySelector("[data-portrait-default]")?.click();
    }
    syncExpressionEmptyState();
    handleEditorChanged();
  });
  const actions = document.createElement("div");
  actions.className = "portrait-resource-actions";
  actions.append(replace, remove);
  row.append(defaultLabel, labelInput, pathInput, actions);
  row.addEventListener("input", handleEditorChanged);
  row.addEventListener("change", handleEditorChanged);
  fields.expressionList.append(row);
}

function syncExpressionEmptyState() {
  if (fields.expressionList.querySelector(".expression-row")) {
    fields.expressionList.querySelector(".resource-empty")?.remove();
    return;
  }
  if (fields.expressionList.querySelector(".resource-empty")) {
    return;
  }
  const empty = document.createElement("div");
  empty.className = "resource-empty";
  empty.innerHTML = "<strong>还没有立绘</strong><span>选择图片或导入一个立绘文件夹。</span>";
  fields.expressionList.append(empty);
}

function collectReferenceAudios() {
  return Array.from(fields.referenceAudioList.querySelectorAll(".reference-audio-row"), (row) => ({
    audio_path: row.querySelector("[data-reference-audio-path]").value.trim(),
    ref_lang: row.querySelector("[data-reference-lang]").value.trim(),
    ref_text: row.querySelector("[data-reference-text]").value.trim(),
    tone: row.querySelector("[data-reference-tone]").value.trim(),
  }));
}

function renderReferenceAudios(references) {
  fields.referenceAudioList.textContent = "";
  (Array.isArray(references) ? references : []).forEach((reference) => {
    addReferenceAudioRow(reference);
  });
  syncReferenceAudioEmptyState();
}

function syncReferenceAudioEmptyState() {
  const existing = fields.referenceAudioList.querySelector(".reference-audio-empty");
  const hasRows = Boolean(fields.referenceAudioList.querySelector(".reference-audio-row"));
  if (hasRows) {
    existing?.remove();
    return;
  }
  if (existing) {
    return;
  }
  const empty = document.createElement("div");
  empty.className = "reference-audio-empty";
  empty.innerHTML = "<strong>还没有参考语音</strong><span>添加音频后填写参考文本和描述词。</span>";
  fields.referenceAudioList.append(empty);
}

function addReferenceAudioRow(reference = {}) {
  fields.referenceAudioList.querySelector(".reference-audio-empty")?.remove();
  const row = document.createElement("article");
  row.className = "reference-audio-row";

  const head = document.createElement("div");
  head.className = "reference-audio-head";
  const pathInput = document.createElement("input");
  pathInput.type = "text";
  pathInput.readOnly = true;
  pathInput.value = reference.audio_path || "";
  pathInput.placeholder = "voice/refs/tone_refs/example.wav";
  pathInput.dataset.referenceAudioPath = "1";
  pathInput.setAttribute("aria-label", "参考语音文件");

  const actions = document.createElement("div");
  actions.className = "reference-audio-actions";
  const preview = document.createElement("button");
  preview.type = "button";
  preview.className = "secondary-button compact-button";
  preview.textContent = "试听";
  preview.addEventListener("click", () => previewReferenceAudio(row));
  const replace = document.createElement("button");
  replace.type = "button";
  replace.className = "secondary-button compact-button";
  replace.textContent = "替换";
  replace.addEventListener("click", () => importReferenceAudio(row));
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "secondary-button compact-button danger-button";
  remove.textContent = "删除";
  remove.addEventListener("click", () => {
    row.remove();
    syncReferenceAudioEmptyState();
    handleEditorChanged();
    refreshControls();
  });
  actions.append(preview, replace, remove);
  head.append(pathInput, actions);

  const details = document.createElement("div");
  details.className = "reference-audio-fields";
  const langField = buildReferenceField("语言", "例如 JA", reference.ref_lang || fields.defaultRefLang.value || "JA", "referenceLang");
  const textField = buildReferenceField("参考文本", "音频中实际说出的内容", reference.ref_text || "", "referenceText");
  const toneField = buildReferenceField("描述词", "例如 温柔、开心", reference.tone || "", "referenceTone");
  details.append(langField, textField, toneField);
  row.append(head, details);
  row.addEventListener("input", handleEditorChanged);
  fields.referenceAudioList.append(row);
}

function buildReferenceField(labelText, placeholder, value, dataKey) {
  const label = document.createElement("label");
  label.className = "reference-audio-field";
  const title = document.createElement("span");
  title.textContent = labelText;
  const input = document.createElement("input");
  input.type = "text";
  input.placeholder = placeholder;
  input.value = value;
  input.dataset[dataKey] = "1";
  label.append(title, input);
  return label;
}

function stopReferenceAudioPreview() {
  if (!previewAudio) {
    return;
  }
  previewAudio.pause();
  previewAudio.currentTime = 0;
  previewAudio = null;
}

function syncVoiceEnabledState() {
  const enabled = fields.voiceEnabled.checked;
  fields.voiceEnabledLabel.textContent = enabled ? "已启用" : "未启用";
  fields.voiceModelFields.classList.toggle("is-disabled", !enabled);
  document.getElementById("page-reference-audio")?.classList.toggle("is-voice-disabled", !enabled);
}

function normalizeColorText(value, fallback) {
  const text = String(value || "").trim();
  const prefixed = text.startsWith("#") ? text : `#${text}`;
  return /^#[0-9a-fA-F]{6}$/.test(prefixed) ? prefixed.toLowerCase() : fallback;
}

function themeFieldInput(id) {
  return fields.themeFields.querySelector(`[data-theme-field="${id}"]`);
}

function themeFieldValue(id) {
  const fallback = request?.theme_defaults?.[id] || "#000000";
  return normalizeColorText(themeFieldInput(id)?.value, fallback);
}

function applyTheme(theme) {
  (request?.theme_fields || []).forEach(({ id }) => {
    const color = normalizeColorText(theme?.[id], request.theme_defaults?.[id] || "");
    if (color && themeVars[id]) {
      document.documentElement.style.setProperty(themeVars[id], color);
    }
  });
}

function hexToRgb(hex) {
  const value = normalizeColorText(hex, "#000000").slice(1);
  return {
    r: Number.parseInt(value.slice(0, 2), 16),
    g: Number.parseInt(value.slice(2, 4), 16),
    b: Number.parseInt(value.slice(4, 6), 16),
  };
}

function componentToHex(value) {
  return Math.round(Math.min(255, Math.max(0, value))).toString(16).padStart(2, "0");
}

function rgbToHex({ r, g, b }) {
  return `#${componentToHex(r)}${componentToHex(g)}${componentToHex(b)}`;
}

function rgbToHsv({ r, g, b }) {
  const red = r / 255;
  const green = g / 255;
  const blue = b / 255;
  const max = Math.max(red, green, blue);
  const min = Math.min(red, green, blue);
  const delta = max - min;
  let h = 0;
  if (delta !== 0) {
    if (max === red) {
      h = ((green - blue) / delta) % 6;
    } else if (max === green) {
      h = (blue - red) / delta + 2;
    } else {
      h = (red - green) / delta + 4;
    }
    h *= 60;
    if (h < 0) {
      h += 360;
    }
  }
  return {
    h,
    s: max === 0 ? 0 : delta / max,
    v: max,
  };
}

function hsvToRgb({ h, s, v }) {
  const chroma = v * s;
  const x = chroma * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = v - chroma;
  let red = 0;
  let green = 0;
  let blue = 0;
  if (h < 60) {
    red = chroma; green = x;
  } else if (h < 120) {
    red = x; green = chroma;
  } else if (h < 180) {
    green = chroma; blue = x;
  } else if (h < 240) {
    green = x; blue = chroma;
  } else if (h < 300) {
    red = x; blue = chroma;
  } else {
    red = chroma; blue = x;
  }
  return {
    r: (red + m) * 255,
    g: (green + m) * 255,
    b: (blue + m) * 255,
  };
}

function renderTheme(theme) {
  closeThemeColorPopover();
  fields.themeFields.textContent = "";
  const themeFields = Array.isArray(request?.theme_fields) ? request.theme_fields : [];
  if (!themeFields.some(({ id }) => id === activeThemeField)) {
    activeThemeField = themeFields[0]?.id || "";
  }

  request.theme_fields.forEach(({ id, label }) => {
    const row = document.createElement("div");
    row.className = "form-row theme-color-row";
    row.dataset.themeRole = id;
    const rowLabel = document.createElement("label");
    rowLabel.htmlFor = `theme-${id}`;
    rowLabel.textContent = label;
    const controls = document.createElement("div");
    controls.className = "theme-color-control";
    const swatchButton = document.createElement("button");
    swatchButton.type = "button";
    swatchButton.className = "theme-color-swatch";
    swatchButton.dataset.themeSwatch = id;
    swatchButton.title = "调整颜色";
    swatchButton.addEventListener("click", () => openThemeColorPopover(id));
    const textInput = document.createElement("input");
    textInput.id = `theme-${id}`;
    textInput.type = "text";
    textInput.maxLength = 7;
    textInput.placeholder = "#RRGGBB";
    textInput.value = normalizeColorText(theme?.[id], request.theme_defaults?.[id] || "");
    textInput.dataset.themeField = id;
    textInput.addEventListener("input", () => {
      const color = normalizeColorText(textInput.value, "");
      if (color && themeVars[id]) {
        document.documentElement.style.setProperty(themeVars[id], color);
      }
      syncThemeRole(id);
      if (id === activeThemeField) {
        syncThemeEditor();
      }
      handleEditorChanged();
    });
    controls.append(swatchButton, textInput);
    row.append(rowLabel, controls);
    fields.themeFields.append(row);
  });

  fields.themeFields.append(buildThemeEditor());
  request.theme_fields.forEach(({ id }) => syncThemeRole(id));
  selectThemeField(activeThemeField, { open: false });
}

function buildThemeEditor() {
  const editor = document.createElement("dialog");
  editor.className = "theme-color-popover";
  editor.hidden = true;

  const head = document.createElement("div");
  head.className = "theme-editor-head";
  const swatch = document.createElement("div");
  swatch.className = "theme-editor-swatch";
  const title = document.createElement("div");
  title.className = "theme-editor-title";
  const label = document.createElement("strong");
  const key = document.createElement("span");
  title.append(label, key);
  head.append(swatch, title);

  const hexRow = document.createElement("label");
  hexRow.className = "theme-editor-field";
  hexRow.textContent = "HEX";
  const hex = document.createElement("input");
  hex.type = "text";
  hex.maxLength = 7;
  hex.placeholder = "#RRGGBB";
  hex.addEventListener("input", () => {
    const color = normalizeColorText(hex.value, "");
    hex.classList.toggle("is-invalid", !color);
    if (color) {
      updateActiveThemeColor(color);
    }
  });
  hexRow.append(hex);

  const rgb = document.createElement("div");
  rgb.className = "theme-rgb-row";
  const rgbInputs = ["R", "G", "B"].map((name) => {
    const field = document.createElement("label");
    field.textContent = name;
    const input = document.createElement("input");
    input.type = "number";
    input.min = "0";
    input.max = "255";
    input.step = "1";
    input.addEventListener("input", updateThemeFromRgbInputs);
    field.append(input);
    rgb.append(field);
    return input;
  });

  const svPad = document.createElement("div");
  svPad.className = "theme-sv-pad";
  const svPointer = document.createElement("span");
  svPointer.className = "theme-picker-pointer";
  svPad.append(svPointer);
  svPad.addEventListener("pointerdown", updateThemeFromSvPointer);
  svPad.addEventListener("pointermove", (event) => {
    if (event.buttons & 1) {
      updateThemeFromSvPointer(event);
    }
  });

  const hue = document.createElement("div");
  hue.className = "theme-hue-strip";
  const huePointer = document.createElement("span");
  huePointer.className = "theme-hue-pointer";
  hue.append(huePointer);
  hue.addEventListener("pointerdown", updateThemeFromHuePointer);
  hue.addEventListener("pointermove", (event) => {
    if (event.buttons & 1) {
      updateThemeFromHuePointer(event);
    }
  });

  const actions = document.createElement("div");
  actions.className = "theme-editor-actions";
  const pick = document.createElement("button");
  pick.type = "button";
  pick.className = "secondary-button";
  pick.textContent = "取色";
  pick.addEventListener("click", pickActiveThemeColor);
  const done = document.createElement("button");
  done.type = "button";
  done.className = "primary-button";
  done.textContent = "完成";
  done.addEventListener("click", closeThemeColorPopover);
  actions.append(pick, done);

  editor.append(head, svPad, hue, hexRow, rgb, actions);
  editor.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeThemeColorPopover();
  });
  themeEditor = {
    root: editor,
    swatch,
    label,
    key,
    hex,
    rgbInputs,
    svPad,
    svPointer,
    hue,
    huePointer,
    pick,
  };
  return editor;
}

function syncThemeRole(id) {
  const input = themeFieldInput(id);
  const color = normalizeColorText(input?.value, "");
  const row = fields.themeFields.querySelector(`[data-theme-role="${id}"]`);
  const swatch = fields.themeFields.querySelector(`[data-theme-swatch="${id}"]`);
  row?.classList.toggle("is-active", id === activeThemeField);
  row?.classList.toggle("is-invalid", Boolean(input?.value) && !color);
  if (swatch) {
    swatch.style.backgroundColor = color || themeFieldValue(id);
  }
}

function selectThemeField(id, options = {}) {
  const themeFields = Array.isArray(request?.theme_fields) ? request.theme_fields : [];
  activeThemeField = themeFields.some((field) => field.id === id)
    ? id
    : (themeFields[0]?.id || "");
  themeFields.forEach(({ id: fieldId }) => syncThemeRole(fieldId));
  syncThemeEditor();
  if (options.open !== false) {
    openThemeColorPopover(activeThemeField);
  }
}

function syncThemeEditor() {
  if (!themeEditor.root || !activeThemeField) {
    return;
  }
  const color = themeFieldValue(activeThemeField);
  const rgb = hexToRgb(color);
  const hsv = rgbToHsv(rgb);
  const field = request.theme_fields.find(({ id }) => id === activeThemeField);
  themeEditor.root.style.setProperty("--theme-editor-color", color);
  themeEditor.root.style.setProperty("--theme-editor-hue", `${hsv.h}deg`);
  themeEditor.swatch.style.background = color;
  themeEditor.label.textContent = field?.label || activeThemeField;
  themeEditor.key.textContent = activeThemeField;
  themeEditor.hex.value = color;
  themeEditor.hex.classList.remove("is-invalid");
  [rgb.r, rgb.g, rgb.b].forEach((value, index) => {
    themeEditor.rgbInputs[index].value = String(value);
  });
  themeEditor.svPointer.style.left = `${hsv.s * 100}%`;
  themeEditor.svPointer.style.top = `${(1 - hsv.v) * 100}%`;
  themeEditor.huePointer.style.left = `${(hsv.h / 360) * 100}%`;
}

function openThemeColorPopover(id) {
  if (!id || !themeEditor.root) {
    return;
  }
  selectThemeField(id, { open: false });
  themeEditor.root.hidden = false;
  if (!themeEditor.root.open) {
    themeEditor.root.showModal();
  }
  themeEditor.hex.focus();
  document.addEventListener("keydown", closeThemePopoverOnEscape, true);
}

function closeThemeColorPopover() {
  if (themeEditor.root) {
    if (themeEditor.root.open) {
      themeEditor.root.close();
    }
    themeEditor.root.hidden = true;
  }
  document.removeEventListener("keydown", closeThemePopoverOnEscape, true);
}

function closeThemePopoverOnEscape(event) {
  if (event.key === "Escape") {
    closeThemeColorPopover();
  }
}

function updateActiveThemeColor(color) {
  const normalized = normalizeColorText(color, "");
  const input = themeFieldInput(activeThemeField);
  if (!normalized || !input) {
    return;
  }
  input.value = normalized;
  document.documentElement.style.setProperty(themeVars[activeThemeField], normalized);
  syncThemeRole(activeThemeField);
  syncThemeEditor();
  handleEditorChanged();
}

function updateThemeFromRgbInputs() {
  if (!themeEditor.rgbInputs?.length || themeEditor.rgbInputs.some((input) => input.value === "")) {
    return;
  }
  const [r, g, b] = themeEditor.rgbInputs.map((input) => (
    Math.min(255, Math.max(0, Number.parseInt(input.value, 10) || 0))
  ));
  updateActiveThemeColor(rgbToHex({ r, g, b }));
}

function updateThemeFromSvPointer(event) {
  const rect = themeEditor.svPad.getBoundingClientRect();
  const x = Math.min(rect.width, Math.max(0, event.clientX - rect.left));
  const y = Math.min(rect.height, Math.max(0, event.clientY - rect.top));
  const hsv = rgbToHsv(hexToRgb(themeFieldValue(activeThemeField)));
  updateActiveThemeColor(rgbToHex(hsvToRgb({
    h: hsv.h,
    s: rect.width ? x / rect.width : 0,
    v: rect.height ? 1 - (y / rect.height) : 0,
  })));
}

function updateThemeFromHuePointer(event) {
  const rect = themeEditor.hue.getBoundingClientRect();
  const x = Math.min(rect.width, Math.max(0, event.clientX - rect.left));
  const hsv = rgbToHsv(hexToRgb(themeFieldValue(activeThemeField)));
  updateActiveThemeColor(rgbToHex(hsvToRgb({
    h: rect.width ? (x / rect.width) * 360 : 0,
    s: hsv.s,
    v: hsv.v,
  })));
}

async function pickActiveThemeColor() {
  if (!activeThemeField) {
    return;
  }
  themeEditor.pick.disabled = true;
  setError("");
  try {
    closeThemeColorPopover();
    const result = await hostCall("studio.pick_screen_color");
    if (result?.cancelled) {
      return;
    }
    const color = normalizeColorText(result?.color, "");
    if (!color) {
      throw new Error("取色结果无效。");
    }
    updateActiveThemeColor(color);
  } catch (error) {
    setError(`屏幕取色失败：${error}`);
  } finally {
    themeEditor.pick.disabled = false;
  }
}

async function openCharacter(characterId) {
  await runBusy(async () => {
    const payload = await hostCall("studio.open_character", { character_id: characterId });
    setCurrentDoc(payload);
  });
}

async function selectCharacter(characterId) {
  const previousId = editingCharacterId;
  if (!characterId || characterId === previousId) {
    fields.studioCharacterSelect.value = previousId;
    refreshSelect(fields.studioCharacterSelect);
    return;
  }
  await flushDraftAutosave();
  await runBusy(async () => {
    try {
      const payload = await hostCall("studio.open_character", { character_id: characterId });
      setCurrentDoc(payload);
    } catch (error) {
      fields.studioCharacterSelect.value = previousId;
      refreshSelect(fields.studioCharacterSelect);
      throw error;
    }
  });
  fields.studioCharacterSelect.__customSelect?.focus();
}

async function createCharacter() {
  await flushDraftAutosave();
  const id = window.prompt("角色 ID：", "");
  if (!id) {
    return;
  }
  const characterId = id.trim();
  const existing = (request.characters || []).find((character) => character.id === characterId);
  if (existing) {
    if (!existing.is_installed) {
      await selectCharacter(characterId);
    } else {
      setError(`角色 ID 已存在：${characterId}。请从下拉菜单直接打开该角色。`);
    }
    return;
  }
  const displayName = window.prompt("显示名称：", characterId);
  if (displayName === null) {
    return;
  }
  await runBusy(async () => {
    const payload = await hostCall("studio.create_character", {
      doc: { id: characterId, display_name: displayName.trim() || characterId },
    });
    setCurrentDoc(payload, {
      id: payload.doc.id,
      display_name: payload.doc.display_name,
      source: "draft",
      is_installed: false,
      is_dirty: true,
    }, { dirty: true });
  });
}

async function discardCurrentDraft() {
  if (!currentWorkspaceId || !currentDoc) {
    return;
  }
  const entry = currentCharacterEntry();
  const published = isPublishedCharacter(entry);
  if (published && !entry?.has_draft && !entry?.is_dirty && !isDirty()) {
    return;
  }
  const action = published ? "放弃修改" : "删除工作区角色";
  const detail = published
    ? "主程序中的已发布版本不会受到影响。"
    : "该角色尚未发布，删除后无法恢复。";
  if (!window.confirm(`${action}「${currentDoc.display_name || currentDoc.id}」？\n${detail}`)) {
    return;
  }
  await runBusy(async () => {
    const result = await hostCall("studio.discard_draft", {
      workspace_id: currentWorkspaceId,
      current_character_id: request.initial_character_id || "",
    });
    request.characters = result.characters || [];
    if (result.doc) {
      setCurrentDoc(result);
      return;
    }
    currentPackageDir = "";
    currentWorkspaceId = "";
    currentDoc = null;
    editingCharacterId = "";
    temporaryCharacter = null;
    renderCharacterOptions();
    renderEditor();
    markBaseline();
  });
}

async function importPortrait(targetRow = null) {
  if (!currentDoc || !currentWorkspaceId) {
    setError("请先打开或新建角色。");
    return;
  }
  const selected = await window.__TAURI__?.dialog?.open({
    title: targetRow ? "替换立绘" : "选择立绘",
    multiple: false,
    filters: [{ name: "图片", extensions: ["png", "jpg", "jpeg", "webp", "gif"] }],
  });
  const path = Array.isArray(selected) ? selected[0] : selected;
  if (!path) {
    return;
  }
  await runBusy(async () => {
    const result = await hostCall("studio.import_portrait", {
      workspace_id: currentWorkspaceId,
      path,
      label: targetRow?.querySelector("[data-expression-label]")?.value.trim() || "portrait",
    });
    if (targetRow) {
      targetRow.querySelector("[data-expression-path]").value = result.relative_path;
      targetRow.querySelector(".compact-button").textContent = "替换";
    } else {
      const hasDefault = Boolean(fields.expressionList.querySelector("[data-portrait-default]:checked"));
      addExpressionRow(result.suggested_label || "立绘", result.relative_path, !hasDefault);
    }
    handleEditorChanged();
    await flushDraftAutosave();
  });
}

async function importPortraitFolder() {
  if (!currentDoc || !currentWorkspaceId) {
    setError("请先打开或新建角色。");
    return;
  }
  const selected = await window.__TAURI__?.dialog?.open({
    title: "选择含立绘的文件夹",
    directory: true,
    multiple: false,
  });
  const path = Array.isArray(selected) ? selected[0] : selected;
  if (!path) {
    return;
  }
  await runBusy(async () => {
    const result = await hostCall("studio.import_portrait_folder", {
      workspace_id: currentWorkspaceId,
      path,
    });
    let hasDefault = Boolean(fields.expressionList.querySelector("[data-portrait-default]:checked"));
    (result.items || []).forEach((item) => {
      addExpressionRow(item.suggested_label || "立绘", item.relative_path, !hasDefault);
      hasDefault = true;
    });
    if (!result.items?.length) {
      notify("所选文件夹中没有支持的立绘图片。", "info");
      return;
    }
    handleEditorChanged();
    await flushDraftAutosave();
    notify(`已导入 ${result.items.length} 张立绘。`, "success");
  });
}

async function importVoiceModel(modelType) {
  if (!currentDoc || !currentWorkspaceId) {
    setError("请先打开或新建角色。");
    return;
  }
  const isGpt = modelType === "gpt";
  const extension = isGpt ? "ckpt" : "pth";
  const selected = await window.__TAURI__?.dialog?.open({
    title: isGpt ? "选择 GPT 模型" : "选择 SoVITS 模型",
    multiple: false,
    filters: [{ name: isGpt ? "GPT 模型" : "SoVITS 模型", extensions: [extension] }],
  });
  const path = Array.isArray(selected) ? selected[0] : selected;
  if (!path) {
    return;
  }
  await runBusy(async () => {
    const result = await hostCall("studio.import_voice_model", {
      workspace_id: currentWorkspaceId,
      path,
      model_type: modelType,
    });
    fields.voiceEnabled.checked = true;
    (isGpt ? fields.gptModelPath : fields.sovitsModelPath).value = result.relative_path;
    syncVoiceEnabledState();
    handleEditorChanged();
    await flushDraftAutosave();
  });
}

async function importReferenceAudio(targetRow = null) {
  if (!currentDoc || !currentWorkspaceId) {
    setError("请先打开或新建角色。");
    return;
  }
  const selected = await window.__TAURI__?.dialog?.open({
    title: targetRow ? "替换参考语音" : "添加参考语音",
    multiple: !targetRow,
    filters: [{ name: "音频", extensions: ["wav", "mp3", "ogg", "flac"] }],
  });
  const paths = Array.isArray(selected) ? selected : (selected ? [selected] : []);
  if (!paths.length) {
    return;
  }
  await runBusy(async () => {
    fields.voiceEnabled.checked = true;
    syncVoiceEnabledState();
    for (const path of paths) {
      const result = await hostCall("studio.import_reference_audio", {
        workspace_id: currentWorkspaceId,
        path,
      });
      if (targetRow) {
        targetRow.querySelector("[data-reference-audio-path]").value = result.relative_path;
      } else {
        addReferenceAudioRow({
          audio_path: result.relative_path,
          ref_lang: fields.defaultRefLang.value.trim() || "JA",
          ref_text: "",
          tone: "",
        });
      }
    }
    handleEditorChanged();
    await flushDraftAutosave();
  });
}

async function importReferenceAudioFolder() {
  if (!currentDoc || !currentWorkspaceId) {
    setError("请先打开或新建角色。");
    return;
  }
  const selected = await window.__TAURI__?.dialog?.open({
    title: "选择参考语音文件夹",
    directory: true,
    multiple: false,
  });
  const path = Array.isArray(selected) ? selected[0] : selected;
  if (!path) {
    return;
  }
  await runBusy(async () => {
    const result = await hostCall("studio.import_reference_audio_folder", {
      workspace_id: currentWorkspaceId,
      path,
      ref_lang: fields.defaultRefLang.value.trim() || "JA",
    });
    fields.voiceEnabled.checked = true;
    syncVoiceEnabledState();
    (result.items || []).forEach((item) => addReferenceAudioRow(item));
    if (!result.items?.length) {
      notify("所选文件夹中没有支持的音频文件。", "info");
      return;
    }
    handleEditorChanged();
    await flushDraftAutosave();
    notify(`已导入 ${result.items.length} 条参考语音。`, "success");
  });
}

async function previewReferenceAudio(row) {
  const relativePath = row.querySelector("[data-reference-audio-path]").value.trim();
  if (!relativePath) {
    setError("当前参考语音没有可试听的音频文件。");
    return;
  }
  await runBusy(async () => {
    const result = await hostCall("studio.load_reference_audio_preview", {
      workspace_id: currentWorkspaceId,
      relative_path: relativePath,
    });
    stopReferenceAudioPreview();
    previewAudio = new Audio(result.data_url);
    await previewAudio.play();
  });
}

function validateThemeInputs() {
  const invalidInput = Array.from(fields.themeFields.querySelectorAll("[data-theme-field]"))
    .find((input) => !normalizeColorText(input.value, ""));
  if (!invalidInput) {
    return true;
  }
  syncThemeRole(invalidInput.dataset.themeField);
  switchPage("theme");
  invalidInput.focus();
  setError("请先修正无效的主题颜色，格式应为 #RRGGBB。");
  return false;
}

function validateExpressionInputs() {
  const rows = Array.from(fields.expressionList.querySelectorAll(".expression-row"));
  if (!rows.length) {
    switchPage("portrait");
    fields.addExpressionButton.focus();
    setError("请至少选择一张立绘。");
    return false;
  }
  if (!fields.expressionList.querySelector("[data-portrait-default]:checked")) {
    switchPage("portrait");
    rows[0].querySelector("[data-portrait-default]").focus();
    setError("请选择默认立绘。");
    return false;
  }
  const labels = new Set();
  rows.forEach((row) => {
    row.querySelectorAll("input").forEach((input) => input.classList.remove("is-invalid"));
  });
  for (const row of rows) {
    const labelInput = row.querySelector("[data-expression-label]");
    const pathInput = row.querySelector("[data-expression-path]");
    const label = labelInput.value.trim();
    const path = pathInput.value.trim();
    let message = "";
    let focusTarget = labelInput;
    if (!label && !path) {
      message = "请填写或删除空的表情立绘行。";
      labelInput.classList.add("is-invalid");
      pathInput.classList.add("is-invalid");
    } else if (!label) {
      message = "请填写表情标签。";
      labelInput.classList.add("is-invalid");
    } else if (!path) {
      message = `请填写表情「${label}」的图片路径。`;
      pathInput.classList.add("is-invalid");
      focusTarget = pathInput;
    } else if (labels.has(label)) {
      message = `表情标签重复：${label}`;
      labelInput.classList.add("is-invalid");
    }
    if (message) {
      switchPage("portrait");
      focusTarget.focus();
      setError(message);
      return false;
    }
    labels.add(label);
  }
  return true;
}

function validateVoiceInputs() {
  [
    fields.gptModelPath,
    fields.sovitsModelPath,
    ...fields.referenceAudioList.querySelectorAll("input"),
  ].forEach((input) => input.classList.remove("is-invalid"));
  if (!fields.voiceEnabled.checked) {
    return true;
  }
  const modelChecks = [
    [fields.gptModelPath, ".ckpt", "GPT 模型"],
    [fields.sovitsModelPath, ".pth", "SoVITS 模型"],
  ];
  for (const [input, extension, label] of modelChecks) {
    const value = input.value.trim().toLowerCase();
    if (value && !value.endsWith(extension)) {
      input.classList.add("is-invalid");
      switchPage("voice-model");
      input.focus();
      setError(`${label}必须是 ${extension} 文件。`);
      return false;
    }
  }
  const rows = Array.from(fields.referenceAudioList.querySelectorAll(".reference-audio-row"));
  if (!rows.length) {
    switchPage("reference-audio");
    fields.addReferenceAudioButton.focus();
    setError("启用语音后至少需要一条完整参考语音。");
    return false;
  }
  const audioPattern = /\.(wav|mp3|ogg|flac)$/i;
  for (const [index, row] of rows.entries()) {
    const inputs = [
      row.querySelector("[data-reference-audio-path]"),
      row.querySelector("[data-reference-lang]"),
      row.querySelector("[data-reference-text]"),
      row.querySelector("[data-reference-tone]"),
    ];
    const values = inputs.map((input) => input.value.trim());
    const emptyIndex = values.findIndex((value) => !value);
    if (emptyIndex >= 0) {
      inputs[emptyIndex].classList.add("is-invalid");
      switchPage("reference-audio");
      inputs[emptyIndex].focus();
      setError(`参考语音第 ${index + 1} 条必须填写音频、语言、参考文本和描述词。`);
      return false;
    }
    if (!audioPattern.test(values[0])) {
      inputs[0].classList.add("is-invalid");
      switchPage("reference-audio");
      inputs[0].focus();
      setError(`参考语音第 ${index + 1} 条文件格式不受支持。`);
      return false;
    }
    const pipeIndex = values.findIndex((value) => value.includes("|"));
    if (pipeIndex >= 0) {
      inputs[pipeIndex].classList.add("is-invalid");
      switchPage("reference-audio");
      inputs[pipeIndex].focus();
      setError(`参考语音第 ${index + 1} 条不能包含竖线字符。`);
      return false;
    }
  }
  return true;
}

async function saveWorkspaceDraft() {
  if (!currentDoc || !currentWorkspaceId) {
    setError("请先打开或新建角色。");
    return;
  }
  if (isPublishedCharacter()) {
    setError("已发布角色请使用“保存”。");
    return;
  }
  await runBusy(async () => {
    await flushDraftAutosave();
    notify(`角色「${currentDoc.display_name || currentDoc.id}」的草稿已保存。`, "success");
  });
}

async function commitCharacter({ publish = false } = {}) {
  if (!currentDoc || !currentWorkspaceId) {
    setError("请先打开或新建角色。");
    return;
  }
  const published = isPublishedCharacter();
  if (publish && published) {
    setError("该角色已经发布。");
    return;
  }
  if (!publish && !published) {
    setError("工作区角色请使用“发布角色”。");
    return;
  }
  if (!validateThemeInputs() || !validateExpressionInputs() || !validateVoiceInputs()) {
    return;
  }
  await runBusy(async () => {
    await flushDraftAutosave();
    const payload = await hostCall("studio.save_character", {
      workspace_id: currentWorkspaceId,
      current_character_id: request.initial_character_id || "",
      doc: collectDoc(),
    });
    if (Array.isArray(payload.characters)) {
      request.characters = payload.characters;
    }
    currentDoc = payload.doc || collectDoc();
    editingCharacterId = currentDoc.id || editingCharacterId;
    temporaryCharacter = null;
    renderCharacterOptions();
    renderEditor();
    markBaseline();
    notify(payload.message || (publish ? "角色已发布。" : "角色已保存。"), "success");
  });
}

async function savePublishedCharacter() {
  await commitCharacter({ publish: false });
}

async function publishCharacter() {
  await commitCharacter({ publish: true });
}

async function exportCharacter() {
  if (!currentDoc || !currentWorkspaceId) {
    setError("请先打开或新建角色。");
    return;
  }
  if (!validateThemeInputs() || !validateExpressionInputs() || !validateVoiceInputs()) {
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
    await flushDraftAutosave();
    const result = await hostCall("studio.export_archive", {
      workspace_id: currentWorkspaceId,
      path,
      include_voice: Boolean(collectDoc().voice),
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
  const voiceEnabled = hasDoc && fields.voiceEnabled.checked;
  const currentEntry = currentCharacterEntry();
  const published = isPublishedCharacter(currentEntry);
  const workspace = hasDoc && !published;
  fields.saveButton.hidden = !hasDoc || !published;
  fields.saveDraftButton.hidden = !workspace;
  fields.publishButton.hidden = !workspace;
  fields.saveButton.disabled = busy || !hasDoc || !published;
  fields.saveDraftButton.disabled = busy || !workspace;
  fields.publishButton.disabled = busy || !workspace;
  fields.exportButton.disabled = busy || !hasDoc;
  fields.discardDraftButton.textContent = published ? "放弃修改" : "删除草稿";
  fields.discardDraftButton.disabled = busy || !hasDoc || (
    published && !(isDirty() || currentEntry?.has_draft || currentEntry?.is_dirty)
  );
  fields.newCharacterButton.disabled = busy;
  fields.studioCharacterSelect.disabled = busy || fields.studioCharacterSelect.options.length === 0;
  refreshSelect(fields.studioCharacterSelect);
  fields.navItems.forEach((item) => {
    item.disabled = busy || !hasDoc;
  });
  [
    fields.characterId,
    fields.displayName,
    fields.initialMessage,
    fields.cardText,
    fields.addExpressionButton,
    fields.importPortraitFolderButton,
  ].forEach((element) => {
    element.disabled = busy || !hasDoc;
  });
  if (hasDoc && currentDoc.id) {
    fields.characterId.disabled = true;
  }
  fields.expressionList.querySelectorAll("input, button").forEach((element) => {
    element.disabled = busy || !hasDoc;
  });
  fields.voiceEnabled.disabled = busy || !hasDoc;
  fields.voiceModelFields.querySelectorAll("input, button").forEach((element) => {
    element.disabled = busy || !voiceEnabled;
  });
  fields.addReferenceAudioButton.disabled = busy || !voiceEnabled;
  fields.importReferenceAudioFolderButton.disabled = busy || !voiceEnabled;
  fields.referenceAudioList.querySelectorAll("input, button").forEach((element) => {
    element.disabled = busy || !voiceEnabled;
  });
  fields.clearGptModelButton.disabled = busy || !voiceEnabled || !fields.gptModelPath.value;
  fields.clearSovitsModelButton.disabled = busy || !voiceEnabled || !fields.sovitsModelPath.value;
  fields.themeFields.querySelectorAll("input, button").forEach((element) => {
    element.disabled = busy || !hasDoc;
  });
}

async function closeStudio() {
  stopReferenceAudioPreview();
  await flushDraftAutosave();
  if (currentWorkspaceId) {
    await hostCall("studio.release_workspace", { workspace_id: currentWorkspaceId });
  }
  await invoke("close_studio");
}

async function load() {
  request = await invoke("load_request");
  applyTheme(request.theme || request.theme_defaults || {});
  const characters = Array.isArray(request.characters) ? request.characters : [];
  const initialId = characters.some((item) => item.id === request.initial_character_id)
    ? request.initial_character_id
    : (characters[0]?.id || "");
  editingCharacterId = "";
  renderCharacterOptions();
  if (initialId) {
    await openCharacter(initialId);
  } else {
    renderEditor();
    refreshControls();
  }
}

fields.navItems.forEach((item) => item.addEventListener("click", () => switchPage(item.dataset.page)));
fields.studioCharacterSelect.addEventListener("change", (event) => selectCharacter(event.target.value));
fields.newCharacterButton.addEventListener("click", createCharacter);
fields.discardDraftButton.addEventListener("click", discardCurrentDraft);
fields.addExpressionButton.addEventListener("click", () => importPortrait());
fields.importPortraitFolderButton.addEventListener("click", importPortraitFolder);
fields.importGptModelButton.addEventListener("click", () => importVoiceModel("gpt"));
fields.importSovitsModelButton.addEventListener("click", () => importVoiceModel("sovits"));
fields.clearGptModelButton.addEventListener("click", () => {
  fields.gptModelPath.value = "";
  handleEditorChanged();
  refreshControls();
});
fields.clearSovitsModelButton.addEventListener("click", () => {
  fields.sovitsModelPath.value = "";
  handleEditorChanged();
  refreshControls();
});
fields.voiceEnabled.addEventListener("change", () => {
  if (!fields.voiceEnabled.checked) {
    const hasVoiceAssets = Boolean(
      fields.gptModelPath.value
      || fields.sovitsModelPath.value
      || fields.referenceAudioList.querySelector(".reference-audio-row")
    );
    if (hasVoiceAssets && !window.confirm("关闭语音后，保存时会移除语音配置和参考语音映射。是否继续？")) {
      fields.voiceEnabled.checked = true;
    }
  }
  syncVoiceEnabledState();
  handleEditorChanged();
  refreshControls();
});
fields.addReferenceAudioButton.addEventListener("click", () => importReferenceAudio());
fields.importReferenceAudioFolderButton.addEventListener("click", importReferenceAudioFolder);
fields.saveDraftButton.addEventListener("click", saveWorkspaceDraft);
fields.publishButton.addEventListener("click", publishCharacter);
fields.saveButton.addEventListener("click", savePublishedCharacter);
fields.exportButton.addEventListener("click", exportCharacter);
fields.cancelButton.addEventListener("click", closeStudio);
[
  fields.characterId,
  fields.displayName,
  fields.initialMessage,
  fields.cardText,
  fields.defaultRefLang,
  fields.textLang,
].forEach((element) => element.addEventListener("input", handleEditorChanged));

window.__TAURI__?.event?.listen?.("sakura://studio-close-requested", closeStudio);
enhanceSelect(fields.studioCharacterSelect);

async function startStudio() {
  try {
    await load();
  } catch (error) {
    setError(String(error));
  }
  await invoke("show_studio");
}

startStudio().catch((error) => setError(String(error)));
