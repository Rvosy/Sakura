const invoke = window.__TAURI__.core.invoke;

const fields = {
  pageTitle: document.getElementById("pageTitle"),
  pageSubtitle: document.getElementById("pageSubtitle"),
  navItems: Array.from(document.querySelectorAll(".nav-item[data-page]")),
  pages: {
    basic: document.getElementById("page-basic"),
    card: document.getElementById("page-card"),
    portrait: document.getElementById("page-portrait"),
    theme: document.getElementById("page-theme"),
  },
  studioCharacterSelect: document.getElementById("studioCharacterSelect"),
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
  basic: { title: "基础信息", subtitle: "名称、开场白与语音状态" },
  card: { title: "人设卡", subtitle: "系统人设与回复语气" },
  portrait: { title: "立绘", subtitle: "默认立绘与表情映射" },
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
let currentDoc = null;
let baseline = "";
let busy = false;
let editingCharacterId = "";
let temporaryCharacter = null;
let activeThemeField = "";
let themeEditor = {};

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

function enhanceSelect(select) {
  if (!select || select.__customSelect) {
    return;
  }
  const wrapper = document.createElement("div");
  wrapper.className = "custom-select";
  const trigger = document.createElement("button");
  trigger.type = "button";
  trigger.className = "custom-select__trigger";
  const label = document.createElement("span");
  label.className = "custom-select__label";
  const caret = document.createElement("span");
  caret.className = "custom-select__caret";
  caret.setAttribute("aria-hidden", "true");
  trigger.append(label, caret);
  const menu = document.createElement("div");
  menu.className = "custom-select__menu";
  menu.setAttribute("role", "listbox");

  select.parentNode.insertBefore(wrapper, select);
  wrapper.append(trigger, select);

  function syncTrigger() {
    const option = select.options[select.selectedIndex];
    label.textContent = option ? option.textContent : "";
    trigger.disabled = select.disabled;
  }

  function buildMenu() {
    menu.textContent = "";
    Array.from(select.options).forEach((option) => {
      const item = document.createElement("div");
      item.className = "custom-select__option";
      item.setAttribute("role", "option");
      item.textContent = option.textContent;
      if (option.value === select.value) {
        item.classList.add("is-selected");
        item.setAttribute("aria-selected", "true");
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
      });
      menu.append(item);
    });
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

  function openMenu() {
    if (select.disabled) {
      return;
    }
    buildMenu();
    document.body.append(menu);
    menu.classList.add("is-open");
    positionMenu();
    wrapper.classList.add("is-open");
    document.addEventListener("pointerdown", onDocumentPointer, true);
    document.addEventListener("keydown", onKeydown, true);
    window.addEventListener("scroll", closeMenu, true);
    window.addEventListener("resize", closeMenu, true);
  }

  function closeMenu() {
    wrapper.classList.remove("is-open");
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
  select.__customSelect = { refresh: syncTrigger };
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

function isDirty() {
  return Boolean(currentDoc) && editorSnapshot() !== baseline;
}

function confirmDiscardChanges() {
  return !isDirty() || window.confirm("当前修改尚未保存，继续操作将丢失这些修改。是否继续？");
}

function characterOptionLabel(character) {
  const label = character.display_name || character.id;
  return character.source === "draft" ? `${label}（新建）` : label;
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
  characterOptions().forEach((character) => {
    const option = document.createElement("option");
    option.value = character.id;
    option.textContent = characterOptionLabel(character);
    fields.studioCharacterSelect.append(option);
  });
  fields.studioCharacterSelect.value = editingCharacterId;
  refreshSelect(fields.studioCharacterSelect);
}

function collectDoc() {
  const theme = { ...(currentDoc?.theme || {}) };
  fields.themeFields.querySelectorAll("[data-theme-field]").forEach((input) => {
    theme[input.dataset.themeField] = input.value.trim();
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
  fields.saveButton.classList.toggle("has-changes", Boolean(dirty));
}

function setCurrentDoc(payload, draftCharacter = null, options = {}) {
  currentPackageDir = payload.package_dir || "";
  currentDoc = payload.doc || null;
  if (Array.isArray(payload.characters)) {
    request.characters = payload.characters;
  }
  editingCharacterId = currentDoc?.id || "";
  temporaryCharacter = draftCharacter;
  renderCharacterOptions();
  renderEditor();
  switchPage("basic");
  if (options.dirty === true) {
    baseline = "";
    refreshDirty();
  } else {
    markBaseline();
  }
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
  const theme = {
    ...(request.theme_defaults || request.theme || {}),
    ...(doc.theme || {}),
  };
  applyTheme(theme);
  renderTheme(theme);
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
      refreshDirty();
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
  refreshDirty();
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
  if (!confirmDiscardChanges()) {
    fields.studioCharacterSelect.value = previousId;
    refreshSelect(fields.studioCharacterSelect);
    return;
  }
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
}

async function createCharacter() {
  if (!confirmDiscardChanges()) {
    return;
  }
  const id = window.prompt("角色 ID：", "");
  if (!id) {
    return;
  }
  const characterId = id.trim();
  if ((request.characters || []).some((character) => character.id === characterId)) {
    setError(`角色 ID 已存在：${characterId}。请从下拉菜单直接打开该角色。`);
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
    }, { dirty: true });
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

async function saveCharacter() {
  if (!currentDoc || !currentPackageDir) {
    setError("请先打开或新建角色。");
    return;
  }
  if (!validateThemeInputs()) {
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
    }
    currentDoc = payload.doc || collectDoc();
    editingCharacterId = currentDoc.id || editingCharacterId;
    temporaryCharacter = null;
    renderCharacterOptions();
    renderEditor();
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
    fields.replyToneInput,
    fields.defaultPortrait,
    fields.addExpressionButton,
  ].forEach((element) => {
    element.disabled = busy || !hasDoc;
  });
  if (hasDoc && currentDoc.id) {
    fields.characterId.disabled = true;
  }
  fields.expressionList.querySelectorAll("input, button").forEach((element) => {
    element.disabled = busy || !hasDoc;
  });
  fields.themeFields.querySelectorAll("input, button").forEach((element) => {
    element.disabled = busy || !hasDoc;
  });
}

async function closeStudio() {
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
enhanceSelect(fields.studioCharacterSelect);
load().catch((error) => setError(String(error)));
