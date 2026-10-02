// Host-rendered records. The plugin owns data, permissions and device semantics.
export function createRecordTable({ document, read, write, presentation, inspect, inspectLabel,
  onError, enhanceSelect, refreshSelect, closeSelects }) {
  const node = (tag, className = "", text = "") => {
    const element = document.createElement(tag); element.className = className; element.textContent = text; return element;
  };
  const root = node("div", "record-table");
  const search = node("input", "admin-search"); search.type = "text"; search.placeholder = "搜索名称、房间或别名";
  search.setAttribute("aria-label", "搜索记录"); root.append(search);
  if (presentation.note) root.append(node("p", "record-table-note", presentation.note));
  const content = node("div", "record-workbench");
  const list = node("div", "record-list"); list.setAttribute("aria-label", "记录列表");
  const editor = node("section", "record-editor"); content.append(list, editor); root.append(content);
  let selectedId = "";
  let signature = "", disposed = false, pendingId = null, details = null, generation = 0;
  const controls = new Map();
  const currentItems = () => read(presentation.itemsField) || [];
  const currentValues = () => read(presentation.valueField) || {};
  function closeDetails() { details?.remove(); details = null; }
  function showDetails(result) {
    closeDetails();
    details = node("section", "record-details"); details.setAttribute("role", "region"); details.setAttribute("aria-label", result.title);
    const heading = node("div", "record-details-heading"); heading.append(node("h3", "", result.title));
    const close = node("button", "secondary-button", "关闭状态"); close.type = "button"; close.addEventListener("click", closeDetails); heading.append(close); details.append(heading);
    if (result.message) details.append(node("p", "hint", result.message));
    const renderRows = (target, rows) => {
      let group = null, list;
      for (const row of rows) {
        if (!list || (row.group || "") !== group) {
          group = row.group || "";
          if (group) target.append(node("h4", "", group));
          list = node("dl", "record-state-values"); target.append(list);
        }
        list.append(node("dt", "", row.label), node("dd", "", row.value));
      }
    };
    renderRows(details, (result.rows || []).filter(row => !row.secondary));
    const secondary = (result.rows || []).filter(row => row.secondary);
    if (secondary.length) {
      const extra = node("details", "record-secondary");
      extra.append(node("summary", "", `其他状态（${secondary.length}）`)); renderRows(extra, secondary); details.append(extra);
    }
    editor.append(details); details.scrollIntoView({ block: "nearest" });
  }
  async function inspectItem(item, button) {
    if (pendingId !== null) return;
    pendingId = item.id; button.disabled = true; button.textContent = "正在读取…";
    editor.querySelectorAll("button").forEach(control => { control.disabled = true; });
    const ticket = generation;
    try {
      const result = await inspect(item.id, () => !disposed && ticket === generation);
      if (!disposed && ticket === generation && currentItems().some(record => record.id === item.id) && result?.id === item.id) showDetails(result);
    } catch (error) { if (!disposed && ticket === generation) onError(error); }
    finally {
      pendingId = null;
      if (!disposed) {
        editor.querySelectorAll("button").forEach(control => { control.disabled = false; });
        button.textContent = inspectLabel;
      }
    }
  }
  function render() {
    closeSelects(editor); controls.clear(); list.replaceChildren(); editor.replaceChildren(); closeDetails();
    search.hidden = currentItems().length === 0;
    const note = root.querySelector(".record-table-note");
    if (note) note.hidden = currentItems().length === 0;
    const query = search.value.trim().toLocaleLowerCase();
    const values = currentValues();
    const items = currentItems().filter(item => [item.label, item.description, ...Object.values(values[item.id] || {})].join(" ").toLocaleLowerCase().includes(query));
    if (!items.some(item => item.id === selectedId)) selectedId = items[0]?.id || "";
    if (!items.length) {
      list.append(node("p", "empty-state", query ? "没有匹配的记录" : "暂无数据")); editor.hidden = true; return;
    }
    editor.hidden = false;
    for (const item of items) {
      const button = node("button", "record-choice"); button.type = "button";
      button.classList.toggle("is-selected", item.id === selectedId); button.setAttribute("aria-pressed", String(item.id === selectedId));
      const heading = node("span", "record-choice-heading"); heading.append(node("strong", "", item.label));
      if (item.status) heading.append(node("span", `record-availability is-${item.status.state}`, item.status.label));
      button.append(heading);
      if (item.description) button.append(node("span", "record-cell-description", item.description));
      button.addEventListener("click", () => { if (selectedId === item.id) return; selectedId = item.id; generation += 1; render(); });
      list.append(button);
    }
    const item = items.find(item => item.id === selectedId);
    editor.append(node("h4", "record-editor-title", item.label));
    if (item.description) editor.append(node("p", "record-editor-subtitle", item.description));
    for (const column of presentation.columns.filter((column, index) => !column.readonly || index > 0)) {
      const row = node("label", "record-field"); row.append(node("span", "", column.label));
      if (column.readonly) { row.append(node("output", "", item.values?.[column.key] ?? "")); editor.append(row); continue; }
      const input = node(column.type === "select" ? "select" : "input");
      input.setAttribute("aria-label", `${item.label} · ${column.label}`);
      if (column.type === "select") {
        for (const option of column.options) { const opt = node("option", "", option.label); opt.value = String(option.value); input.append(opt); }
      } else { input.type = column.type === "boolean" ? "checkbox" : "text"; if (column.maxLength != null) input.maxLength = column.maxLength; }
      const value = values[item.id]?.[column.key] ?? column.default;
      if (column.type === "boolean") input.checked = Boolean(value); else input.value = String(value ?? "");
      input.addEventListener(column.type === "string" ? "input" : "change", () => {
        const next = structuredClone(currentValues());
        next[item.id] = { ...(next[item.id] || {}), [column.key]: column.type === "boolean" ? input.checked
          : column.type === "select" ? column.options.find(option => String(option.value) === input.value)?.value : input.value };
        write(presentation.valueField, next);
      });
      row.append(input); editor.append(row); controls.set(column.key, { input, itemId: item.id, column });
    }
    if (inspect) {
      const button = node("button", "secondary-button record-inspect", inspectLabel); button.type = "button";
      button.disabled = pendingId !== null; button.addEventListener("click", () => inspectItem(item, button)); editor.append(button);
    }
    editor.querySelectorAll("select").forEach(enhanceSelect);
  }
  function update() {
    const items = currentItems();
    const next = JSON.stringify(items);
    if (next !== signature) {
      signature = next; generation += 1; closeDetails(); render(); return;
    }
    const values = currentValues();
    for (const { input, itemId, column } of controls.values()) {
      if (document.activeElement === input) continue;
      const value = values[itemId]?.[column.key] ?? column.default;
      if (column.type === "boolean") input.checked = Boolean(value); else input.value = String(value ?? "");
      if (column.type === "select") refreshSelect(input);
    }
  }
  search.addEventListener("input", () => { generation += 1; render(); });
  update();
  return { element: root, update, dispose() { disposed = true; generation += 1; closeSelects(root); closeDetails(); root.remove(); } };
}
