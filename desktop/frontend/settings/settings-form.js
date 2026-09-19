// Host-owned settings rows, shared by page contributions and plugin settings.
export function createSettingsForm({ document, plugin, section, read, write, enhanceSelect, renderAction, renderCollection, renderDisplay, refreshSelect = () => {} }) {
  const root = document.createElement("div");
  root.dataset.pluginSection = section.section_id; root.className = "settings-form";
  const inputs = new Map();
  let advanced = null;
  const appendRow = (field, row) => {
    if (field.placement !== "advanced") { root.append(row); return; }
    if (!advanced) {
      advanced = document.createElement("details"); advanced.className = "settings-group advanced-section";
      const summary = document.createElement("summary"); summary.textContent = "高级设置"; advanced.append(summary); root.append(advanced);
    }
    advanced.append(row);
  };
  for (const field of section.fields || []) {
    if (field.type === "data") continue;
    const row = document.createElement("div"); row.className = "setting-row";
    const labelBox = document.createElement("div"); labelBox.className = "setting-row-text";
    const label = document.createElement("label"); label.className = "setting-title"; label.textContent = field.label;
    const id = `setting-${plugin.id}-${section.section_id}-${field.key}`; label.htmlFor = id;
    labelBox.append(label);
    if (field.description) {
      if (field.type === "boolean") {
        const desc = document.createElement("span"); desc.className = "setting-desc"; desc.textContent = field.description; labelBox.append(desc);
      } else {
        labelBox.classList.add("setting-row-help");
        const help = document.createElement("button"); help.type = "button"; help.className = "setting-help";
        help.textContent = "?"; help.dataset.tooltip = field.description; help.setAttribute("aria-label", `${field.label}说明`); labelBox.append(help);
      }
    }
    if (["status", "resource", "readonly"].includes(field.type)) {
      let display = renderDisplay(field);
      row.append(labelBox, display); appendRow(field, row);
      inputs.set(field.key, { row, field, updateDisplay() { const next = renderDisplay(field); display.replaceWith(next); display = next; } });
      continue;
    }
    const input = document.createElement(field.type === "select" ? "select" : "input"); input.id = id;
    input.dataset.pluginField = field.key;
    if (field.tooltip) input.dataset.tooltip = field.tooltip;
    if (field.type === "select") {
      for (const option of field.options || []) {
        const element = document.createElement("option"); element.value = String(option.value); element.textContent = option.label; input.append(element);
      }
    } else input.type = field.type === "boolean" ? "checkbox" : ["number", "integer"].includes(field.type) ? "number" : field.type === "password" ? "password" : "text";
    for (const [attr, key] of [["min", "minimum"], ["max", "maximum"], ["step", "step"], ["maxLength", "maxLength"]]) {
      if (field[key] != null) input[attr] = String(field[key]);
    }
    if (field.type === "integer") input.step = "1";
    input.placeholder = field.placeholder || "";
    input.required = Boolean(field.required); input.disabled = Boolean(field.readonly);
    const value = read(field.key);
    if (field.type === "boolean") input.checked = Boolean(value);
    else input.value = String(value ?? field.displayDefault ?? "");
    input.addEventListener(field.type === "boolean" || field.type === "select" ? "change" : "input", () => {
      const next = field.type === "boolean" ? input.checked : ["integer", "number"].includes(field.type)
        ? input.value === "" ? null : Number(input.value) : field.type === "select"
          ? field.options.find(o => String(o.value) === input.value)?.value : input.value;
      write(field.key, next); syncConditions();
    });
    let control = input;
    let toggle = null;
    if (field.optionalToggle) {
      control = document.createElement("label"); control.className = "inline-input";
      const check = document.createElement("input"); toggle = check; check.type = "checkbox"; check.checked = value != null;
      check.disabled = Boolean(field.readonly);
      const text = document.createElement("span"); text.textContent = "自定义";
      input.disabled = !check.checked || Boolean(field.readonly);
      check.addEventListener("change", () => {
        input.disabled = !check.checked || Boolean(field.readonly);
        if (check.checked && input.value === "") input.value = String(field.displayDefault ?? field.minimum ?? 1);
        write(field.key, check.checked ? Number(input.value) : null);
      });
      control.append(check, text, input);
    } else if (field.type === "boolean" || field.unit) {
      control = document.createElement("span"); control.className = field.type === "boolean" ? "setting-toggle" : "field-unit";
      control.append(input);
      if (field.unit) { const unit = document.createElement("span"); unit.className = "unit"; unit.textContent = field.unit; control.append(unit); }
    }
    row.append(labelBox, control); appendRow(field, row); inputs.set(field.key, { row, input, field, toggle });
  }
  function syncConditions() {
    for (const { row, input, field, toggle } of inputs.values()) {
      if (!input || !field.enabledWhen) continue;
      const available = String(read(field.enabledWhen.field)) === field.enabledWhen.equals;
      input.disabled = !available || Boolean(field.readonly) || (toggle && !toggle.checked);
      if (toggle) toggle.disabled = !available || Boolean(field.readonly); row.hidden = !available && field.enabledWhen.hide === true;
    }
  }
  syncConditions();
  const embedded = new Set(section.fields.flatMap(field => field.actionIds || []));
  const actions = (section.actions || []).filter(action => !embedded.has(action.action_id)).map(action => ({ action, node: renderAction(action) }));
  const collections = (section.collections || []).map(collection => ({ collection, node: renderCollection(collection) }));
  for (const entry of [...actions, ...collections]) root.append(entry.node);
  return { element: root,
    update() {
      for (const { input, field, toggle, updateDisplay } of inputs.values()) {
        if (updateDisplay) { updateDisplay(); continue; }
        if (document.activeElement === input) continue;
        const value = read(field.key);
        if (field.type === "boolean") input.checked = Boolean(value);
        else input.value = String(value ?? field.displayDefault ?? "");
        if (field.type === "select") refreshSelect(input);
        if (toggle) { toggle.checked = value != null; input.disabled = value == null || Boolean(field.readonly); }
      }
      syncConditions();
      for (const entry of actions) { const node = renderAction(entry.action); entry.node.replaceWith(node); entry.node = node; }
      for (const entry of collections) { const node = renderCollection(entry.collection); entry.node.replaceWith(node); entry.node = node; }
    },
    mounted() { root.querySelectorAll("select").forEach(enhanceSelect); }, dispose() { root.remove(); } };
}
